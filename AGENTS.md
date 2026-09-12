# AGENTS.md — Hard Documentation Rules for AI Agents

> This file is a **hard rule** for any AI agent (opencode, Claude, other chat bots)
> working in this repository. Violating it is a failure. Read it before doing work.

---

## 1. Purpose of this file

The human maintains a fleet of agents across multiple machines (local dev box + a
remote **H100** VM at `ssh main.copper.sanati-emp.coder`). Agents frequently hand work
off to other agents, sessions are lost, and context is expensive. **The only durable
knowledge is the documentation we write down.** Therefore: if you did it, you must
document it.

---

## 2. The hard rule (applies to EVERY session)

> **Every action you take that changes state, every problem you hit, every solution
> you apply, every runbook you produce — MUST be written into the docs before the
> session ends.**

Concretely, for each thing you do, record **all four** of these, in the doc(s) below:

| What | Meaning |
|------|---------|
| **What you did** | The action, command, file, or change. Include exact paths and commands. |
| **Problem** | The blocker you faced (error message, symptom, constraint). |
| **Solution** | Exactly how you fixed it (what you tried, what worked, why). |
| **Runbook** | The repeatable step-by-step procedure to do the task again (commands + expected output), so another agent or the human can execute it without you. |

---

## 3. Where documentation goes

Maintain a **running journal** and keep the runbooks/status docs current.

### 3.1 `docs/DOC_JOURNAL.md` (NEW per session — append)

- If it doesn't exist, **create** it with a heading for the current session
  (date + short goal).
- Append, in reverse-date order or a simple chronological log, each session's:
  - **Goal**
  - **What you did** (with commands/paths)
  - **Problems faced** (verbatim errors/symptoms)
  - **Solutions applied**
  - **Current state** (what's done, what's pending)
  - **Next steps**
- One entry block per topic/work-item. Be terse but complete.

### 3.2 Runbooks & status docs (keep updated)

- `docs/AEROVLA_EVAL_RUNBOOK.md` — full eval ops (transfer → server → eval → metrics → pull results).
- `docs/SPLIT_RUNBOOK.md` — split execution (UE4 server on local GPU + model on H100), pre-created by an earlier agent.
- `docs/PROJECT_HANDOFF.md` — project context (what the project is, all changes, formats, env, disk state, blockers).
- Create new `docs/<NAME>_RUNBOOK.md` for any new workflow you produce.
- **When you change a workflow, UPDATE the corresponding runbook in the same session.**

### 3.3 Formatting rules for docs

- Markdown only. Use tables for change-logs (item / what / problem / solution).
- Include **exact shell commands** (copy-pasteable) and **expected output** markers.
- Record **file paths with line numbers** for code changes (`src/foo.py:42`).
- Note **hardware/env constraints** (e.g. "6 GB VRAM cannot run model", "H100 MIG has no Vulkan → CPU render").

---

## 4. Mandatory milestones for documentation

Do these **without being asked**:

1. **Start of work**: create/append the `docs/DOC_JOURNAL.md` session entry stating your goal.
2. **After any state change** (new file, edit, install, config change): log "what + problem + solution" in the journal.
3. **On a surfaced problem**: even if unresolved, log the problem + attempted solutions + what remains.
4. **End of session / handoff**: update `docs/PROJECT_HANDOFF.md` (§1 "what changed" tables, §7 disk-state, §9 next steps), update the relevant runbook, and add a final journal summary.
5. **Never rely on memory**: if you cannot open the file, write the doc block into your final message so the human can paste it.

---

## 5. Convention: SPLIT/deprecated-path markers

When something becomes deprecated or is replaced:
- Keep old runbooks but prepend `> **SUPERSEDED** <date> — see <new-doc> for the current approach.`
- Do not delete documentation of a failed attempt; it is valuable. Mark it clearly instead.

---

## 6. Current key context (verified facts — keep this section updated)

- **Project**: AeroVLA (ECCV 2026) — VLA model for UAV navigation, TravelUAV/AirSim.
- **User goal**: closed-loop eval of a custom LoRA (`checkpoints/`) on the custom
  `BrushifyCountryRoads` env; results viewed locally.
- **Local box**: RTX 3050, 6 GB VRAM → **cannot run the model** (even 4-bit OOM, verified).
  Can run the **UE4/AirSim server** for rendering.
- **H100 VM** (`ssh main.copper.sanati-emp.coder`, project at `/workspaces/AeroVLA`):
  - MIG 7g.80gb, **no working Vulkan** → UE4 renders on CPU (llvmpipe), ~20 h for 123 eps.
  - venv `.venv` (`--system-site-packages`) has full stack (transformers 4.42.4, peft,
    accelerate 0.32.1, airsim client patched, bf16 wrapper).
