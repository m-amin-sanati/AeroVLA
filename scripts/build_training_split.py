#!/usr/bin/env python3
"""Build the Option-A training split JSON from the user's own AirSim episodes.

Reads each episode's `merged_data.json` (which carries the sampled
`index`/keyframes and `trajectory_raw_detailed` per-frame telemetry) plus the
per-frame `log/<frame>.json` states, and emits a `train_step_a`-compatible
split:

    [ { "traj_rel_dir", "img_name", "instruction", "label{fwd,down,yaw}",
        "is_last_step", "is_penultimate" }, ... ]

The Action label for a keyframe is the body-frame (NED) displacement from that
keyframe to the NEXT sampled keyframe — the exact semantics
`AirVLNSimulatorClientTool_AeroVLA.move_path_by_actions` applies
(`dx=fwd*cos(yaw)`, `dy=fwd*sin(yaw)`, `dz=down`, plus a yaw delta).

Label math (mirrors `move_path_by_actions` at airsim_plugin/...:322):
  T_current = global position/orientation of keyframe i
  P_next    = global position of keyframe i+1
  r         = body yaw at keyframe i (ZYX euler)
  The requested displacement in body frame:
      d_global = P_next - T_current.position
      local    = R(-yaw) @ d_global          (ground-projected, z is NED down)
      fwd      = local[0],  down = local[2],  yaw = yaw_next - yaw_current
  NOTE: we do NOT recompute an exact axis-aligned rotation; the AirSim client
  itself moves by `fwd*cos(yaw)` / `fwd*sin(yaw)` from the *current* yaw, so the
  deltas ARE the local body-frame fwd/down deltas (dx,dy projected onto the
  world XY then rotated by -current_yaw).

Usage:
  python scripts/build_training_split.py \
      --eps_dir envs/data_raws/BrushifyForestPack \
      --meta_dir envs/data_raws/BrushifyForestPack \
      --out data/aerovla_train_dataset_forestpack.json \
      --map BrushifyForestPack \
      [--min_frames 5] [--skip_missing]

Corrupt episodes (missing merged_data.json / mark.json / logs) are skipped and
reported. Idempotent: re-runs overwrite `--out`.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
from scipy.spatial.transform import Rotation as R

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def body_frame_deltas(p_cur, q_cur, yaw_cur, p_next, yaw_next):
    """Return (fwd, down, yaw) displacement of p_next relative to p_cur in the
    UAV's local body (NED) frame at p_cur."""
    d = np.asarray(p_next, dtype=np.float64) - np.asarray(p_cur, dtype=np.float64)
    # NED: z already positive-down (AirSim NED). Rotate XY by -current yaw.
    ca = np.cos(-yaw_cur)
    sa = np.sin(-yaw_cur)
    local_x = ca * d[0] - sa * d[1]
    local_y = sa * d[0] + ca * d[1]
    local_z = d[2]  # NED down already matches AirSim z-down
    fwd = local_x
    # down in NED is +z; but the client's "down" action also moves along world z.
    down = local_z
    yaw = yaw_next - yaw_cur
    return float(fwd), float(down), float(yaw)


