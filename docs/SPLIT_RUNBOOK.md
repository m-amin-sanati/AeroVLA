# AeroVLA — Split Execution Runbook (UE4 on local GPU + Model on H100)

> Companion to `PROJECT_HANDOFF.md` and `AEROVLA_EVAL_RUNBOOK.md`.
> This runbook documents the **split/decentralized** execution approach:
> the UE4/AirSim **server + rendering** runs on a local machine with a working
> NVIDIA GPU (e.g. RTX 3060), while the **model inference/eval client** runs on the
> H100 VM. It also documents everything that was set up on the H100 and the exact
> code changes needed to use this approach.

---

## 1. Why this approach (context from the H100 run)

The H100 here is a **MIG 7g.80gb** slice with **broken NVIDIA Vulkan**:
`libGLX_nvidia.so.0` exports `vk_icdGetInstanceProcAddr` but the ICD fails to load
(`vkCreateInstance` → `-9 / VK_ERROR_INCOMPATIBLE_DRIVER`), even after upgrading the
Vulkan loader to 1.4.304. As a result UE4 (Vulkan RHI) must render on **CPU via
Mesa llvmpipe**, giving ~10–11 min/episode (≈20 h for 123 episodes).

The architecture already separates the **server** (owns UE4/AirSim, does rendering +
physics) from the **client** (runs the model, makes decisions) over msgpack-RPC.
So the natural fix is: put the **server on the user's local RTX 3060** (working
NVIDIA Vulkan → fast rendering) and keep the **model on the H100**.

```
[Local RTX 3060]                         [H100]
air simulator server + UE4  <--msgpack-->  eval_aerovla.py (model client)
AirVLNSimulatorServerTool.py   :30000      (venv, bf16 model, CUDA inference)
       |  AirSim API :30001+
   UE4 / AirSim world (GPU render)
```

---

## 2. What is already set up on the H100 (do NOT redo)

- Project: `/workspaces/AeroVLA`
- Python venv: `/workspaces/AeroVLA/.venv` (`--system-site-packages`).
  Installed: `transformers==4.42.4`, `peft==0.11.1`, `accelerate==0.32.1`,
  `numpy==1.26.4`, `scipy==1.12.0`, `timm==0.9.10`, `numba`, `yacs`, `tqdm`,
  `opencv-python-headless`, `msgpackrpc` (from repo zip), `tornado==4.5.3` +
  `backports.ssl-match-hostname`, `airsim==1.8.1` (manual copy), `einops`, `psutil`.
- Patched `.venv/lib/python3.12/site-packages/airsim/client.py` (removed
  `pack_encoding/unpack_encoding='utf-8'`) — required to avoid 4–5 s/step latency.
- `src/model_wrapper/aerovla_wrapper_ui.py`: **bf16** load (reverted from 4-bit),
  and `tkinter`/`ImageTk` imports made optional (no tkinter in venv).
- Data ready: 131 `merged_data.json`, 123 `dataset_raw` symlinks, split file
  (123 eps), `map_spawnarea_info.json` (56 areas).
- Eval CLI (unchanged defaults): `--simulator_tool_port 30000` etc.

---

## 3. Changes required to enable the split

> **UPDATE 2026-09-08 (VERIFIED):** the direct-connect approach described in
> the original §3B/C does NOT work here: the H100 (coder workspace pod, default
> route via `169.254.1.1`) **cannot reach the local LAN/VPN IPs at all** (tested
> `10.255.0.2`, `10.0.0.1`, `192.168.100.25`, `172.17.0.1` — all unreachable).
> The working solution is a **reverse SSH tunnel** (local initiates `ssh -R`),
> which makes the local UE4 server appear at the H100's own `127.0.0.1` — so
> **no `param.py` IP change is needed at all.** See §3A + §3T below.

### A) [LOCAL] `airsim_plugin/AirVLNSimulatorServerTool.py` — APPLIED
Line ~696:
```python
HOST = '0.0.0.0'      # was '127.0.0.1'   ✅ already changed locally
```
`serve()` uses the global `HOST` for `msgpackrpc.Address(HOST, PORT)` (line 662),
so binding to `0.0.0.0` makes it reachable via the tunnel.