- **Split approach** (current plan): local UE4 server + H100 model client, connected by
  SSH **reverse tunnels** (H100 cannot reach local directly, but local can reach H100).
  - Local `airsim_plugin/AirVLNSimulatorServerTool.py:696` → `HOST='0.0.0.0'`.
  - `scripts/split.sh` opens server + `ssh -R` tunnels; H100 `param.py` stays `127.0.0.1`.
- **Env archive layout** (learned the hard way): `envs/BrushifyCountryRoads.zip` is a
  **standalone complete UE4 env** (launcher + binary + 2.47 GB `.pak` + Engine). Do NOT
  let 7z auto-merge the `.z01`/`.z02` (they are separate raw-data parts, `.z01` corrupt,
  `.z02` empty). Extract with the `.z0x` files moved aside.
- **Env layout schema (since 2026-09-10)**: per-env folder `envs/<Map>/` with
  `data_raws/` (episode archives) and `engine/<Map>/` (extracted UE4, launcher
  `engine/<Map>/<Map>.sh`). Server resolves via `resolve_env_launcher`
  (`AirVLNSimulatorServerTool.py`), uses `engine_dir='engine'` in `env_exec_path_dict`.
- **Local env status**: **BrushifyCountryRoads engine EXTRACTED + verified** at
  `envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/` (launcher `.sh` + 165M
  binary + 2.47G pak, chmod +x applied); server resolves it (exists+exec True).
  BrushifyForestPack extracted but nested at
  `envs/BrushifyForestPack/envs/BrushifyForestPack/` (pending move to `engine/`;
  not registered in `env_exec_path_dict`, not needed for brushify eval).
- **Split eval is launch-ready**: local `bash scripts/split.sh [PORT] [--windowed]`
  sanity check passes; then H100 `bash scripts/run_eval.sh`. **Data prep flow
  (2026-09-10)**: if `merged_data.json` is missing, run on H100
  `bash scripts/prepare_env_data.sh <Map>` (TravelUAV generator → then symlinks
  `dataset_raw/<Map>` → `envs/data_raws/<Map>`). `aerovla_wrapper_ui.py` now has
  optional `tkinter`/`ImageTk` imports (headless-safe; commit `f0be9b2`).
- **One-command split (since 2026-09-10)**: `split.sh` auto-launches the H100 eval
  via `ssh H100 bash scripts/run_eval.sh <PORT> /tmp/split_eval.log` (independent,
  tracked, pidfile `/tmp/aerovla_eval_<PORT>.pid`); **`kill $(cat
  /tmp/aerovla_split_<PORT>.pid)` tears down everything** (server + tunnel + viewer +
  UE4 orphans via `fuser -k <PORT+1..+16>` + remote eval). Viewer fixed to a single
  persistent tkinter window (commit `7a45cdb`). **Scene view mode** = `SpringArmChase`
  (chase cam) on port 30001 since commit `b59fc25` (UE4 reads `settings/<port>/settings.json`
  `ViewMode` at launch only; current run keeps its baked view). During a run the eval
  sets the drone pose every step → **no WASD flight**; the window is a watch-debug view.