def build_episode(ep, map_name, min_frames, skip_missing):
    """Yield one split dict per keyframe. Returns (samples, skipped_reason).

    I/O: reads only `merged_data.json` (its `trajectory_raw_detailed` already
    carries every frame's global position/orientation, verified identical to the
    per-frame `log/<frame>.json` `sensors.state` — so we DO NOT open 232 log
    files per episode; those reads are slow on this box's page-cache storage).

    `skip_missing` is advisory: a missing per-frame state for a keyframe aborts
    the whole episode (returns (None, reason)) rather than emitting a zeroed
    action label, which would be misleading for training."""
    merged = os.path.join(ep, "merged_data.json")
    mark = os.path.join(ep, "mark.json")
    if not os.path.isfile(merged):
        return [], "no merged_data.json"
    if not os.path.isfile(mark):
        return [], "no mark.json"
    try:
        j = json.load(open(merged))
    except Exception as e:
        return [], "merged corrupt: %s" % e

    index = j.get("index") or []
    if len(index) < 2:
        return [], "index too short (<2)"
    if len(index) < min_frames:
        return [], "index too short (<%d)" % min_frames
    conv = j.get("conversations") or []
    instr = ""
    for c in conv:
        if c.get("from") == "human":
            instr = c.get("value", "")
            break
    if not instr:
        return [], "no instruction"

    # Precompute the densest per-frame state table from trajectory_raw_detailed
    # (indexed by frame number = row index, 0-based; verified to match the
    # per-frame log/state files exactly).
    frames = j.get("trajectory_raw_detailed") or []
    if len(frames) < max(index) + 1:
        return [], "trajectory_raw_detailed too short"
    def state_of(fr):
        if fr < len(frames):
            return frames[fr].get("position"), frames[fr].get("orientation")
        return None, None

    rel_dir = "{}/{}".format(map_name, os.path.basename(ep))
    samples = []
    n_idx = len(index)
    for i, fr in enumerate(index):
        ip1 = index[i + 1] if (i + 1) < n_idx else None
        img_name = "{:06d}.png".format(fr)
        is_last = ip1 is None
        if not is_last:
            p_cur, q_cur = state_of(fr)
            p_next, q_next = state_of(ip1)
            if p_cur is None or p_next is None:
                return None, "missing state for keyframes %d/%d" % (fr, ip1)
            def yaw_of(quat):
                return R.from_quat(quat).as_euler("zyx")[0] if quat is not None else 0.0
            yaw_cur = yaw_of(q_cur)
            yaw_next = yaw_of(q_next)
            fwd, down, yaw = body_frame_deltas(p_cur, q_cur, yaw_cur, p_next, yaw_next)
            label = {"fwd": fwd, "down": down, "yaw": yaw}
        else:
            label = {"fwd": 0.0, "down": 0.0, "yaw": 0.0}

        samples.append({
            "traj_rel_dir": rel_dir,
            "img_name": img_name,
            "instruction": instr,
            "label": label if isinstance(label, dict) else {"fwd": 0.0, "down": 0.0, "yaw": 0.0},
            "is_last_step": bool(is_last),
            "is_penultimate": bool(i == n_idx - 2),
        })
    if not samples:
        return [], "no samples"
    return samples, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eps_dir", default="envs/data_raws/BrushifyForestPack",
                    help="dir containing one subdir per episode")
    ap.add_argument("--meta_dir", default=None,
                    help="where merged_data.json + mark.json live (default = eps_dir)")
    ap.add_argument("--out", default="data/aerovla_train_dataset_forestpack.json")
    ap.add_argument("--map", default="BrushifyForestPack")
    ap.add_argument("--min_frames", type=int, default=5)
    ap.add_argument("--skip_missing", action="store_true",
                    help="skip corrupt episodes instead of failing")
    ap.add_argument("--max_eps", type=int, default=None)
    args = ap.parse_args()

    meta_dir = args.meta_dir or args.eps_dir
    out_samples = []
    skipped = []
    eps = sorted(
        d for d in os.listdir(meta_dir)
        if os.path.isdir(os.path.join(meta_dir, d))
    )
    if args.max_eps is not None:
        eps = eps[: args.max_eps]

    for m in eps:
        ep = os.path.join(meta_dir, m)
        samples, reason = build_episode(ep, args.map, args.min_frames, args.skip_missing)
        if samples is None:
            reason = reason or "unbuildable"
            if not args.skip_missing:
                raise SystemExit("FATAL episode {}: {}".format(m, reason))
            skipped.append((m, reason))
            continue
        if not samples:
            skipped.append((m, reason or "no samples"))
            continue
        out_samples.extend(samples)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out_samples, f, indent=2)
    print("wrote {} samples ({} episodes) -> {}".format(len(out_samples), len(eps), args.out))
    if skipped:
        print("skipped {} episodes:".format(len(skipped)))
        for m, r in skipped[:20]:
            print("  {}: {}".format(m, r))
        if len(skipped) > 20:
            print("  ... and {} more".format(len(skipped) - 20))


if __name__ == "__main__":
    main()