### T) [LOCAL] Reverse tunnel (REPLACES the old §3B/§3C)
The H100 client connects to `127.0.0.1:30000` (its own localhost), which the
tunnel maps back to the local server. No `param.py` edit on the H100.

- Use the launcher: `scripts/split.sh` (starts the local server + opens
  `ssh -R 30000..30016:127.0.0.1:PORT` to the H100 + verifies reachability).
- Manual equivalent (port 30000, plus 30001..30016 for AirSim API):
  ```bash
  cd /path/to/AeroVLA/airsim_plugin
  python AirVLNSimulatorServerTool.py --gpus 0 --port 30000   # local, GPU render
  # in another local terminal:
  ssh -N -o ExitOnForwardFailure=yes \
      -R 30000:127.0.0.1:30000 \
      -R 30001:127.0.0.1:30001 ... -R 30016:127.0.0.1:30016 \
      main.copper.sanati-emp.coder
  ```

### C) [LOCAL] Network / firewall — tunnel only
No inbound opening needed from the H100; the tunnel is outbound (local → H100).
Just ensure SSH to `main.copper.sanati-emp.coder` works from local.

### D) [LOCAL] No Vulkan hacks
Do **not** set `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.json` on local —
that was the H100 CPU fallback. Use default NVIDIA Vulkan. The
`VK_ICD_FILENAMES/LIBGL_ALWAYS_SOFTWARE` vars inside the H100 `run_eval.sh` are
server-render-only and harmless to the client process — leave them.

---

## 4. Run sequence (split — VERIFIED)

> **FULL RUN VERIFIED 2026-09-09 (cont.)**: a full re-run of the 123-episode
> eval through the split ended the earlier flapping and produced real results.
> Sequence that works:
> 1. Local: `bash scripts/split.sh` (server + tunnel up).
> 2. H100: make `eval_save_path` EMPTY or the client will silently exit (see
>    gotcha 6 below). Backup any prior results first:
>    `mv eval_results/checkpoints/seen_valset/BrushifyCountryRoads
>        eval_results/checkpoints/seen_valset/BrushifyCountryRoads.bak_<tag>`
>    and `mkdir -p eval_results/checkpoints/seen_valset/BrushifyCountryRoads`.
>    **Then `chown ubuntu:ubuntu` it** (see gotcha 7).
> 3. H100: `cd /workspaces/AeroVLA && nohup bash scripts/run_eval.sh >
>    /tmp/split_eval.log 2>&1 &` (detached — plain `&` in one ssh cmd hangs ssh).
> 4. Watch: result dirs appear under
>    `eval_results/checkpoints/seen_valset/BrushifyCountryRoads/`; each has
>    `log/` (JSON), `frontcamera/`+`downcamera/`+`rightcamera/`+`rearcamera/`
>    (+ `_depth/`), `object_description.json`, `ori_info.json`. Ended at
>    ~2.3 ep / 5 min (≈5 h for 123) vs 20 h CPU-rendered.

> **Smoke test VERIFIED end-to-end 2026-09-09**: `bash scripts/split.sh` brought
> up the local server on `0.0.0.0:30000` (aero_vla python, pid 3828439), opened
> the reverse `ssh -R 30000..30016` tunnel, and `echo > /dev/tcp/127.0.0.1/30000`
> from the H100 returned `H100_REACHES_OK`. The tornado `Uncaught exception`
> lines in `/tmp/aerovla_server.log` are **benign** — artifacts of the port-probe
> closing mid-msgpack-RPC; the server stays up.
> Cleanup: `kill $(cat /tmp/aerovla_server.pid /tmp/aerovla_tunnel.pid)`.
> Verify liveness with `ss -ltnp | grep ':30000'` — do NOT use `pgrep` with
> literal `\|` patterns (they match nothing → false "DEAD").

