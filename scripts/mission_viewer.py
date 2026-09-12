#!/usr/bin/env python3
"""AeroVLA mission console - OPTION A: one window = target + views + takeover.

Shows a single tkinter window with:
  * TARGET   - the object to find THIS episode (desc text + asset name + target
               pos), polled from the H100 beacon file /tmp/aerovla_target.json.
  * FRONT    - the drone's own front/chase view (what the model sees).
  * BOTTOM   - straight-down view (BottomCamera).
  * LIDAR    - live top-down projection of Lidar1, colored by height, with a
               cross-hair marker at the target's x/y when known.
  * MODE     - AUTO (autopilot drives; the eval loop runs) / MANUAL (you drive,
               the H100 eval loop pauses and awaits hand-back). Toggle with M.

In MANUAL mode this window becomes the flyer (same motor-control flight logic
as drone_keyboard.py): WASD/arrows move, R/F up/down, Q/E yaw, +/- speed, P
pause, M toggle mode. It writes /tmp/aerovla_manual on the H100 (via the reverse
tunnel, or the shared flag forwarded by split.sh) so the eval loop pauses.

Connects to LOCAL AirSim API (default :30001) as independent read-only/fly
clients. Requires tkinter + Pillow + numpy + airsim (local aero_vla conda env).

Usage:
    python scripts/mission_viewer.py [SCENE_PORT]
"""
import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageTk

import airsim

BOTTOM_CAM = "BottomCamera"
FRONT_CAM = "FrontCamera"
LIDAR_NAME = "Lidar1"
TICK_MS = 40

TARGET_BEACON_LOCAL = "/tmp/aerovla_target.json"
BEACON_REMOTE_PATH = "/tmp/aerovla_target.json"
MANUAL_FLAG_LOCAL = "/tmp/aerovla_manual"
H100 = "main.copper.sanati-emp.coder"

KNOWN_VIEW_CAMS = {"FrontCamera": "FRONT", "BottomCamera": "BOTTOM"}


def _png_to_ndarray(b):
    if not b:
        return None
    try:
        from io import BytesIO
        return np.asarray(Image.open(BytesIO(b)).convert("RGB"))
    except Exception:
        return None


def _make_client(port):
    c = airsim.MultirotorClient(ip="127.0.0.1", port=port, timeout_value=10)
    c.confirmConnection()
    return c


def _get_scene(client, cam):
    return _png_to_ndarray(client.simGetImage(cam, airsim.ImageType.Scene))


class WorkerPool:
    def __init__(self, port, n=8):
        self.clients = []
        self._faulty = set()
        self.pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="mission")
        self.port = port
        for _ in range(n):
            try:
                self.clients.append(_make_client(port))
            except Exception as e:
                self.clients.append(None)
                print(f"mission: client connect failed: {type(e).__name__}: {e}", flush=True)

    def _get(self, i):
        if i in self._faulty or i >= len(self.clients):
            return None
        c = self.clients[i]
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


def load_target():
    """Read the per-episode target beacon. Checks local file first, then ssh H100."""
    if os.path.exists(TARGET_BEACON_LOCAL):
        try:
            with open(TARGET_BEACON_LOCAL) as f:
                return json.load(f)
        except Exception:
            pass
    # Fallback: poll the H100 file directly over ssh (small, infrequent)
    try:
        out = __import__("subprocess").run(
            ["ssh", "-o", "ConnectTimeout=6", "-o", "LogLevel=ERROR", H100,
             f"cat {BEACON_REMOTE_PATH} 2>/dev/null"],
            capture_output=True, text=True, timeout=12,
        ).stdout
        if out:
            return json.loads(out)
    except Exception:
        return None
    return None


