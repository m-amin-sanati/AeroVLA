#!/bin/bash
# ============================================================================
# AeroVLA - SPLIT EVAL launcher
#
# Runs the UE4/AirSim server on the LOCAL GPU machine and the model eval on the
# H100, connected over SSH reverse tunnels.
#
# WHY a tunnel: the H100 (coder workspace pod) cannot reach this machine
# directly (no route to local LAN/VPN IPs). But this machine CAN ssh to the
# H100, so we expose this machine's server ports as 127.0.0.1 ports ON the H100
# via `ssh -R`. The H100 client then just uses its default 127.0.0.1 and never
# knows the server is remote - no param.py change needed.
#
# Usage:
#   bash scripts/split.sh [LOCAL_SERVER_PORT] [--windowed] [--cameras]
#
#   LOCAL_SERVER_PORT    port for the AeroVLA server (default 30000); AirSim
#                        API ports are LOCAL_SERVER_PORT+1 .. +16.
#   --windowed           launch UE4 in a visible window instead of offscreen,
#                        so you can SEE the scene live.
#   --cameras            ALSO show the live drone camera views (front/left/
#                        right/rear/down) in a local window (requires local
#                        DISPLAY). Picks up the first scene port
#                        (=LOCAL_SERVER_PORT+1). Requires the aero_vla conda
#                        env (airsim + Pillow + tkinter).
# ============================================================================
set -euo pipefail

H100="main.copper.sanati-emp.coder"

WINDOWED=0
CAMERAS=0
declare -a POS_ARGS=()
for a in "$@"; do
  case "$a" in
    --windowed) WINDOWED=1 ;;
    --cameras)  CAMERAS=1 ;;
    -h|--help)  sed -n '1,20p' "$0"; exit 0 ;;
    *)          POS_ARGS+=("$a") ;;
  esac
done

LOCAL_PORT="${POS_ARGS[0]:-30000}"
AIRSIM_PORTS_MAX=$((LOCAL_PORT + 16))   # 30001..30016 for scenes

# Server deps live in the local aero_vla conda env (msgpackrpc/tornado/airsim).
SERVER_PYTHON="${AEROVLA_SERVER_PYTHON:-/media/sanati/DriveE/miniconda3/envs/aero_vla/bin/python}"
if [ ! -x "$SERVER_PYTHON" ]; then
  echo "FATAL: server python not found: $SERVER_PYTHON"
  echo "       set AEROVLA_SERVER_PYTHON to a python with msgpackrpc+tornado+airsim."
  exit 1
fi
if ! "$SERVER_PYTHON" -c "import msgpackrpc, tornado, airsim" 2>/dev/null; then
  echo "FATAL: $SERVER_PYTHON lacks server deps (msgpackrpc/tornado/airsim)."
  exit 1
fi

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "=============================================="
echo "AeroVLA split launcher"
echo "  LOCAL port         : ${LOCAL_PORT} (+1..+16 AirSim API)"
echo "  H100 host          : ${H100}"
echo "  Project            : ${ROOT}"
echo "  UE4 mode           : $([ ${WINDOWED} -eq 1 ] && echo 'WINDOWED (visible)' || echo 'offscreen')"
echo "  Camera viewer      : $([ ${CAMERAS} -eq 1 ] && echo 'ON (live drone cams in local window)' || echo 'off')"
echo "=============================================="

# --------------------------------------------------------------------------
# 0. Sanity checks
# --------------------------------------------------------------------------
[ -x "${ROOT}/envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/BrushifyCountryRoads.sh" ] \
  || { echo "FATAL: env launcher missing - did you extract envs/ into the new engine/ layout?"; exit 1; }
command -v ssh >/dev/null || { echo "FATAL: ssh not found"; exit 1; }
command -v python >/dev/null || true

# --------------------------------------------------------------------------
# 1. Start the simulator server (LOCAL, GPU rendering, background)
#    Binds 0.0.0.0:LOCAL_PORT (HOST change already applied to
#    AirVLNSimulatorServerTool.py).
# --------------------------------------------------------------------------
echo "==> Starting local simulator server on 0.0.0.0:${LOCAL_PORT} ..."
: > /tmp/aerovla_server.log
(
  cd "${ROOT}/airsim_plugin"
  if [ ${WINDOWED} -eq 1 ]; then
    nohup "$SERVER_PYTHON" AirVLNSimulatorServerTool.py --gpus 0 --port "${LOCAL_PORT}" --windowed \
      > /tmp/aerovla_server.log 2>&1 &
  else
    nohup "$SERVER_PYTHON" AirVLNSimulatorServerTool.py --gpus 0 --port "${LOCAL_PORT}" \
      > /tmp/aerovla_server.log 2>&1 &
  fi
  echo $! > /tmp/aerovla_server.pid
)
sleep 3
if ! kill -0 "$(cat /tmp/aerovla_server.pid)" 2>/dev/null; then
  echo "FATAL: local server failed to start. See /tmp/aerovla_server.log"
  tail -20 /tmp/aerovla_server.log
  exit 1