### msgpack version alignment (REQUIRED — added 2026-09-09)

- Local server `aero_vla` env: `msgpack==1.1.2`.
- H100 client `.venv`: was `1.2.2` → **must be `1.1.2`** or msgpack-RPC framing
  breaks and the server crashes (`msgpackrpc/transport/tcp.py:27
  len(message)` on any real RPC) → client silently dies.
- Apply once on H100: `cd /workspaces/AeroVLA && .venv/bin/pip install
  msgpack==1.1.2`. Verify: real `ping` through the tunnel returns `True`.

```
# Terminal 1 — LOCAL (as a non-root user; UE4 refuses root):
bash /path/to/AeroVLA/scripts/split.sh
#   -> starts server, opens tunnels, prints "NEXT: on the H100, run ..."

# Terminal 2 — H100:
cd /workspaces/AeroVLA
bash scripts/run_eval.sh      # UNCHANGED (uses --simulator_tool_port 30000 -> H100 localhost -> tunnel -> local UE4)
```

Results still write to `eval_results/checkpoints/seen_valset/BrushifyCountryRoads`
on the H100. Aggregate with `bash scripts/metric.sh` on the H100 afterward.

The H100 `run_eval.sh` was verified 2026-09-08: same arg set as `eval_aerovla.sh`
(`--simulator_tool_port 30000`, `CUDA_VISIBLE_DEVICES=0`, `.venv` python) — works
for the split as-is.

---

## 5. Local server prerequisites (RTX 3060 box)

- Python 3.10/3.12 with `msgpackrpc`, `tornado`, `airsim` installed (server uses
  only stdlib + msgpackrpc + subprocess; heavy ML deps are H100-only).
  **Use the `aero_vla` conda env**: `/media/sanati/DriveE/miniconda3/envs/aero_vla/bin/python`
  (has all three depss). `scripts/split.sh` now defaults to it via `$SERVER_PYTHON`
  (override with `AEROVLA_SERVER_PYTHON=...`) and sanity-checks the deps at launch.
  The base miniconda python does NOT have `msgpackrpc` — do not use it.
- `airsim_plugin/settings/` writable and `envs/BrushifyCountryRoads/` writable by the
  runtime user (UE4 writes `Saved/Config`).
- Working Vulkan/OpenGL for UE4; run as non-root with a display or `xvfb-run`.

---

## 5.5. Visible / windowed UE4 (optional — SEE the scene live)