- **LiDAR sensor (since 2026-09-12)**: `AIRSIM_SETTINGS_TEMPLATE` now has a
  `"Lidar1"` sensor (SensorType 6, 16ch, Range 100 m, 100000 PPS, 10 RPS, HFOV ±90°
  VFOV −5..−35°, `SensorLocalFrame`) on every drone; client `Lidar(BaseSensor)` in
  `AirVLNSimulatorClientTool_AeroVLA.py` captures `getLidarData('Lidar1')`
  (point_cloud/time_stamp/pose/segmentation) in both `getSensorInfo()` and
  `move_path_by_actions` → per-frame `{'sensors':{...,'lidar':...}}` persisted by
  `save_logs` into `log/000000.json`. Verified live on ForestPack (25 pts after
  sim motion; 0 pts at the high template spawn — sim must be unpaused + near
  geometry). Template `ViewMode` also fixed `Manual`→`SpringArmChase` for
  consistency with committed per-port settings.
  validity` `b59fc25` changed only the generated files → next
  regeneration would silently revert chase cam). Working tree already had the
  template fix to `SpringArmChase`; included it in this commit.
- **Live-view gotcha (since 2026-09-12)**: viewer windows on the local box
  `:1` (3840x1080). UE4 ForestPack windowed 1280x720 chase cam. **Mission
  console** (`scripts/mission_viewer.py`, since 2026-09-12, replaces
  `multiview.py`/`camera_viewer.py` as the `--cameras` panel): ONE window with
  TARGET box + FRONT + BOTTOM + LIDAR + telemetry + M = AUTO/MANUAL takeover
  (in MANUAL, WASD/R-F/Q-E fly via nonblocking `moveByVelocityAsync`/`hoverAsync`;
  local pushes `/tmp/aerovla_manual.json` to H100 over ssh; H100 `eval_aerovla.py`
  `_manual_takeover_active()` blocks before `makeActions` until clear). TARGET
  polls `/tmp/aerovla_target.json` (written per mission by
  `EvalBatchState._write_target_beacon` on H100).
  Key traps (fixed 2026-09-12, `8dcf696`+`d6a56cf`): **`mainloop()` must be
  called by `main()` AFTER `_start()`** — if `__init__` calls it, the frame loop
  never runs and ALL views stay placeholders (the "no view" bug). `WorkerPool`
  must NOT permanently fault workers on transient RPC errors (was killing all
  views silently) — it now soft-fails + auto-reconnects at >=25 consecutive
  fails. Beacon fields `object_desc`/`asset_name`/`instruction` are **lists**
  (unwrap `[0]`), and the label shows the full instruction (strip `<image>`,
  `wraplength=1000`). Window is 1200x800 but **the WM may move it between
  relaunches** — always re-read `xwininfo -root -tree` before cropping a
  screenshot (it landed at both `+1970+87` and `+37+106` this session).
  On this AirSim build **`simGetImages` (plural) RPCErrors; use per-camera
  `simGetImage(cam, ImageType.Scene)`** (PNG bytes) — mission/multiview patched
  accordingly. **Launch the server tool with CWD = `airsim_plugin/`** (default
  `--root_path ../envs` is CWD-relative; from project root it silently skips
  UE4 spawn while reporting success). Drone parked at `(150,150,-30)` returns
  ~4980 lidar pts (near geometry); spawn/high altitude → 0 pts.
- **Git sync (since 2026-09-10)**: both repos now track the personal fork
  `git@github.com:m-amin-sanati/AeroVLA.git` (added as `fork` remote; `origin` stays
  upstream `XuPeng23/AeroVLA`). Fork `main` is the single source of truth; local + H100
  `main` are synced to it. Server tool default `HOST=127.0.0.1` (`--host 0.0.0.0` for
  split/tunnel); `--windowed` optional; `aerovla_wrapper_ui.py` is pristine upstream.
  To sync H100 from local commits: `git push fork main` locally, then on H100
  `git fetch fork && git reset --hard fork/main`.

---

## 7. Checklist before finishing ANY session

- [ ] `docs/DOC_JOURNAL.md` session entry appended (goal / did / problems / solutions / state / next).
- [ ] `docs/PROJECT_HANDOFF.md` current (changes, disk state, blockers, next steps).
- [ ] Relevant runbooks updated to match reality.
- [ ] AGENTS.md §6 "current key context" refreshed if needed.
- [ ] No secrets/keys committed (never log passwords, tokens, model paths with credentials).
- **Drone manual flight (since 2026-09-12)**: use `scripts/drone_keyboard.py <port>`
  (tkinter window on `:1`; WASD/arrows move, R/F up/down, Q/E yaw, space up, C
  down, P pause toggle, +/-, Esc exit). Hard facts learned:
  (a) This AirSim build's drone is a REAL multirotor that flies via motor control
      (`move_path_by_actions` in `AirVLNSimulatorClientTool_AeroVLA.py`):
      `enableApiControl`+`armDisarm`+`moveByVelocityAsync` (NED: vz<0 = up).
      `simSetKinematics` teleport CANNOT fly it — vertical commands just fall
      (z → thousands). Use the flyer script, not manual kinematics, for flight.
  (b) Use NON-blocking `moveByVelocityAsync` (no `.join()`) in UI ticks or the
      RPC wedges; `moveToZAsync` also works for altitude.
  (c) `moveByVelocityAsync(0,0,0)` does NOT hold altitude here (sinks ~0.27 m/s);
      to hover use `hoverAsync()`. In a periodic UI tick, gate on a `moving` flag:
      moving → velocity cmd, idle → `hoverAsync()`.
  (d) Stuck-key trap in tkinter: binding per-key `<Left>` etc. with a handler that
      only ADDS to the keyset (no release) leaves the key set forever → the flyer
      keeps commanding motion ("cycles" after release). Use ONE generic
      `<KeyPress>`/`<KeyRelease>` pair and `keysym.lower()` add/discard (this is
      what `drone_keyboard.py` does).
  (e) If a drone is fallen/falling: freeze it with a zero-velocity
      `KinematicsState` + `simSetKinematics(..., ignore_collision=True)` +
      `simContinueForFrames(2)` + `simPause(True)` (the eval's `setPoses` pattern),
      then unpause and fly. Spawn: ForestPack `(-86.12, 283.52, -11.13)` from
      `data/meta/map_spawnarea_info.json`.