fi
echo "    server pid $(cat /tmp/aerovla_server.pid)"
echo "    log: /tmp/aerovla_server.log"

# --------------------------------------------------------------------------
# 2. Open reverse tunnels to the H100 (background)
#    H100:127.0.0.1:{port} -> LOCAL:127.0.0.1:{port}
# --------------------------------------------------------------------------
TUNNEL_ARGS=()
for p in $(seq "${LOCAL_PORT}" "${AIRSIM_PORTS_MAX}"); do
  TUNNEL_ARGS+=( -R "${p}:127.0.0.1:${p}" )
done
echo "==> Opening reverse tunnels to ${H100} ..."
nohup ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -N "${TUNNEL_ARGS[@]}" "${H100}" > /tmp/aerovla_tunnel.log 2>&1 &
echo $! > /tmp/aerovla_tunnel.pid
sleep 4
if ! kill -0 "$(cat /tmp/aerovla_tunnel.pid)" 2>/dev/null; then
  echo "FATAL: ssh tunnel failed. See /tmp/aerovla_tunnel.log"
  tail -20 /tmp/aerovla_tunnel.log
  exit 1
fi
echo "    tunnel pid $(cat /tmp/aerovla_tunnel.pid)"
echo "    log: /tmp/aerovla_tunnel.log"

# --------------------------------------------------------------------------
# 3. Verify reachability from the H100
# --------------------------------------------------------------------------
echo "==> Verifying H100 can reach local server through the tunnel ..."
if timeout 15 ssh -o ConnectTimeout=10 -o LogLevel=ERROR "${H100}" \
    "echo > /dev/tcp/127.0.0.1/${LOCAL_PORT} 2>/dev/null && echo OK || echo FAIL" \
    | sed -n '$p' | grep -q OK; then
  echo "    OK: H100 -> 127.0.0.1:${LOCAL_PORT} reachable"
else
  echo "    WARNING: could not verify (H100 banner noise). Continuing."
fi

# --------------------------------------------------------------------------
# 4. Optional: live drone camera viewer (LOCAL window, read-only)
#    Connects to the first scene's AirSim API port (=LOCAL_PORT+1) as an
#    independent client and shows front/left/right/rear/down feeds live.
# --------------------------------------------------------------------------
if [ ${CAMERAS} -eq 1 ]; then
  SCENE_PORT=$((LOCAL_PORT + 1))
  VIEWER_PY="${SERVER_PYTHON}"   # same aero_vla env has airsim+Pillow+tkinter
  echo "==> Starting live camera viewer on AirSim :${SCENE_PORT} ..."
  if [ -n "${DISPLAY:-}" ]; then
    nohup env DISPLAY="${DISPLAY}" "$VIEWER_PY" \
      "${ROOT}/scripts/camera_viewer.py" "${SCENE_PORT}" \
      > /tmp/aerovla_cameras.log 2>&1 &
    echo $! > /tmp/aerovla_cameras.pid
    sleep 2
    if ! kill -0 "$(cat /tmp/aerovla_cameras.pid)" 2>/dev/null; then
      echo "    WARNING: camera viewer failed to start. See /tmp/aerovla_cameras.log"
      tail -20 /tmp/aerovla_cameras.log
    else
      echo "    camera viewer pid $(cat /tmp/aerovla_cameras.pid)"
      echo "    log: /tmp/aerovla_cameras.log"
      echo "    (scene camera feed window opened; press q/close to stop later)"
    fi
  else
    echo "    SKIP: no \$DISPLAY on this session - start viewer manually with:"
    echo "    DISPLAY=:0 $VIEWER_PY ${ROOT}/scripts/camera_viewer.py ${SCENE_PORT}"
  fi
fi

cat <<'EOF'

==============================================
NEXT: on the H100, run the eval client:
  cd /workspaces/AeroVLA
  bash scripts/run_eval.sh

Results go to:
  /workspaces/AeroVLA/eval_results/checkpoints/seen_valset/BrushifyCountryRoads

To stop local server + tunnel (+ camera viewer if launched):
  kill $(cat /tmp/aerovla_server.pid) $(cat /tmp/aerovla_tunnel.pid) \
       $(cat /tmp/aerovla_cameras.pid 2>/dev/null)
==============================================
EOF
