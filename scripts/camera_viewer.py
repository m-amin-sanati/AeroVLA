#!/usr/bin/env python3
"""Live drone camera viewer for a running split eval.

Connects to the LOCAL AirSim API port (the UE4 scene port assigned by the
server, default 30001) as an independent read-only client and shows the drone
cameras (Front/Left/Right/Rear/Down) live in a tkinter window, plus a
telemetry line (frame count + position).

Usage:
    python scripts/camera_viewer.py               # port 30001
    python scripts/camera_viewer.py 30001         # pick the scene port

It does NOT talk to the H100 eval client and does NOT disturb the running
eval: it only issues read-only simGetImages + getMultirotorState against the
same AirSim server the eval drives.

Requires tkinter + Pillow + numpy + airsim (available in the local `aero_vla`
conda env):
    /media/sanati/DriveE/miniconda3/envs/aero_vla/bin/python scripts/camera_viewer.py
"""

import argparse
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageTk

import airsim

CAMERAS = ["FrontCamera", "LeftCamera", "RightCamera", "RearCamera", "DownCamera"]
CAMERA_TITLES = ["FRONT", "LEFT", "RIGHT", "REAR", "DOWN"]

PANEL_W, CELL_H = 512, 384
COLS = ["FrontCamera", "DownCamera"]
ROWS = [
    ("FrontCamera", "DownCamera"),
    ("LeftCamera", "RightCamera"),
    ("RearCamera", "RearCamera"),
]


def fetch_images(client):
    requests = [
        airsim.ImageRequest(cam, airsim.ImageType.Scene, pixels_as_float=False, compress=False)
        for cam in CAMERAS
    ]
    responses = client.simGetImages(requests)
    frames = {}
    for cam, resp in zip(CAMERAS, responses):
        if resp.width == 0 or resp.height == 0:
            continue
        buf = resp.image_data_uint8
        img = np.frombuffer(buf, dtype=np.uint8).reshape(resp.height, resp.width, 3)
        frames[cam] = img
    return frames


def make_panel(frames, titles):
    """Build a single RGB image: 3 rows (front+down / left+right / rear) + telemetry bar."""
    h_cell, w_cell = CELL_H, PANEL_W
    rows_imgs = []
    for left_cam, right_cam in ROWS:
        left = frames.get(left_cam)
        right = frames.get(right_cam)
        show_right = right_cam != left_cam
        if left is None:
            left = np.zeros((256, 256, 3), np.uint8)
        if right is None:
            right = np.zeros((256, 256, 3), np.uint8)
        l_im = Image.fromarray(left).resize((w_cell // 2, h_cell), Image.BICUBIC)
        if show_right:
            r_im = Image.fromarray(right).resize((w_cell // 2, h_cell), Image.BICUBIC)
        else:
            r_im = Image.fromarray(left).resize((w_cell // 2, h_cell), Image.BICUBIC)
            l_im = l_im.resize((w_cell // 2, h_cell), Image.BICUBIC)
        row = Image.new("RGB", (w_cell, h_cell), (0, 0, 0))
        row.paste(l_im, (0, 0))
        row.paste(r_im, (w_cell // 2, 0))
        draw = ImageDraw.Draw(row)
        draw.text((10, 10), titles["left"], fill=(0, 0, 255))
        if show_right:
            draw.text((w_cell // 2 + 10, 10), titles["right"], fill=(0, 0, 255))
        rows_imgs.append(row)
    panel = Image.new("RGB", (w_cell, h_cell * 3), (0, 0, 0))
    y = 0
    for row in rows_imgs:
        panel.paste(row, (0, y))
        y += h_cell
    return panel


class Viewer:
    def __init__(self, port):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.title("AeroVLA drone cameras")
        self.lbl = tk.Label(self.root, borderwidth=0)
        self.lbl.pack()
        self.client = airsim.MultirotorClient(ip="127.0.0.1", port=port, timeout_value=15)
        self.client.confirmConnection()
        self.frame_idx = 0
        self.titles = {
            "front": "FRONT", "left": "LEFT", "right": "RIGHT", "rear": "REAR", "down": "DOWN",
        }
        self._update()
        self.root.mainloop()

    def _update(self):
        import cv2  # only used for BGR->RGB conversion of raw uint8 buffers
        frames = fetch_images(self.client)
        panel = make_panel(frames, self.titles)
        draw = ImageDraw.Draw(panel)
        try:
            pos = self.client.getMultirotorState().kinematics_estimated.position
            vel = self.client.getMultirotorState().kinematics_estimated.linear_velocity
            speed = np.sqrt(vel.x_val**2 + vel.y_val**2 + vel.z_val**2)
            line = (f"frame {self.frame_idx}  pos x={pos.x_val:.1f} y={pos.y_val:.1f} "
                    f"z={pos.z_val:.1f}  speed {speed:.2f} m/s")
        except Exception:
            line = f"frame {self.frame_idx}"
        draw.rectangle((0, 0, panel.width, 60), fill=(0, 0, 0))
        draw.text((12, 12), line, fill=(0, 255, 0))
        photo = ImageTk.PhotoImage(panel)
        self.lbl.configure(image=photo)
        self.lbl.image = photo
        self.frame_idx += 1
        self.root.after(50, self._update)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=30001, help="AirSim API port (scene port)")
    args = ap.parse_args()
    try:
        Viewer(args.port)
    except Exception as e:
        print(f"viewer error: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
