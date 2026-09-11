#!/bin/bash
# ============================================================================
# AeroVLA - SPLIT EVAL launcher
#
# Runs the UE4/AirSim server on the LOCAL GPU machine and the model eval on the
# H100, connected over SSH reverse tunnels. The H100 eval client is launched
# automatically by calling scripts/run_eval.sh over SSH.
#
# WHY a tunnel: the H100 (coder workspace pod) cannot reach this machine
# directly (no route to local LAN/VPN IPs). But this machine CAN ssh to the
# H100, so we expose this machine's server ports as 127.0.0.1 ports ON the H100
# via `ssh -R`. The H100 client then just uses its default 127.0.0.1 and never
# knows the server is remote - no param.py change needed.
#
# CLEANUP: A single `kill`/Ctrl-C on this script tears EVERYTHING down
# (local server + tunnel + camera viewer + the remote H100 eval), because
#   - every local child pid is tracked here,
#   - the remote eval's pid is written to /tmp/aerovla_eval_<PORT>.pid by
#     run_eval.sh, and this script ssh-kills it on exit.
# No manual cleanup needed.
#
# Usage:
#   bash scripts/split.sh [LOCAL_SERVER_PORT] [MAP] [--windowed] [--cameras] [--no-eval]
#
#   LOCAL_SERVER_PORT    port for the AeroVLA server (default 30000); AirSim
#                        API ports are LOCAL_SERVER_PORT+1 .. +16.
#   MAP                  env/map to evaluate (default BrushifyCountryRoads;
#                        e.g. BrushifyForestPack). Passed to the H100 eval as
#                        AEROVLA_MAP so run_eval.sh targets the right env.
#   --windowed           launch UE4 in a visible window instead of offscreen,
#                        so you can SEE the scene live.
#   --cameras            ALSO show the live drone camera views (front/left/
#                        right/rear/down) in a local window (requires local
#                        DISPLAY). Picks up the first scene port
#                        (=LOCAL_SERVER_PORT+1). Requires the aero_vla conda
#                        env (airsim + Pillow + tkinter).
#   --no-eval            do NOT auto-start the H100 eval client (just server+
#                        tunnel+viewer); start run_eval.sh manually later.
# ============================================================================
set -euo pipefail

H100="main.copper.sanati-emp.coder"
MAIN_PID=$$

WINDOWED=0
CAMERAS=0
NO_EVAL=0
declare -a POS_ARGS=()
for a in "$@"; do
  case "$a" in
    --windowed) WINDOWED=1 ;;
    --cameras)  CAMERAS=1 ;;
    --no-eval)  NO_EVAL=1 ;;
    -h|--help)  sed -n '1,22p' "$0"; exit 0 ;;
    *)          POS_ARGS+=("$a") ;;
  esac
done

LOCAL_PORT=30000
MAP=BrushifyCountryRoads
for p in "${POS_ARGS[@]}"; do
  case "$p" in
    ''|*[!0-9]*) MAP="$p" ;;            # non-numeric -> map name
    *)            LOCAL_PORT="$p" ;;    # numeric -> server port
  esac
done
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
echo "  MAP                : ${MAP}"
echo "  H100 host          : ${H100}"
echo "  Project            : ${ROOT}"
echo "  UE4 mode           : $([ ${WINDOWED} -eq 1 ] && echo 'WINDOWED (visible)' || echo 'offscreen')"
echo "  Camera viewer      : $([ ${CAMERAS} -eq 1 ] && echo 'ON (live drone cams in local window)' || echo 'off')"
echo "  H100 eval client   : $([ ${NO_EVAL} -eq 1 ] && echo 'MANUAL (--no-eval)' || echo 'AUTO via run_eval.sh')"
echo "=============================================="

# --------------------------------------------------------------------------
# 0. Sanity checks
# --------------------------------------------------------------------------
[ -x "${ROOT}/envs/${MAP}/engine/${MAP}/${MAP}.sh" ] \
  || { echo "FATAL: env launcher missing for ${MAP} - did you extract envs/ into the new engine/ layout?"; exit 1; }
command -v ssh >/dev/null || { echo "FATAL: ssh not found"; exit 1; }
command -v python >/dev/null || true