class MissionViewer:
    def __init__(self, port=30001):
        import tkinter as tk
        self.tk = tk
        self.port = port
        self.speed = 5.0
        self.yaw_speed = 60.0
        self.keys = set()
        self.manual = False
        self.beacon_time = 0
        self.target = None
        self.client = airsim.MultirotorClient(ip="127.0.0.1", port=port, timeout_value=10)
        self.client.confirmConnection()
        self.pool = WorkerPool(port)

        self.root = tk.Tk()
        self.root.title(f"AeroVLA mission console  (AirSim :{port})")

        top = tk.Frame(self.root)
        top.pack(fill="x")
        self.target_lbl = tk.Label(top, text="target: poll...", justify="left",
                                   anchor="w", wraplength=700, font=("Monospace", 10))
        self.target_lbl.pack(side="left", padx=6, pady=4, fill="x", expand=True)
        self.mode_lbl = tk.Label(top, text="[AUTO]", font=("Monospace", 12, "bold"),
                                 fg="lime", bg="#002200")
        self.mode_lbl.pack(side="right", padx=8)

        main = tk.Frame(self.root)
        main.pack(fill="both", expand=True)
        self.front_lbl = tk.Label(main, text="front: n/a", borderwidth=2, relief="groove")
        self.front_lbl.grid(row=0, column=0, rowspan=2, padx=4, pady=4)
        self.lidar_lbl = tk.Label(main, text="lidar: n/a", borderwidth=2, relief="groove")
        self.lidar_lbl.grid(row=0, column=1, padx=4, pady=4)
        self.bottom_lbl = tk.Label(main, text="bottom: n/a", borderwidth=2, relief="groove")
        self.bottom_lbl.grid(row=1, column=1, padx=4, pady=4)

        self.tel_lbl = tk.Label(self.root, text="telemetry...", justify="left", anchor="w",
                                font=("Monospace", 10))
        self.tel_lbl.pack(fill="x", padx=6, pady=2)

        for k in ("<KeyPress>", "<KeyRelease>"):
            self.root.bind(k, self._on_key)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.mainloop()

    # ---- key handling (manual flight + mode toggle) ----
    def _on_key(self, e):
        if e.keysym in ("Escape", "Return"):
            if e.keysym == "Escape":
                self._quit()
            return
        sym = e.keysym.lower()
        if sym == "m":
            if e.type == "2":  # KeyPress only (toggle once)
                self._toggle_manual()
            return
        if e.type == "2":
            self.keys.add(sym)
        else:
            self.keys.discard(sym)

    def _toggle_manual(self):
        self.manual = not self.manual
        self._write_manual_flag()
        self._update_mode_label()

    def _write_manual_flag(self):
        # local copy (viewer reads it) + push to H100 so eval loop sees it
        try:
            with open(MANUAL_FLAG_LOCAL, "w") as f:
                f.write('{"manual": %s, "at": %f}\n' % (str(self.manual).lower(), time.time()))
        except Exception:
            pass
        try:
            __import__("subprocess").run(
                ["ssh", "-o", "ConnectTimeout=6", "-o", "LogLevel=ERROR", H100,
                 "printf '{\"manual\": %s}\\n' > /tmp/aerovla_manual.json" % str(self.manual).lower()],
                capture_output=True, timeout=12,
            )
        except Exception:
            pass

    def _update_mode_label(self):
        self.mode_lbl.configure(
            text="[MANUAL]" if self.manual else "[AUTO]",
            fg="red" if self.manual else "lime",
            bg="#330000" if self.manual else "#002200",
        )

    # ---- target beacon polling ----
    def _pump_target(self):
        if time.time() - self.beacon_time > 2.0:
            self.beacon_time = time.time()
            t = load_target()
            if t:
                self.target = t
                self._draw_target_label()
        self.root.after(2000, self._pump_target)

    def _draw_target_label(self):
        t = self.target or {}
        desc = t.get("object_desc", "")
        asset = t.get("asset_name", "")
        pos = t.get("object_position", [])
        pos_s = ""
        if pos and isinstance(pos[0], (list, tuple)):
            x, y, z = pos[0][:3]
            pos_s = f"target pos: ({x:.1f}, {y:.1f}, {z:.1f})"
        self.target_lbl.configure(
            text=f"TARGET: {desc}  [{asset}]   {pos_s}"
        )

    # ---- main loop ----
    def _schedule(self):
        try:
            if self.manual:
                self._apply_manual()
            self._frame()
        except Exception as e:
            print(f"mission: error {type(e).__name__}: {e}", flush=True)
        self.root.after(TICK_MS, self._schedule)

    def _frame(self):
        workers = self.pool
        futs = {
            "front": workers.submit(_get_scene, FRONT_CAM, worker=0),
            "bottom": workers.submit(_get_scene, BOTTOM_CAM, worker=1),
            "lidar": workers.submit(fetch_lidar, worker=2),
        }
        results = {k: f.result(timeout=TICK_MS * 8) for k, f in futs.items()}

        fv = results.get("front")
        if fv is not None:
            im = Image.fromarray(fv).resize((640, 480), Image.BICUBIC)
            draw = ImageDraw.Draw(im)
            draw.rectangle((0, 0, im.width, 30), fill=(0, 0, 0))
            draw.text((8, 6), "FRONT", fill=(255, 255, 0))
            ph = ImageTk.PhotoImage(im)
            self.front_lbl.configure(image=ph)
            self.front_lbl.image = ph
        else:
            self.front_lbl.configure(text="front: no image")

        bv = results.get("bottom")
        if bv is not None:
            im = Image.fromarray(bv).resize((420, 420), Image.BICUBIC)
            draw = ImageDraw.Draw(im)
            draw.text((8, 6), "BOTTOM", fill=(255, 255, 0))
            ph = ImageTk.PhotoImage(im)
            self.bottom_lbl.configure(image=ph)
            self.bottom_lbl.image = ph
        else:
            self.bottom_lbl.configure(text="bottom: no image")

        pc = results.get("lidar")
        img = lidar_to_image(pc, target_xy=self._target_xy(pc))
        ph = ImageTk.PhotoImage(img)
        self.lidar_lbl.configure(image=ph)
        self.lidar_lbl.image = ph

        self._draw_telemetry()

    def _target_xy(self, pc):
        t = self.target
        if not t or not t.get("object_position") or pc is None or len(pc) == 0:
            return None
        pos0 = t["object_position"][0]
        return (pos0[0], pos0[1])

    def _draw_telemetry(self):
        try:
            s = self.client.getMultirotorState().kinematics_estimated
            p = s.position
            v = s.linear_velocity
            sp = math.sqrt(v.x_val ** 2 + v.y_val ** 2 + v.z_val ** 2)
            self.tel_lbl.configure(
                text=(f"pos x={p.x_val:8.2f}  y={p.y_val:8.2f}  z={p.z_val:8.2f}  "
                      f"speed={sp:5.2f} m/s   mode={self.manual and 'MANUAL' or 'AUTO'}   "
                      f"paused={self._is_paused()}   "
                      f"keys: {self._keys_str()}\n"
                      f"M: toggle AUTO/MANUAL   W/A/S/D move  R/F up/down  Q/E yaw  "
                      f"+/- speed  P pause  Esc exit")
            )
        except Exception as e:
            self.tel_lbl.configure(text=f"telemetry err: {type(e).__name__}: {e}")

    def _keys_str(self):
        return " ".join(sorted(k for k in self.keys)) or "-"

    def _is_paused(self):
        try:
            return self.client.simIsPause()
        except Exception:
            return None

    # ---- manual flight (flyer logic) ----
    def _apply_manual(self):
        moving = False
        dx = dy = dz = d_yaw = 0.0
        if "w" in self.keys or "up" in self.keys:
            dx += 1.0 * self.speed; moving = True
        if "s" in self.keys or "down" in self.keys:
            dx -= 1.0 * self.speed; moving = True
        if "a" in self.keys or "left" in self.keys:
            dy -= 1.0 * self.speed; moving = True
        if "d" in self.keys or "right" in self.keys:
            dy += 1.0 * self.speed; moving = True
        if "r" in self.keys or "space" in self.keys:
            dz -= 1.0 * self.speed; moving = True
        if "f" in self.keys:
            dz += 1.0 * self.speed; moving = True
        if "q" in self.keys:
            d_yaw += 1.0 * self.yaw_speed; moving = True
        if "e" in self.keys:
            d_yaw -= 1.0 * self.yaw_speed; moving = True
        if "p" in self.keys:
            self.keys.discard("p")
            try:
                self.client.simPause(not self.client.simIsPause())
            except Exception:
                pass
        if "+" in self.keys or "equal" in self.keys:
            self.keys.discard("+"); self.keys.discard("equal")
            self.speed = min(20.0, self.speed + 1.0)
        if "-" in self.keys or "minus" in self.keys:
            self.keys.discard("-"); self.keys.discard("minus")
            self.speed = max(0.5, self.speed - 1.0)
        try:
            self.client.simPause(False)
            if moving:
                yaw = self._cur_yaw_deg()
                c, s_ = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
                vx = dx * c - dy * s_
                vy = dx * s_ + dy * c
                if d_yaw:
                    ym = airsim.YawMode(is_rate=True, yaw_or_rate=d_yaw)
                else:
                    ym = airsim.YawMode(is_rate=False, yaw_or_rate=yaw)
                self.client.moveByVelocityAsync(
                    vx, vy, dz, duration=0.5,
                    drivetrain=airsim.DrivetrainType.MaxDegreeOfFreedom, yaw_mode=ym,
                )
            else:
                self.client.hoverAsync()
        except Exception as e:
            self.info_telemetry_err(e)

    def info_telemetry_err(self, e):
        try:
            self.tel_lbl.configure(text=f"err: {type(e).__name__}: {e}")
        except Exception:
            pass

    def _cur_yaw_deg(self):
        try:
            s = self.client.getMultirotorState().kinematics_estimated
            r = s.orientation
            return math.degrees(math.atan2(
                2.0 * (r.w_val * r.z_val + r.x_val * r.y_val),
                1.0 - 2.0 * (r.y_val ** 2 + r.z_val ** 2),
            ))
        except Exception:
            return 0.0

    # -- startup / shutdown --
    def _start(self):
        self._pump_target()
        self._schedule()

    def _quit(self):
        try:
            self.client.simPause(False)
        except Exception:
            pass
        self.root.destroy()


