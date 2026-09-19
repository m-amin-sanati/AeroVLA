#!/bin/bash
# Foggy-lidar replay capture — KEYFRAME mode (2026-09-19)
# Captures exactly each episode's `index` keyframes from merged_data.json, so every
# img_name in data/aerovla_train_dataset_fog_lidar.json has a captured PNG + log JSON.
cd /workspaces/AeroVLA
export RP_PORT=30000
export RP_MAP=BrushifyForestPack
export RP_JSON_LIST=/workspaces/AeroVLA/data/uav_dataset/fog_lidar_episodes.json
export RP_OUT=/workspaces/AeroVLA/envs/lidar_capture
export RP_MAX_EPS=18
export RP_MAX_FRAMES=120
export RP_FOG=1.0
export RP_DATA_ROOT=/workspaces/AeroVLA/envs/data_raws
setsid /workspaces/AeroVLA/.venv/bin/python -u scripts/replay_capture_lidar.py > /workspaces/AeroVLA/envs/lidar_capture_keyframes.log 2>&1 < /dev/null &
echo "launched pid $!" > /tmp/launch18kf.pid
