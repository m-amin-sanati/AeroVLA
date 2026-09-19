#!/usr/bin/env python3
"""Foggy LiDAR replay-capture of recorded episode trajectories.

Goal: build a small training split of episodes whose `log/<frame>.json` carry a
real `sensors.lidar` key (clean geometry, captured under FOG), paired with
foggy RGB/depth PNGs, so the 3D LiDAR-visual fusion can be fine-tuned and the
cross-attention's use of LiDAR can be measured.

This is NOT a closed-loop model eval. It replays the *recorded* drone poses
(`merged_data.json['trajectory_raw_detailed']`), teleporting the drone along
the true trajectory with AirSim `simSetKinematics` (setPoses), enabling FOG on
the scene, then capturing per-frame sensors (state / imu / lidar) + images.

Usage (run from project root, like the eval). Config is passed via env vars
(because the client import chain runs HfArgumentParser on sys.argv)::

    RP_PORT=30000 RP_MAP=BrushifyForestPack \\
    RP_JSON_LIST=data/uav_dataset/seen_valset_splits/BrushifyForestPack.json \\
    RP_OUT=/tmp/lidar_capture \\
    RP_MAX_EPS=6 RP_MAX_FRAMES=120 RP_FOG=1.0 RP_DATA_ROOT=../envs/data_raws \\
        python scripts/replay_capture_lidar.py

RP_JSON_LIST is the same episode list format the eval uses (entries with a
"json": "<Map>/<uuid>/merged_data.json" key). The captured tree is written to
RP_OUT/<Map>/<uuid>/{log,<cam>,<cam>_depth,...}.
"""

import json
import os
import sys
import time

# The client module (via utils.logger -> src.common.param) runs
# HfArgumentParser on sys.argv at import time. To avoid the HF parser choking
# on our CLI args, we read all replay config from environment variables (no
# argparse on sys.argv) and clear sys.argv to a benign value before importing.
import argparse as _argparse


def _load_cfg():
    def _get(key, default=None, cast=None):
        v = os.environ.get(key, default)
        if v is None:
            return None
        if cast is not None:
            return cast(v)
        return v
    return {
        'port': _get('RP_PORT', 30000, int),
        'map': _get('RP_MAP', 'BrushifyForestPack'),
        'json_list': _get('RP_JSON_LIST', None),
        'out': _get('RP_OUT', None),
        'max_episodes': _get('RP_MAX_EPS', 6, int),
        'max_frames': _get('RP_MAX_FRAMES', 120, int),
        'fog': _get('RP_FOG', 1.0, float),
        'data_root': _get('RP_DATA_ROOT', '../envs/data_raws'),
        'all_frames': _get('RP_ALL_FRAMES', False) and _get('RP_ALL_FRAMES', '').lower() in ('1', 'true', 'yes'),
    }


