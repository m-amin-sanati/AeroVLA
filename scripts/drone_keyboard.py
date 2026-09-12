#!/usr/bin/env python3
"""AeroVLA drone keyboard flyer — WASD/arrows move the drone via AirSim API.

This is a control window for the LIVE sim. The engine is API-driven
(`ExternalPhysicsEngine`), so arrow keys in the UE4 window only move the chase
camera, NOT the drone. This script gives you real flight control by translating
key presses into flight commands using the SAME motor-control API the eval uses
(`AirVLNSimulatorClientTool_AeroVLA.py` `move_path_by_actions`):
`enableApiControl`/`armDisarm` + periodic `moveByVelocityAsync` + `rotateToYawAsync`.

IMPORTANT: do NOT use `simSetKinematics` for manual flight on this build — it does
not create real motion (the drone is a real multirotor here, and teleporting with
simSetKinematics fights gravity/collision and is unusable for altitude). The eval
itself flies with motor control, and so do we.

  Arrows / WASD : move forward/back, strafe left/right (in drone yaw frame)
  R / F         : ascend / descend  (F = down, R = up)
  Q / E         : yaw left / right (rotate in place)
  Space         : hold to move up (alias of R)
  C             : hold to move down (alias of F)
  +/- or [ ]    : speed up / slow down (m/s per key, default 3)
  P             : toggle pause (simPause)
  Esc           : exit

Usage:
    python scripts/drone_keyboard.py [SCENE_PORT]

Connects to LOCAL AirSim API (default 30001). Requires tkinter + airsim
(local `aero_vla` conda env).
"""
import argparse
import math

import airsim

STATS = [
    ("w", "forward", "Up"),
    ("s", "back", "Down"),
    ("a", "left", "Left"),
    ("d", "right", "Right"),
    ("r", "up", " "),
    ("f", "down", " "),
    ("q", "yaw_left", " "),
    ("e", "yaw_right", " "),
]


class DroneFlyer:
    def __init__(self, port=30001, speed=5.0, yaw_speed=60.0):
        import tkinter as tk
        self.tk = tk
        self.port = port
        self.speed = speed
        self.yaw_speed = yaw_speed
        self.client = airsim.MultirotorClient(ip="127.0.0.1", port=port, timeout_value=10)
        self.client.confirmConnection()
        try:
            self.client.enableApiControl(True)
            self.client.armDisarm(True)
            self.client.simPause(False)
        except Exception:
            pass
        self.keys = set()
        self.root = tk.Tk()
        self.root.title(f"AeroVLA drone flyer  (AirSim :{port})")
        self.root.geometry("560x300")
        self.info = tk.Label(
            self.root,
            text="connecting...",
            justify="left",
            font=("Monospace", 11),
            anchor="nw",
        )
        self.info.pack(fill="both", expand=True, padx=10, pady=8)
        for k in ("<KeyPress>", "<KeyRelease>"):
            self.root.bind(k, self._on_key)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self._tick()
        self.root.mainloop()

    def _on_key(self, e):
        if e.keysym in ("Escape", "Return"):
            if e.keysym == "Escape":
                self._quit()
            return
        sym = e.keysym.lower()
        if e.type == "2":  # KeyPress
            self.keys.add(sym)
        else:
            self.keys.discard(sym)

    def _quit(self):
        try:
            self.client.simPause(False)
        except Exception:
            pass
        self.root.destroy()

    def _tick(self):
        try:
            self._apply()
            pos = self.client.getMultirotorState().kinematics_estimated.position
            self.info.configure(text=self._summary(pos))
        except Exception as e:
            self.info.configure(text=f"error: {type(e).__name__}: {e}")
        self.root.after(30, self._tick)

    def _summary(self, pos):
        keys = " ".join(sorted(self.keys)) or "-"
        return (
            f"pos  x={pos.x_val:8.2f}  y={pos.y_val:8.2f}  z={pos.z_val:8.2f}\n"
            f"paused={self._is_paused()}  speed={self.speed:5.1f} m/s  yaw={self.yaw_speed:.0f} deg/s\n"
            f"pressed: {keys}\n\n"
            f"W/A/S/D or arrows: move (drone yaw frame)\n"
            f"R/F: up/down   Q/E: yaw   P: pause   +/-: speed\n"
            f"Esc: exit"
        )

    def _is_paused(self):
        try:
            return self.client.simIsPause()
        except Exception:
            return None

    def _apply(self):
        moving = False
        dx = 0.0
        dy = 0.0
        dz = 0.0
        d_yaw = 0.0

        if "w" in self.keys or "Up" in self.keys or "up" in self.keys:
            dx += 1.0 * self.speed; moving = True
        if "s" in self.keys or "Down" in self.keys or "down" in self.keys:
            dx -= 1.0 * self.speed; moving = True
        if "a" in self.keys or "Left" in self.keys or "left" in self.keys:
            dy -= 1.0 * self.speed; moving = True
        if "d" in self.keys or "Right" in self.keys or "right" in self.keys:
            dy += 1.0 * self.speed; moving = True
        if "r" in self.keys or "space" in self.keys:
            # NED: negative z = up
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
            self.keys.remove("+") if "+" in self.keys else None
            self.keys.discard("equal")
            self.speed = min(20.0, self.speed + 1.0)
        if "-" in self.keys or "minus" in self.keys:
            self.keys.remove("-") if "-" in self.keys else None
            self.keys.discard("minus")
            self.speed = max(0.5, self.speed - 1.0)

        try:
            # Keep the sim unpaused while a command is active (AirSim auto-pauses
            # the drone when it rests on the ground).
            self.client.simPause(False)
            if moving:
                yaw = self._cur_yaw_deg()
                c, s_ = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
                # local (drone yaw frame) -> world velocity
                vx = dx * c - dy * s_
                vy = dx * s_ + dy * c
                # Drives yaw inside the SAME velocity command: rotating -> use a
                # yaw RATE (smooth), else hold current yaw. (Calling
                # rotateToYawAsync here AND passing yaw_mode=hold on the next line
                # made them fight, so yaw never turned.)
                if d_yaw:
                    ym = airsim.YawMode(is_rate=True, yaw_or_rate=d_yaw)
                else:
                    ym = airsim.YawMode(is_rate=False, yaw_or_rate=yaw)
                self.client.moveByVelocityAsync(
                    vx, vy, dz,
                    duration=0.5,
                    drivetrain=airsim.DrivetrainType.MaxDegreeOfFreedom,
                    yaw_mode=ym,
                )
            else:
                # Idle: hoverAsync HOLDS altitude (moveByVelocityAsync(0,0,0) on
                # this build just decelerates then the drone slowly sinks).
                self.client.hoverAsync()
        except Exception as e:
            self.info.configure(text=f"err: {type(e).__name__}: {e}")

    def _cur_yaw_deg(self):
        try:
            s = self.client.getMultirotorState().kinematics_estimated
            r = s.orientation
            yaw = math.atan2(
                2.0 * (r.w_val * r.z_val + r.x_val * r.y_val),
                1.0 - 2.0 * (r.y_val ** 2 + r.z_val ** 2),
            )
            return math.degrees(yaw)
        except Exception:
            return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=30001)
    ap.add_argument("--speed", type=float, default=3.0)
    ap.add_argument("--yawspeed", type=float, default=60.0)
    args = ap.parse_args()
    DroneFlyer(args.port, args.speed, args.yawspeed)


if __name__ == "__main__":
    main()