> Added 2026-09-09. By default the split launches UE4 with `-RenderOffscreen`
> (no window, rendering only). To **watch the scene** (useful for debugging the
> custom env / the drone's path), launch the server with **`--windowed`**.

### Right way — `scripts/split.sh --windowed`
The server arg is plumbed through `split.sh`:
```bash
bash scripts/split.sh --windowed        # visible window on this machine's :1
bash scripts/split.sh                   # default offscreen (no window)
bash scripts/split.sh 30000 --windowed  # also fine; port + flag in any order
```

### What it does
- `split.sh` forwards `--windowed` to `AirVLNSimulatorServerTool.py`.
- In the server, `make_env_launch_cmd` builds the UE4 command line once, used by
  BOTH `open_scene` and `reopen_scene_from_port`:
  - offscreen: `-RenderOffscreen -NoSound -NoVSync -GraphicsAdapter=<gpu> -settings=<path>`
  - windowed:  `-windowed -ResX=1280 -ResY=720 -WinX=740 -WinY=610 -NoSound -NoVSync -GraphicsAdapter=<gpu> -settings=<path>`
- Offscreen remains the default; `--windowed` just adds the visible flag per run.

### Facts verified on this box
- A real X display exists: Xorg on `:1`, `HDMI-0` 1920x1080, owned by user
  `sanati` (uid 1000). No Xvfb is running; `DISPLAY=:1` is set for `sanati`.
- The server runs as `sanati` → a windowed UE4 will render to `:1` fine.

### Applying mid-run WITHOUT killing a running eval
The `--windowed` choice is read at server start, but each new UE4 process is
launched with that flag. The flag governs the **next** spawned/reopened scene:
1. Start (or restart) with `--windowed`.
2. If a UE4 process is already up offscreen and you want it windowed now:
   ```bash
   pkill -f "BrushifyCountryRoads.*-RenderOffscreen"   # local, kills current UE4 only
   ```
   The client's next `reopen_scene_from_port` spawns UE4 **windowed**.
   (Single-scene runs need the full server restart: `kill $(cat
   /tmp/aerovla_server.pid)` then re-run `bash scripts/split.sh --windowed`.)

### Code reference
- Helper: `airsim_plugin/AirVLNSimulatorServerTool.py` `make_env_launch_cmd`
  (module level, just above `class EventHandler`).
- Arg: `--windowed` added to `argparse` (default False) in `__main__`.
- Launch sites (both route through the helper): initial `_open_scenes` and
  `reopen_scene_from_port`.
- `scripts/split.sh`: parses `--windowed`/`-h`/`--help`, forwards the flag.

### Notes / caveats
- Only the **next spawned/reopened** UE4 process gets the flag; the currently
  running offscreen episode is unaffected until it ends.
- Speed: windowed vs offscreen is negligible for output; the flight data is
  independent of whether a window is shown. The 200-step logic, screenshots and
  logs are identical.
- If you want it headless again, restore `-RenderOffscreen`.
- Server launch still uses `stdout=DEVNULL` — UE4's own stdout is discarded; the
  window is the only visible surface.

---

## 6. Gotchas (verified on H100)
1. **UE4 refuses root** — server must run as a normal user.
2. **airsim `client.py` utf-8 patch** is on the *client* (H100) side — already done.
3. UE4 needs write access to its `Saved/` dir — chown/chmod the env tree to the
   runtime user.
4. Don't enable the llvmpipe/GLX-disable workaround on a machine with working GPU
   Vulkan.
5. `timm` must be `>=0.9.10, <1.0.0` for the prismatic model (OpenVLA) to load.
6. **The client exits SILENTLY (exit 0, no traceback) right after
   "random shuffle data / dataset grouped by scene" when `self.data` is empty.**
   Cause: `load_my_datasets` (`src/vlnce_src/env_uav.py:122-131`) **skips every
   trajectory whose seq_name already exists in `eval_save_path`**. If all split
   entries already have a result dir (e.g. from a prior run), `self.data==[]` and
   `next_minibatch` returns `None` immediately (`env_uav.py:191-194`). Fix: point
   at a fresh/empty `eval_save_path` (back up old results first; do NOT delete if
   you might need them).
7. **PermissionError on save is a separate silent kill.** If you create the fresh
   `eval_save_path` dir as root (e.g. via the root SSH session), the eval runs as
   `ubuntu` and dies with `PermissionError: [Errno 13] ... makedirs` at
   `closeloop_util.py:44`. **Always `chown ubuntu:ubuntu <eval_save_path>` after
   creating it.**
8. **msgpack must match** (server 1.1.2 ↔ H100 venv 1.1.2) — see §4.
9. **Debugging SSH-output mangling:** the `coder ssh` banner goes to *stderr*; if
   you `2>&1` into `grep`, your command output gets garbled with the banner text.
   Redirect stdout to a file and read the file instead of greps across streams.
10. **The eval takes ~5 h for 123 eps locally-rendered** (2.3 ep / 5 min) —
    plan for the local machine + tunnel to stay up that long.
11. **A full 200-step episode with no stop signal is recorded as `oracle_`**
    (the model never emits a stop within `maxWaypoints`). That's expected, not
    an error — the first episode of a fresh run is often `oracle_`.

---

## 7. H100 run status while split is being set up

The H100 closed-loop eval can keep running in the background on CPU rendering
(~20 h total). Progress log: `eval.log`. At the time of writing it had completed
~49/123 episodes. If the split comes up first, either let the current run continue
to completion or halt it (`pkill -f eval_aerovla.py`) and restart with the split for
a much faster run — discarding the partial results.