sys.argv = ['replay_capture_lidar']

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
for _p in (_HERE, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import cv2

from airsim_plugin.AirVLNSimulatorClientTool_AeroVLA import AirVLNSimulatorClientTool
import airsim

RGB_FOLDER = ['frontcamera', 'leftcamera', 'rightcamera', 'rearcamera', 'downcamera']
DEPTH_FOLDER = [name + '_depth' for name in RGB_FOLDER]
CAMERAS = ['FrontCamera', 'LeftCamera', 'RightCamera', 'RearCamera', 'DownCamera']


def gen_machines_info(sim_port, open_scenes):
    return [
        {
            'MACHINE_IP': '127.0.0.1',
            'SOCKET_PORT': int(sim_port),
            'MAX_SCENE_NUM': 16,
            'open_scenes': open_scenes,
            'gpus': [0] * 8,
        },
    ]


def main():
    cfg = _load_cfg()
    if not cfg['json_list'] or not cfg['out']:
        print('[replay] RP_JSON_LIST and RP_OUT env vars required', file=sys.stderr)
        sys.exit(2)
    args = _argparse.Namespace(**cfg)

    # 1. Load episode list
    with open(args.json_list, 'r') as f:
        entries = json.load(f)
    print(f'[replay] {len(entries)} episode entries in list; capturing at most {args.max_episodes}')

    # 2. Connect to server + open foggy scene
    machines_info = gen_machines_info(args.port, [args.map])
    tool = AirVLNSimulatorClientTool(machines_info=machines_info)
    tool.run_call()
    tool.set_weather_fog(args.fog)
    print(f'[replay] scene opened + fog={args.fog}')

    client = tool.airsim_clients[0][0]  # single scene
    assert client is not None

    captured = 0
    for entry in entries:
        if captured >= args.max_episodes:
            break
        rel_json = entry['json']                       # e.g. BrushifyForestPack/<uuid>/merged_data.json
        parts = rel_json.strip('/').split('/')
        map_name, seq_uuid = parts[0], parts[1]
        if map_name != args.map:
            continue
        src_dir = os.path.join(args.data_root, map_name, seq_uuid)
        merged_json = os.path.join(src_dir, 'merged_data.json')
        if not os.path.exists(merged_json):
            print(f'[replay] skip missing {merged_json}')
            continue
        with open(merged_json, 'r') as f:
            merged = json.load(f)
        frames = merged['trajectory_raw_detailed']
        # Captures exactly the keyframes the training split references (the `index`
        # list in merged_data.json), so every img_name in the split has a captured
        # PNG/log. Falls back to frames[:max_frames] when no index is available.
        keyframe_list = merged.get('index') or []
        if keyframe_list and not args.all_frames:
            cap_frames = [int(fr) for fr in keyframe_list]
            print(f'[replay] capture {seq_uuid}: {len(cap_frames)} KEYFRAMES (index max={max(cap_frames) if cap_frames else 0})')
        else:
            cap_frames = list(range(len(frames)))
            if args.max_frames:
                cap_frames = cap_frames[:args.max_frames]
            print(f'[replay] capture {seq_uuid}: {len(cap_frames)} frames (max_frames={args.max_frames})')

        out_dir = os.path.join(args.out, map_name, seq_uuid)
        # Clear any previous capture of this episode so stale frames (e.g. an
        # earlier 0..39 run) never mix with the fresh keyframe set.
        if os.path.isdir(out_dir):
            print(f'[replay] clearing existing {out_dir}')
            import shutil
            shutil.rmtree(out_dir)
        os.makedirs(os.path.join(out_dir, 'log'), exist_ok=True)
        for cam in RGB_FOLDER + DEPTH_FOLDER:
            os.makedirs(os.path.join(out_dir, cam), exist_ok=True)

        for idx in cap_frames:
            frame = frames[idx]
            pos = frame['position']
            ori = frame['orientation']
            pose = airsim.Pose(
                position_val=airsim.Vector3r(x_val=pos[0], y_val=pos[1], z_val=pos[2]),
                orientation_val=airsim.Quaternionr(x_val=ori[0], y_val=ori[1], z_val=ori[2], w_val=ori[3]),
            )
            # place drone
            ok = tool.setPoses(poses=[[pose]])
            if not ok:
                print(f'[replay] setPoses failed frame {idx}; abort episode')
                break
            # sensors (state/imu/lidar)
            sens = tool.getSensorInfo()
            if sens is None:
                print(f'[replay] getSensorInfo failed frame {idx}; abort episode')
                break
            sens_info = sens[0][0]['sensors']

            # images (5 rgb + 5 depth, foggy) — retry transient RPC failures
            imgs = None
            for attempt in range(8):
                imgs = tool.getImageResponses(cameras=CAMERAS)
                if imgs is not None:
                    break
                print(f'[replay]   getImageResponses retry {attempt+1}/8 frame {idx}')
                time.sleep(2)
            if imgs is None:
                print(f'[replay] getImageResponses failed after retries frame {idx}; abort episode')
                break
            rgb_list, depth_list = imgs[0][0]

            # write log json
            info = {'frame': idx, 'sensors': sens_info}
            with open(os.path.join(out_dir, 'log', str(idx).zfill(6) + '.json'), 'w') as f:
                json.dump(info, f)

            # write images
            for cid, cam in enumerate(RGB_FOLDER):
                cv2.imwrite(os.path.join(out_dir, cam, str(idx).zfill(6) + '.png'), rgb_list[cid])
            for cid, cam in enumerate(DEPTH_FOLDER):
                cv2.imwrite(os.path.join(out_dir, cam, str(idx).zfill(6) + '.png'), depth_list[cid])

            if idx % 20 == 0:
                npts = len(sens_info.get('lidar', {}).get('point_cloud', [])) // 3
                print(f'[replay]   frame {idx}: lidar_pc_pts={npts}')
            time.sleep(0.02)

        # sanity: confirm lidar has points (fog scene near geometry)
        try:
            d = client.getLidarData(lidar_name='Lidar1', vehicle_name='')
            print(f'[replay] final lidar pts this episode: {len(list(d.point_cloud)) // 3}')
        except Exception as e:
            print(f'[replay] final lidar probe failed: {e}')

        captured += 1
        print(f'[replay] done {seq_uuid} -> {out_dir}')

    tool.closeScenes()
    print(f'[replay] FINISHED: {captured} episodes captured under fog={args.fog}')


if __name__ == '__main__':
    main()