# --------------------------------------------------------------------------
# Teardown: killing THIS script (or Ctrl-C) kills everything we spawned:
# local server + tunnel + camera viewer + the remote H100 eval.
# --------------------------------------------------------------------------
SERVER_PID=""
TUNNEL_PID=""
VIEWER_PID=""
EVAL_PIDFILE="/tmp/aerovla_eval_${LOCAL_PORT}.pid"
_CLEANED=0

cleanup() {
  if [ "$_CLEANED" -eq 1 ]; then
    exit 0   # already ran (TERM then EXIT both fire the trap)
  fi
  _CLEANED=1
  echo
  echo "==> AeroVLA split teardown (script pid ${MAIN_PID}) ..."

  # 1. local server
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    pkill -TERM -P "$SERVER_PID" 2>/dev/null || true   # UE4 children
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    sleep 1
    kill -KILL "$SERVER_PID" 2>/dev/null || true
    echo "    stopped local server ($SERVER_PID)"
  fi

  # 1b. kill any UE4 scene binaries that outlive the server (the `.sh` launcher
  #     spawns the UE4 binary as a grandchild, so the pkill -P above misses it).
  #     Use fuser -k on the scene ports (local procs only) + settings-path pkill.
  local sp
  for sp in $(seq $((LOCAL_PORT+1)) $((LOCAL_PORT+16))); do
    fuser -k -TERM "${sp}/tcp" >/dev/null 2>&1 || true
  done
  sleep 2
  for sp in $(seq $((LOCAL_PORT+1)) $((LOCAL_PORT+16))); do
    fuser -k -KILL "${sp}/tcp" >/dev/null 2>&1 || true
  done
  pkill -9 -f "settings/${LOCAL_PORT}/" 2>/dev/null || true

  # 2. reverse tunnel
  if [ -n "$TUNNEL_PID" ] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
    kill -TERM "$TUNNEL_PID" 2>/dev/null || true
    echo "    stopped reverse tunnel ($TUNNEL_PID)"
  fi

  # 3. camera viewer
  if [ -n "$VIEWER_PID" ] && kill -0 "$VIEWER_PID" 2>/dev/null; then
    kill -TERM "$VIEWER_PID" 2>/dev/null || true
    echo "    stopped camera viewer ($VIEWER_PID)"
  fi

  # 4. remote H100 eval: the pidfile lives ON the H100 (/tmp/aerovla_eval_<PORT>.
  #    pid, written by run_eval.sh). Read it via ssh, kill the tree, then also
  #    pkill any eval_aerovla.py as a fallback (belt & braces).
  if [ ${NO_EVAL} -eq 0 ]; then
    echo "    stopping remote H100 eval ..."
    timeout 30 ssh -o ConnectTimeout=10 -o LogLevel=ERROR "${H100}" \
      "REPID=\$(cat /tmp/aerovla_eval_${LOCAL_PORT}.pid 2>/dev/null || true); \
       if [ -n \"\${REPID}\" ] && [ \"\${REPID}\" -gt 0 ] 2>/dev/null; then \
         pkill -TERM -P \${REPID} 2>/dev/null; kill -TERM \${REPID} 2>/dev/null; sleep 2; \
         pkill -TERM -P \${REPID} 2>/dev/null; kill -KILL \${REPID} 2>/dev/null; \
       fi; \
       pkill -f eval_aerovla.py 2>/dev/null; rm -f /tmp/aerovla_eval_${LOCAL_PORT}.pid; true" \
      >/dev/null 2>&1 || true
  fi
  rm -f "$EVAL_PIDFILE"

  echo "==> teardown complete."
  exit 0
}
trap cleanup EXIT INT TERM

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
SERVER_PID="$(cat /tmp/aerovla_server.pid 2>/dev/null || true)"
sleep 3
if [ -z "$SERVER_PID" ] || ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "FATAL: local server failed to start. See /tmp/aerovla_server.log"
  tail -20 /tmp/aerovla_server.log
  exit 1
fi
echo "    server pid ${SERVER_PID}"
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
TUNNEL_PID="$(cat /tmp/aerovla_tunnel.pid 2>/dev/null || true)"
sleep 4
if [ -z "$TUNNEL_PID" ] || ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
  echo "FATAL: ssh tunnel failed. See /tmp/aerovla_tunnel.log"
  tail -20 /tmp/aerovla_tunnel.log
  exit 1
