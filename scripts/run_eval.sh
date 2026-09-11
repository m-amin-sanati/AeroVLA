#!/bin/bash
# ============================================================================
# AeroVLA - H100 closed-loop eval client (INDEPENDENT launcher)
#
# Runs eval_aerovla.py on the H100 against the local UE4/AirSim server reached
# through the reverse tunnels opened by scripts/split.sh (client just uses
# 127.0.0.1:<PORT>).
#
# Can be started TWO ways:
#   1. Standalone, manually on the H100:
#        bash scripts/run_eval.sh [PORT] [LOG]
#   2. Auto-launched by scripts/split.sh (passes LOCAL_PORT so the pidfile
#      name matches what split.sh tears down).
#
# CLEANUP: this script records the eval's real PID in
#   /tmp/aerovla_eval_<PORT>.pid
# and installs a trap so that TERM/INT/EXIT (e.g. `kill <pid>` of this script,
# or Ctrl-C) kills the eval process and its children automatically. No manual
# cleanup needed. split.sh can also read the same pidfile to tear the remote
# eval down from the local machine.
# ============================================================================
set -euo pipefail

PROJECT_ROOT="/workspaces/AeroVLA"
VENV="$PROJECT_ROOT/.venv/bin/python"
PORT="${1:-30000}"
LOG="${2:-/tmp/split_eval.log}"
PIDFILE="/tmp/aerovla_eval_${PORT}.pid"
# Map / env to evaluate; override with:  AEROVLA_MAP=BrushifyForestPack bash scripts/run_eval.sh
MAP="${AEROVLA_MAP:-BrushifyCountryRoads}"
EVAL_SAVE_DIR="./eval_results/checkpoints/seen_valset/${MAP}"
EVAL_JSON="./data/uav_dataset/seen_valset_splits/${MAP}.json"

chmod -R a+rX "$PROJECT_ROOT" 2>/dev/null || true
cd "$PROJECT_ROOT"

# The eval runs as the 'ubuntu' user (su below) and redirects to $LOG. If the
# log was pre-created by root (e.g. split.sh's ssh launcher, or a previous run)
# ubuntu cannot open it -> "Permission denied" -> eval dies instantly. Make it
# writable by everyone, and drop any stale pidfile so the confirm loop below is
# honest.
touch "$LOG" 2>/dev/null || true
chmod 666 "$LOG" 2>/dev/null || true
rm -f "$PIDFILE"

cleanup() {
  local pid=""
  if [ -f "$PIDFILE" ]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "${pid:-}" ] && [ "$pid" -gt 0 ] 2>/dev/null; then
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
      sleep 2
      kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
  fi
}
trap cleanup EXIT INT TERM

# Launch the eval as the 'ubuntu' user, background it inside the su shell and
# record the actual python PID (both the nohup-wrapped python -> eval_aerovla.py
# chain resolves to the same PID; $! is the python PID). This script then keeps
# running, waiting on that PID, so a TERM here triggers the trap above.
su -s /bin/bash ubuntu -c "
  cd $PROJECT_ROOT && \
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.json \
  LIBGL_ALWAYS_SOFTWARE=1 \
  CUDA_VISIBLE_DEVICES=0 \
  nohup $VENV -u $PROJECT_ROOT/src/vlnce_src/eval_aerovla.py \
    --run_type eval --name AerialVLA_Eval --gpu_id 0 \
    --simulator_tool_port $PORT --DDP_MASTER_PORT 80005 --batchSize 1 --maxWaypoints 200 \
    --dataset_path ./dataset_raw/ \
    --eval_save_path ${EVAL_SAVE_DIR} \
    --model_path ./checkpoints \
    --eval_json_path ${EVAL_JSON} \
    --map_spawn_area_json_path ./data/meta/map_spawnarea_info.json \
    --object_name_json_path ./data/meta/object_description.json \
    > $LOG 2>&1 & echo \$! > $PIDFILE
" || { echo "FATAL: failed to launch eval on this host (are you on the H100?)"; exit 1; }

echo "eval client launched (pid $(cat "$PIDFILE" 2>/dev/null)); log: $LOG"

# Keep this launcher alive owning the cleanup; exit when the eval exits
# naturally (run completed) or when we are TERMed (trap kills the eval).
while [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; do
  sleep 5
done