def lidar_to_image(pc, size=420, target_xy=None):
    img = Image.new("RGB", (size, size), (8, 8, 12))
    draw = ImageDraw.Draw(img)
    if pc is None or len(pc) == 0:
        draw.text((10, 10), "lidar: no points", fill=(255, 255, 0))
        return img
    xs = pc[:, 0]
    ys = pc[:, 1]
    zs = pc[:, 2]
    cx = (xs.max() + xs.min()) / 2 if xs.size else 0
    cy = (ys.max() + ys.min()) / 2 if ys.size else 0
    dx = max(xs.max() - xs.min(), 1e-6)
    dy = max(ys.max() - ys.min(), 1e-6)
    cxs = 2 * size / 3
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

    if target_xy:
        tx, ty = target_xy
        px = size // 2 + int((tx - cx) * s)
        py = size // 2 - int((ty - cy) * s)
        if 0 <= px < size and 0 <= py < size:
            r = 8
            draw.ellipse((px - r, py - r, px + r, py + r), outline=(255, 0, 255), width=3)
            draw.text((px + 10, py + 6), "TARGET", fill=(255, 0, 255))
    draw.text((6, 6), f"lidar: {len(pc)} pts", fill=(255, 255, 255))
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=30001)
    ap.add_argument("--retry", type=int, default=60)
    args = ap.parse_args()
    deadline = time.time() + args.retry
    while True:
        try:
            v = MissionViewer(args.port)
            v._start()
            return
        except Exception as e:
            if time.time() >= deadline:
                print(f"mission fatal: {type(e).__name__}: {e}")
                sys.exit(1)
            print(f"mission: window not ready ({type(e).__name__}: {e}); retrying...")
            time.sleep(2)


if __name__ == "__main__":
    main()