fi
echo "    tunnel pid ${TUNNEL_PID}"
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
      "${ROOT}/scripts/camera_viewer.py" "${SCENE_PORT}" --retry 120 \
      > /tmp/aerovla_cameras.log 2>&1 &
    echo $! > /tmp/aerovla_cameras.pid
    VIEWER_PID="$(cat /tmp/aerovla_cameras.pid 2>/dev/null || true)"
    sleep 2
    if [ -z "$VIEWER_PID" ] || ! kill -0 "$VIEWER_PID" 2>/dev/null; then
      echo "    WARNING: camera viewer failed to start. See /tmp/aerovla_cameras.log"
      tail -20 /tmp/aerovla_cameras.log
    else
      echo "    camera viewer pid ${VIEWER_PID}"
      echo "    log: /tmp/aerovla_cameras.log"
      echo "    (scene camera feed window opened; press q/close to stop later)"
    fi
  else
    echo "    SKIP: no \$DISPLAY on this session - start viewer manually with:"
    echo "    DISPLAY=:0 $VIEWER_PY ${ROOT}/scripts/camera_viewer.py ${SCENE_PORT}"
  fi
fi

# --------------------------------------------------------------------------
# 5. Auto-launch the H100 eval client (unless --no-eval).
#    We invoke the INDEPENDENT scripts/run_eval.sh over SSH (it writes the
#    remote eval pid into ${EVAL_PIDFILE} so our trap can tear it down).
# --------------------------------------------------------------------------
if [ ${NO_EVAL} -eq 0 ]; then
  echo "==> Auto-starting H100 eval client (scripts/run_eval.sh, port ${LOCAL_PORT}) ..."
  # NOTE: run_eval.sh runs REMOTELY and writes /tmp/aerovla_eval_<PORT>.pid on
  # the H100. We verify it exists via ssh. (It is NOT a local file.)
  timeout 40 ssh -o ConnectTimeout=15 -o LogLevel=ERROR "${H100}" \
    "cd /workspaces/AeroVLA && AEROVLA_MAP=${MAP} bash scripts/run_eval.sh ${LOCAL_PORT} /tmp/split_eval.log" \
    > /tmp/aerovla_eval_launch.log 2>&1 || true
  REMOTE_PID="$(timeout 15 ssh -o ConnectTimeout=10 -o LogLevel=ERROR "${H100}" \
    "cat /tmp/aerovla_eval_${LOCAL_PORT}.pid 2>/dev/null" 2>/dev/null | grep -E '^[0-9]+$' | head -1 || true)"
  if [ -n "$REMOTE_PID" ]; then
    echo "    remote eval pid ${REMOTE_PID} (pidfile on H100)"
    echo "    log: /tmp/split_eval.log"
  else
    echo "    WARNING: could not confirm remote eval start (see /tmp/aerovla_eval_launch.log)."
    tail -5 /tmp/aerovla_eval_launch.log
  fi
else
  echo "==> (--no-eval) skipping H100 eval client. Start it later manually:"
  echo "    ssh ${H100} 'cd /workspaces/AeroVLA && AEROVLA_MAP=${MAP} bash scripts/run_eval.sh ${LOCAL_PORT}'"
fi

cat <<EOF

==============================================
Split stack is UP. Run the 3D scene + cameras live in the local windows.
This script (pid ${MAIN_PID}) stays attached. The remote eval is also running
on the H100 and will be cleaned up together with the local stack on exit.

To stop EVERYTHING (local server + tunnel + viewer + remote eval), just:
  kill ${MAIN_PID}     (easiest, from any shell)
or Ctrl-C right here (if attached to this terminal).

(AirSim scene ports come up after the H100 eval opens scenes; the camera
 viewer retries in the background - see /tmp/aerovla_cameras.log.)

Results land on the H100 at:
  /workspaces/AeroVLA/eval_results/checkpoints/seen_valset/BrushifyCountryRoads
==============================================
EOF

# --------------------------------------------------------------------------
# 6. Stay attached: keep this script alive so a Ctrl-C / `kill <pid>` triggers
#    the cleanup trap above, which tears down the whole stack (local server +
#    tunnel + viewer + remote H100 eval).
#    NOTE: must NOT block on a plain external command (`tail -f /dev/null`
#    swallows TERM until it exits). A `wait` loop is the reliable way to stay
#    resident AND catch TERM/INT immediately.
# --------------------------------------------------------------------------
echo "${MAIN_PID}" > /tmp/aerovla_split_${LOCAL_PORT}.pid
while true; do
  wait || true
done
