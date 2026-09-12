#!/usr/bin/env python3
"""AeroVLA live viewer: BottomCamera + LiDAR only.

Shows a live tkinter window with:
  * BOTTOM     - dedicated straight-down view (BottomCamera, hi-res).
  * LiDAR      - live 2D top-down projection of the Lidar1 point cloud,
                 colored by height (blue=low ... red=high), auto-scaled.

Fetches are parallelized: each RPC consumer gets its OWN AirSim client and runs
in a thread-pool worker, so a slow/stalled camera never blocks the others and
the window refreshes as fast as the sim returns data.

Usage:
    python scripts/multiview.py [SCENE_PORT] [--no-lidar]

Connects to the LOCAL AirSim API port (default 30001) as read-only clients.
Requires tkinter + Pillow + numpy + airsim (local `aero_vla` conda env).
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageTk

import airsim

BOTTOM_CAM = "BottomCamera"
LIDAR_NAME = "Lidar1"
TICK_MS = 40


def _png_to_ndarray(b):
    """Decode a PNG byte string (simGetImage) into an RGB ndarray, or None."""
    if not b:
        return None
    try:
        from io import BytesIO
        from PIL import Image as _PILImage
        return np.asarray(_PILImage.open(BytesIO(b)).convert("RGB"))
    except Exception:
        return None


def _make_client(port):
    c = airsim.MultirotorClient(ip="127.0.0.1", port=port, timeout_value=10)
    c.confirmConnection()
    return c


def _get_scene(client, cam):
    return _png_to_ndarray(client.simGetImage(cam, airsim.ImageType.Scene))


class WorkerPool:
    """A set of dedicated AirSim clients, one per worker, for parallel RPC."""

    def __init__(self, port, n=8):
        self.port = port
        self.n = n
        self.clients = []
        self._faulty = set()
        self.pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="multiview")
        self._connect_all()

    def _connect_all(self):
        for _ in range(self.n):
            try:
                self.clients.append(_make_client(self.port))
            except Exception as e:
                self.clients.append(None)
                print(f"multiview: client connect failed: {type(e).__name__}: {e}", flush=True)

    def _get(self, i):
        if i in self._faulty or i >= len(self.clients):
            return None
        c = self.clients[i]
        if c is None:
            return None
        return c

    def submit(self, fn, *args, worker=None):
        return self.pool.submit(self._run, worker or 0, fn, *args)

    def _run(self, i, fn, *args):
        c = self._get(i)
        if c is None:
            return None
        try:
            return fn(c, *args)
        except Exception as e:
            if "Reconnect" in str(e) or "timed out" in str(e).lower():
                self._faulty.add(i)
            return None


def fetch_lidar(client):
    try:
        data = client.getLidarData(lidar_name=LIDAR_NAME)
        n = len(data.point_cloud) // 3
        if n == 0:
            return None
        return np.array(data.point_cloud, dtype=np.float64).reshape(-1, 3)
    except Exception:
        return None


def lidar_to_image(pc, size=420):
    """Top-down projection of the point cloud (X forward), colored by height."""
    img = Image.new("RGB", (size, size), (8, 8, 12))
    draw = ImageDraw.Draw(img)
    if pc is None or len(pc) == 0:
        draw.text((10, 10), "lidar: no points", fill=(255, 255, 0))
        return img
    xs = pc[:, 0]
    ys = pc[:, 1]
    zs = pc[:, 2]
    cxs = 2 * size / 3
    cx = (xs.max() + xs.min()) / 2 if xs.size else 0
    cy = (ys.max() + ys.min()) / 2 if ys.size else 0
    dx = max(xs.max() - xs.min(), 1e-6)
    dy = max(ys.max() - ys.min(), 1e-6)
    s = min(cxs / dx, cxs / dy)
    zmin, zmax = zs.min(), zs.max()
    zr = max(zmax - zmin, 1e-6)

    def h2rgb(z):
        t = (z - zmin) / zr
        if t < 0.25:
            return (0, int(255 * t / 0.25), 255)
        if t < 0.5:
            return (0, 255, int(255 * (1 - (t - 0.25) / 0.25)))
        if t < 0.75:
            return (int(255 * (t - 0.5) / 0.25), 255, 0)
        return (255, int(255 * (1 - (t - 0.75) / 0.25)), 0)

    step = max(1, len(pc) // 8000)
    for i in range(0, len(pc), step):
        x, y, z = pc[i]
        px = size // 2 + int((x - cx) * s)
        py = size // 2 - int((y - cy) * s)
        if 0 <= px < size and 0 <= py < size:
            draw.point((px, py), fill=h2rgb(z))
    draw.text((6, 6), f"lidar: {len(pc)} pts", fill=(255, 255, 255))
    return img


class MultiView:
    def __init__(self, port, show_lidar=True):
        import tkinter as tk
        self.tk = tk
        self.port = port
        self.show_lidar = show_lidar
        self.pool = WorkerPool(port)
        self.root = tk.Tk()
        self.root.title(f"AeroVLA  (AirSim :{port})")
        self.bottom_lbl = tk.Label(self.root, text="bottom: n/a", borderwidth=2, relief="groove")
        self.bottom_lbl.grid(row=0, column=0, padx=4, pady=4)
        self.lidar_lbl = tk.Label(self.root, text="lidar: n/a", borderwidth=2, relief="groove")
        self.lidar_lbl.grid(row=0, column=1, padx=4, pady=4)
        self.n = 0
        self._schedule()
        self.root.mainloop()

    def _schedule(self):
        try:
            self._frame()
        except Exception as e:
            print(f"multiview: error {type(e).__name__}: {e}", flush=True)
        self.root.after(TICK_MS, self._schedule)

    def _frame(self):
        workers = self.pool
        futs = {}
        futs["bottom"] = workers.submit(_get_scene, BOTTOM_CAM, worker=0)
        if self.show_lidar:
            futs["lidar"] = workers.submit(fetch_lidar, worker=1)

        results = {k: f.result(timeout=TICK_MS * 8) for k, f in futs.items()}

        bt = results.get("bottom")
        if bt is not None:
            im = Image.fromarray(bt).resize((420, 420), Image.BICUBIC)
            draw = ImageDraw.Draw(im)
            draw.text((8, 6), "BOTTOM", fill=(255, 255, 0))
            ph = ImageTk.PhotoImage(im)
            self.bottom_lbl.configure(image=ph)
            self.bottom_lbl.image = ph
        else:
            self.bottom_lbl.configure(text="bottom: no image")

        if self.show_lidar:
            pc = results.get("lidar")
            img = lidar_to_image(pc)
            ph = ImageTk.PhotoImage(img)
            self.lidar_lbl.configure(image=ph)
            self.lidar_lbl.image = ph

        self.n += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=30001, help="AirSim API port (scene port)")
    ap.add_argument("--no-lidar", action="store_true", help="disable LiDAR panel")
    ap.add_argument("--retry", type=int, default=60, help="seconds to retry connect if scene not up")
    args = ap.parse_args()

    deadline = time.time() + args.retry
    while True:
        try:
            MultiView(args.port, show_lidar=not args.no_lidar)
            return
        except Exception as e:
            if time.time() >= deadline:
                print(f"multiview fatal: {type(e).__name__}: {e}")
                sys.exit(1)
            print(f"multiview: window not ready ({type(e).__name__}: {e}); retrying...")
            time.sleep(2)


if __name__ == "__main__":
    main()
