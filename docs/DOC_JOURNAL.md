# DOC_JOURNAL.md — AeroVLA session log

Chronological log. Each entry: goal / did / problem / solution / state / next.
Latest at the bottom.

---

## Session 2026-09-08 — Split-eval setup + local env extraction + AGENTS.md

### Goal
- Let the H100 CPU-rendered eval finish (56/123 at last check).
- In parallel, prepare the **split** approach: UE4 server on local RTX 3050 +
  model client on H100, connected over SSH reverse **tunnels** (since the H100
  cannot reach the local LAN directly).
- Create `AGENTS.md` hard documentation rules (user directive).

### What I did
- **Created `AGENTS.md`** at project root with the documentation hard-rules
  (what/problom/solution/runbook → docs/journal/runbooks/handoff; milestone
  checklist; deprecated-marking convention; verified-context section).
- **Local env extraction** (`envs/BrushifyCountryRoads/`):
  - Archive layout investigation: `7z l BrushifyCountryRoads.zip` initially auto-
    merged `.z01`/`.z02` (misleading output). Verified by moving `.z0x` aside:
    - `BrushifyCountryRoads.zip` = **standalone complete UE4 env** (launcher +
      165 MB binary + 2.36 GB `.pak` + Engine + PhysX/OpenXR libs, `zip -T` OK).
    - `BrushifyCountryRoads.z01` = raw episode data zip, **corrupt** (errors on
      `2cd3b36c-d435-4b13-977f-865216d77400/leftcamera/000355.png` — the known-
      bad UUID).
    - `BrushifyCountryRoads.z02` = empty (0 files).
  - Solution: moved `.z01`/`.z02` to `/tmp/opencode/parts/`, extracted `.zip`
    alone with `7z x -y -o. BrushifyCountryRoads.zip` → got full env; moved
    parts back to `envs/`; cleaned up the 10.6 GB joined temp file.
  - Result: `envs/BrushifyCountryRoads/` ready (launcher, binary, pak, Engine).
- **Local server HOST change**: `airsim_plugin/AirVLNSimulatorServerTool.py:696`
  `HOST = '127.0.0.1'` → `'0.0.0.0'` (verified `serve()` line 662 reads global
  `HOST`).
- **Wrote `scripts/split.sh`**: launches local server (nohup), opens `ssh -R`
  reverse tunnels for port `30000..30016` to the H100, then verifies the H100
  can reach `127.0.0.1:30000` through the tunnel. Rationale: tunnel makes the
  local server appear as the H100's own localhost → **no `param.py` change**.
- **H100 progress check**: `eval.log` shows 56/123 (2026-09-08 12:20), still
  RUNNING.

### Problems faced
1. 7z auto-merged `.z01`/`.z02` with `.zip` and gave misleading/conflicting
   listings + partial extractions (only 157 MB binary, no pak).
   - Fix: move `.z0x` files aside → `.zip` alone lists/parses as complete env.
2. H100 cannot reach local directly (tested 10.255.0.2 / 10.0.0.1 /
   192.168.100.25 / 172.17.0.1 — all `no` from H100).
   - Fix: reverse-tunnel approach (local initiates `ssh -R`).
3. Persistent shell echoed stale 7z listing outputs — cosmetic, ignored; used
   fresh commands to verify actual FS state.

### Current state
- Local: env extracted, `HOST=0.0.0.0` applied, `scripts/split.sh` written
  (not yet executable-flaged / not yet run).
- H100: eval running, 56/123.

### Next steps
- `chmod +x scripts/split.sh` + `bash -n` syntax check.
- Verify H100 `run_eval.sh` exists and points at the right eval args.
- When H100 run completes: aggregate `scripts/metric.sh`, pull `eval_results/`.
- Test the tunnel + split eval end-to-end when the user is ready.
- Update `docs/SPLIT_RUNBOOK.md` with the tunnel-based flow (differs from the
  original direct-connect assumption).

### Follow-up (same session, later)
- `scripts/split.sh` → `chmod +x` + `bash -n` = SYNTAX OK.
- **H100 `run_eval.sh` verified** (`/workspaces/AeroVLA/scripts/run_eval.sh`):
  - Runs client via `.venv/bin/python`, `CUDA_VISIBLE_DEVICES=0`,
    `--simulator_tool_port 30000` (i.e., H100-localhost), full arg set.
  - Conclusion: **reusable unchanged for the split** — through the reverse
    tunnel the H100's `127.0.0.1:30000` maps to the local UE4 server, so no
    `param.py`/IP change is needed. The `VK_ICD_FILENAMES=lvp_icd.json` +
    `LIBGL_ALWAYS_SOFTWARE=1` vars in it are server-render-only and harmless on
    the client process.
- Split flow (frozen): `[local] bash scripts/split.sh` → `[H100] bash scripts/run_eval.sh`.
- Eval progress at last check: **56/123**, RUNNING.

## Session 2026-09-09 — Eval results synced + split smoke test VERIFIED + metrics deferred

### Goal
- H100 eval finished → confirm results are local (123 episodes), decide/run
  metric aggregation, and verify the **split** flow end-to-end for fast re-runs.

### What I did
- **Confirmed eval DONE**: H100 run completed 123/123; user synced results into
  `eval_results/checkpoints/seen_valset/BrushifyCountryRoads/` = 123 dirs
  (50 prefixed `success_`, 73 plain) → SR ≈ 50/123 ≈ 40.65%. Each dir has
  `log/` (JSON pose files), `ori_info.json`, `object_description.json`, camera
  dirs (+ `_depth`).
- **Metrics = DEFERRED by user decision** ("let skip this and go on our plan").
  Investigated: `utils/metric.py` needs per-episode GT `merged_data.json`
  (via `ori_info.json` → `ori_traj_dir` `./dataset_raw/...` → simlink →
  `envs/BrushifyCountryRoads/raw/<uuid>/`), but local `raw/` doesn't exist and
  `.z01` is corrupt/unlistable. H100 has full GT tree (62 KB total, small). Not
  done — user said skip.
- **Split smoke test RUN + VERIFIED** (the plan's next step):
  - Fixed `scripts/split.sh` python bug: server launch used bare `python`
    (base conda, missing `msgpackrpc`) → now uses `$SERVER_PYTHON` defaulting to
    `/media/sanati/DriveE/miniconda3/envs/aero_vla/bin/python` (has
    msgpackrpc+tornado+airsim), with a deps sanity check
    (`split.sh` §"Server deps" header, lines ~26-38).
  - Ran `bash scripts/split.sh` → local server up on `0.0.0.0:30000` (pid
    3828439), reverse tunnels opened, `H100 -> 127.0.0.1:30000` = **OK**.
- **Process-detection gotcha**: `pgrep -af "AirVLNSimulatorServerTool\|ssh -o
  ExitOnForwardFailure"` (with `\|`) fails to match → false "DEAD" report. Use
  `ss -ltnp | grep :30000` + `ps aux | grep AirVLNSimulatorServerTool` to confirm.

### Problems faced
1. Base conda `python` has no `msgpackrpc` → `split.sh` server would crash.
   - Fix: use `aero_vla` env python via `$SERVER_PYTHON` (with AEROVLA_SERVER_PYTHON override).
2. Tornado `Uncaught exception` lines in `/tmp/aerovla_server.log` = **benign**
   artifacts of the H100 `/dev/tcp` port-probe closing the msgpack-RPC
   connection mid-handshake; the server stays up (verified listening + alive).
3. Earlier `kill -0`/`pgrep` checks falsely reported the tunnel "DEAD"; live
   test `echo > /dev/tcp/127.0.0.1/30000` from H100 returned H100_REACHES_OK.

### Current state
- Split infra VERIFIED end-to-end: local server (aero_vla python) on
  `0.0.0.0:30000`, reverse `ssh -R 30000..30016` tunnel up, H100 reaches it.
- Smoke-test server + tunnel still running (pids in
  `/tmp/aerovla_server.pid`, `/tmp/aerovla_tunnel.pid`).
- Metrics (CSV aggregation) not generated — deferred.

### Next steps
- To do a full split eval: `[local] bash scripts/split.sh` (already up → skip)
  then `[H100] cd /workspaces/AeroVLA && bash scripts/run_eval.sh` (long-running,
  ~hours; local machine must stay on + tunnel stable).
- Stop the leftovers when done: `kill $(cat /tmp/aerovla_server.pid /tmp/aerovla_tunnel.pid)`.
- Optional: pull GT `merged_data.json` from H100 and run `scripts/metric.sh`
  (after fixing `RESULTS_DIR` = `eval_results/checkpoints`) for SR/OSR/NE/SPL.

## Session 2026-09-09 (cont.) — FULL SPLIT EVAL RUN 1 (VERIFIED WORKING)

### Goal
- User decision: re-run the full 123-episode eval through the split infra
  (local UE4 GPU render + reverse tunnel + H100 model client) rather than the
  20 h CPU-rendered H100 run.
- Root-cause the repeated "silent death" of the eval client and fix it.

### What I did
1. **Msgpack framing bug found + fixed.** Local server `aero_vla` env has
   `msgpack==1.1.2`; H100 client `.venv` had `msgpack==1.2.2`. The encoding
   change breaks msgpack-RPC framing → server crash
   `TypeError: object of type 'int' has no len()` at
   `msgpackrpc/transport/tcp.py:27 on_message` → client silently dies on the
   first real RPC (`ping`) in `_confirmSocketConnection`.
   Fix (applied, verified): on H100
   `cd /workspaces/AeroVLA && .venv/bin/pip install msgpack==1.1.2`.
   Verified real msgpack-RPC `ping` over the tunnel → `True` + no new server
   traceback. **This was a RED HERRING for the silent-exit symptom** (see #2)
   but is a real latent bug worth keeping fixed.
2. **Silent-exit root cause (the actual blocker): nothing to evaluate.** The eval
   client exits silently right after "random shuffle data / dataset grouped by
   scene" when `self.data` is empty. `load_my_datasets`
   (`src/vlnce_src/env_uav.py:122-131`) **skips any trajectory whose seq_name
   already exists in `eval_save_path`**. The H100 `eval_save_path` already had
   123 completed episode dirs (prior CPU run) covering all 123 split entries →
   `self.data` empty → `next_minibatch` returns `None` on first call
   (`env_uav.py:191-194`) → `eval()` breaks → exit 0, no traceback.
   Proof: `eval_json_path` =123 entries, result dirs =123, samples matched.
3. **Backed up prior results** (do not destroy them):
   `mv eval_results/checkpoints/seen_valset/BrushifyCountryRoads
       eval_results/checkpoints/seen_valset/BrushifyCountryRoads.bak_20260909_priorCPU123`
   then created a fresh empty dir.
4. **Permission bug (3rd blocker) found + fixed.** Fresh dir was created by my
   `root` SSH session → `root:root 755`, but eval runs as `ubuntu` → on first
   episode end `save_to_dataset_eval` hit
   `PermissionError: [Errno 13] Permission denied:
   .../oracle_1a535b8e-...` → client crashed (traceback in
   `/tmp/split_eval3.log` lines 14-25 → exit). Fix:
   `chown ubuntu:ubuntu .../BrushifyCountryRoads .../BrushifyCountryRoads.bak_...`.
5. **Relaunch + VERIFIED end-to-end working:**
   - Local infra (all from the original `bash scripts/split.sh`, still up): server
     pid **3861739** `0.0.0.0:30000`, tunnel pid **3861798** (`ssh -R
     30000..30016`), UE4 render pid **3948923** (`BrushifyCountryRoads
     -RenderOffscreen -GraphicsAdapter=0`, GPU 86% util, 2.7 GB VRAM).
   - H100 client: `cd /workspaces/AeroVLA && nohup bash scripts/run_eval.sh >
     /tmp/split_eval4.log 2>&1 &`. Log shows `Connected 127.0.0.1:30000`,
     "开始打开场景" (opening scene), normal per-step navigation.
   - **Results are being written**: `RESULT_COUNT` grew 0 → 7 (and climbing);
     each episode dir has full `log/` (1258 JSON), `frontcamera/` (258 png),
     `downcamera/`, `rightcamera/`, `rearcamera/`, `*_depth/`,
     `object_description.json`, `ori_info.json`. First episode outcome was an
     `oracle_` (model ran all 200 steps). Projected ~5 h for 123 eps at ~2.3
     ep/5 min pace (vs 20 h CPU).

### Problems faced (this session)
1. `msgpack` 1.1.2 vs 1.2.2 framing break (server `tcp.py:27` crash on a real
   RPC). → fix: downgrade H100 venv to 1.1.2.
2. "Nothing to evaluate" silent exit (all split entries already completed). →
   fix: backup old results + fresh empty `eval_save_path`.
3. `PermissionError` on save (root-created eval dir vs ubuntu eval process). →
   fix: `chown ubuntu:ubuntu` the eval dir (+ backup dir).
4. SSH output mangling: the local `grep -a` pipelines were merging the coder
   banner (stderr) with command output. **Lesson: run the remote command with
   stdout>file 2>/dev/null (or separate streams), then read the file** — the
   banner only goes to stderr and stdout stays clean.
5. `nohup ... &` inside a single remote SSH command hung the SSH for the full
   120 s timeout (it waits for the pipe). → wrap with a *local* `nohup ... &`
   of the ssh command, or a local `sleep`+relaunch; check state separately.
6. `scp` from H100 fails (exit 255; coder env has no working sftp) — use
   `ssh remote 'cat file'` with the read-the-stdout-file pattern instead.

### Follow-up (same session): Windowed/visible UE4 → `split.sh --windowed` (IMPLEMENTED)
- User asked: "what if i want to use UE4 with graphical so I can see the scene?"
  then: "i need this option as an argument in the split.sh".
- Verified locally: a real X display `:1` (Xorg pid 8843, HDMI-0 1920x1080) is
  accessible as user `sanati` (uid 1000) → a windowed UE4 will render visibly.
- **Implemented (edits, NOT yet applied to the running session):**
  - `airsim_plugin/AirVLNSimulatorServerTool.py`:
    - New `argparse` flag `--windowed` (default False) in `__main__`.
    - New module-level helper `make_env_launch_cmd(env_path, gpu_id,
      settings_path)` that picks `-windowed -ResX=1280 -ResY=720 -WinX=740
      -WinY=610` vs `-RenderOffscreen` based on `args.windowed`.
    - Both launch sites now call the helper: `_open_scenes` and
      `reopen_scene_from_port`.
  - `scripts/split.sh`: parses `--windowed` (and `-h/--help`), prints the mode,
    forwards `--windowed` to the server command. Usage:
    `bash scripts/split.sh [PORT] [--windowed]`. Offscreen is still the default.
  - Verified: `bash -n` OK; `py_compile` OK; helper produces the exact expected
    command lines in both modes.
- Runbook updated: `docs/SPLIT_RUNBOOK.md` §5.5 now documents the `--windowed`
  argument end-to-end (usage, code refs, mid-run apply notes).
- The running split eval (thread A) is unaffected: it was launched offscreen,
  and `--windowed` only affects processes started after it.

### Follow-up (same session): Killed old eval + WINDOWED RUN LAUNCHED (VERIFIED)
- User: "kill all related process of our evaluation i want to run it by my own"
  → killed local server 3861739 + tunnel 3861798 (no UE4 / H100 client was up);
  removed `/tmp/aerovla_server.pid` + `/tmp/aerovla_tunnel.pid`; port 30000 freed.
- User: "run the evaluation with windows" → clean relaunch:
  - `nohup bash scripts/split.sh --windowed` → local server **4111535**
    `0.0.0.0:30000`, mode WINDOWED, tunnel **4111617** (`ssh -R 30000..30016`),
    H100→`127.0.0.1:30000` = REACH_OK.
  - H100 client: `cd /workspaces/AeroVLA && nohup bash scripts/run_eval.sh >
    /tmp/split_eval5.log 2>&1 &` → model loaded 3/3 shards, Step 33/200, episode
    1/123, `RESULT_COUNT=1` already.
  - **LOCAL UE4 SPAWNED WINDOWED** (pid 4121917):
    `bash ../envs/BrushifyCountryRoads/BrushifyCountryRoads.sh -windowed
    -ResX=1280...` → visible scene on local display `:1`.
- Blockers hit this run (all benign/expected):
  - Tunnel stderr shows the coder "version mismatch" banner → harmless, tunnel
    stays up (kill -0 OK).
  - `cat /tmp/aerovla_server.log` triggered a tornado `Uncaught exception`
    traceback in my shell output only — artifacts of the readiness port-probe,
    server stays up (listening on 30000, pid 4111535).
- State: fresh empty `eval_save_path` (ubuntu:ubuntu) → new 123-ep result set
  from this WINDOWED run replaces the prior CPU one (`.bak` kept).

### Current state
- Split eval **RUNNING** and **writing results** to the fresh H100 path
  `eval_results/checkpoints/seen_valset/BrushifyCountryRoads/` (ubuntu-owned).
- Local server/tunnel/UE4 all up. Prior 123 CPU results safe in
  `BrushifyCountryRoads.bak_20260909_priorCPU123`.

### Next steps
- Monitor to completion: `ssh ... 'ls <eval_save_path> | wc -l'` until 123; watch
  `/tmp/split_eval4.log` and local GPU/nvidia-smi. (First run will now produce
  NON-oracle results too since save works.)
- On completion: the new results REPLACE/SUPERSEDE the prior CPU run as the
  user-chosen dataset; decide whether to keep the `.bak` (disk) — likely delete
  or keep for comparison.
- Metrics still deferred (user decision); `scripts/metric.sh` `RESULTS_DIR` fix
  still open.
- Timeout at 200 steps → Oracle outcome is expected for episodes the model never
  stops on; treat `oracle_` results same as before (SR computation uses its own
  logic).

### Follow-up (same session): ADD_ENV_RUNBOOK written (research + document)
- Goal: user wants to add *another* env to evaluate; produce a reusable runbook
  mirroring exactly how `BrushifyCountryRoads` was integrated end-to-end.
- **Did (research, read-only — no state changed):**
  - Mapped the full pipeline via file reads:
    - `env_exec_path_dict` at `airsim_plugin/AirVLNSimulatorServerTool.py:241`;
      resolve = `os.path.join(root_path, exec_path, bash_name + '.sh')`;
      `--root_path` default `../envs` (~line 704); **prefix matching** for
      unknown map ids (lines 522-526).
    - `make_env_launch_cmd` at `AirVLNSimulatorServerTool.py:443`.
    - spawn-area read `env_uav.py:57`, `find_closest_area` at `:61` (skips rows
      with `len<18`, anchor `[r0+1,r1+1,r2+0.5]`).
    - dataset load `env_uav.py:114-155` (`mark.json` via
      `merged_data.json.replace('merged_data.json','mark.json')`; **skips
      episodes already in `eval_save_path`**; `trajectory` from
      `trajectory_raw_detailed`).
    - **eval uses LIVE AirSim images** for model input (`env_uav.py:358`
      `getImageResponses()` → 5 rgb + 5 depth), NOT `feature.tensor`/preprocessed
      → docs now state `preprocess_image2tensor.py` is training-only.
    - `closeloop_util.py:76-88,:142` object_description lookup
      (`asset_name.replace("AA","")`).
    - Verified spawn-area row layout on disk (18 fields) and that rows are
      generated from `mark.json` (`asset_name==object_name`,
      `place_pos==target.position`): 123-ep Brushify set → 56 rows / 28 assets;
      2/131 episodes don't snap (35-50 m) — benign.
    - Verified H100 layout: `dataset_raw/BrushifyCountryRoads/<uuid>` are
      **symlinks** → `envs/BrushifyCountryRoads/<uuid>`; 123 uuids == split count;
      each uuid = mark.json + merged_data.json + object_description.json +
      11 image/log dirs.
    - Confirmed `scripts/run_eval.sh` (H100) hardcodes Brushify; mirrors
      `scripts/eval_aerovla.sh` param resolution (TASK_ID→TEST_JSON/SAVE_DIR).
    - Re-confirmed the 3 gotchas (msgpack 1.1.2, fresh save dir, chown ubuntu).
- **Wrote** `docs/ADD_ENV_RUNBOOK.md` (NEW, 10 sections): zip extraction guards,
  env_exec_path_dict registration, spawn-area row generation w/ script, H100
  episode placement + symlinks, split json template, run_eval.sh edits, launch
  via `split.sh --windowed`, metrics note (deferred), full checklist, anchor table.
- Problem: `find_closest_area`/spawn-area format not documented anywhere; solved
  by code+data cross-check (row↔mark matching test above).
- Solution: runbook's §3 includes a copy-paste generator from mark.json files.
- State: runbook written; windowed split eval **still RUNNING** on H100
  (checked during research: 25/123 at ~29.7 s/it, `RESULT_COUNT=3`, log
  `/tmp/split_eval5.log`).
- Next: (a) mirror new runbook reference in `docs/PROJECT_HANDOFF.md`;
  (b) when user provides the new env archive, follow the runbook end-to-end;
  (c) continue monitoring windowed eval to 123/123.


## Session 2026-09-10 — Landing a canonical per-env layout (`engine/` + `data_raws/`)

**Goal**: reorganize `envs/` so each env is contained in its own folder, splitting the
UE4 engine (`engine/`) from the raw episode archives (`data_raws/`), and encode that
schema into the server resolver on both the local repo and the H100 repo.

**What I did**
- Confirmed current on-disk layout: `envs/BrushifyCountryRoads/{data_raws,envs}/`
  (Brushify engine still **zipped** — user will extract later); `envs/BrushifyForestPack/`
  has `data_raws/` + an extracted UE4 under `envs/BrushifyForestPack/envs/BrushifyForestPack/`.
- The prior 123-ep split eval server (`/tmp/aerovla_server.pid` 4111535, tunnel 4111617)
  are **dead** — eval is not currently running.
- **Local `airsim_plugin/AirVLNSimulatorServerTool.py`** edits:
  - Added `'engine_dir': 'engine'` to the `BrushifyCountryRoads` entry in
    `env_exec_path_dict` (line ~266-270).
  - New helper `resolve_env_launcher(scen_id)` (after the dict, ~line 307): for
    `engine_dir`-style envs returns
    `os.path.join(root_path, exec_path, engine_dir, bash_name, bash_name + '.sh')`
    → `envs/<Map>/engine/<Map>/<Map>.sh`; else legacy
    `os.path.join(root_path, exec_path, bash_name + '.sh')`.
  - Replaced all 3 resolution sites (was `_open_scenes` get-branch + prefix-branch +
    `reopen_scene_from_port`) with `resolve_env_launcher(...)` (lines ~539, ~547, ~614).
- **Local `scripts/split.sh`**: sanity check now requires
  `${ROOT}/envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/BrushifyCountryRoads.sh`.
- **H100 `/workspaces/AeroVLA`**: applied the identical patch via ssh (`/tmp/patch_h100.py`),
  verified with `.venv/bin/python` that `resolve_env_launcher('BrushifyCountryRoads')`
  → `../envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/BrushifyCountryRoads.sh`
  and Carla entries unchanged.
- Verified local synth-check: Brushify→engine path, Carla_Town01→unchanged, ForestPack→None (not yet registered).

**Problem**
- H100 system python lacks `msgpackrpc` → had to use `/workspaces/AeroVLA/.venv/bin/python`
  for the resolve verification.
- `scp` had failed in past sessions, so used `ssh ... 'cat > /tmp/patch_h100.py && python3 ...'`.

**Solution**
- Patch helper `resolve_env_launcher` centralizes path building; `engine_dir` key is
  backward-compatible (absent → legacy carla/closeloop resolution unchanged).
- Ran `ast.parse` syntax checks + runtime resolve checks on both repos (OK).

**Current state**
- Both repos have the schema; server resolves new-path; split.sh sanity updated.
- Docs updated: `ADD_ENV_RUNBOOK.md` (§1 extraction → `engine/<Map>/`, §2 registry
  `engine_dir`, checklist + anchor table), `PROJECT_HANDOFF.md` (architecture diagram,
  key-data-facts, §7 disk-state, implication note).

**Next steps**
- User extracts `BrushifyCountryRoads` engine into `envs/BrushifyCountryRoads/engine/`
  (no space right now; `BrushifyForestPack` already extracted → move into `engine/`).
- When a new map arrives: follow `docs/ADD_ENV_RUNBOOK.md` (now schema-aware).


## Session 2026-09-10 — Git sync between local + H100 via personal fork

**Goal**: unify the two AeroVLA repos (local + H100 VM) through a personal GitHub fork
`https://github.com/m-amin-sanati/AeroVLA.git` (fork of upstream `XuPeng23/AeroVLA`).

**What I did**
- Inventoried both repos. Both were clones of `XuPeng23/AeroVLA` @ `e37685a` on `main`,
  each with local modifications + untracked files, no commits ahead.
- Local repo: added `.gitignore` rules for big/generated data (`envs/`, `openvla-7b/`,
  `eval_results/`, `evals.zip`, `checkpoints/`, `data/others/*.json`,
  `data/aerovla_train_dataset.json`, runtime logs, `TravelUAV/` vendored subrepo,
  `dataset_raw/`). Committed:
  - `4689151` env-schema + split tooling + docs + split.json
  - `f7272e0` ignore vendored TravelUAV
- Reconciled 2 shared files after user decision:
  - `AirVLNSimulatorServerTool.py`: **default HOST=127.0.0.1**, added optional
    `--host` (use `0.0.0.0` for split/tunnel eval), kept optional `--windowed` +
    `make_env_launch_cmd()`.
  - `aerovla_wrapper_ui.py`: reverted to **upstream pristine** (bf16, `.to(device)`,
    no bnb 4-bit).
  - commit `d7bfa44` (+ pushed to fork ).
- H100 repo: added `fork` remote, discarded its tracked working-tree edits (server
  HOST 127.0.0.1 only + no windowed; wrapper bf16 tweaks were already upstream-equal),
  `git reset --hard fork/main` → synced to fork. Kept H100-only untracked scripts:
  `scripts/{daemon_server.sh, run_eval.sh, run_eval_daemon.py, start_server.sh,
  start_server_daemon.py}`, `smoke_test_model.py`. `dataset_raw/` added to ignore
  (commit `dda2728`, pushed from local).
- Final H100 state: `HEAD = dda2728` = fork/main, clean tracked tree, 6 untracked
  H100-only scripts intact. H100 client uses `127.0.0.1:30000` via reverse tunnel →
  server default bind 127.0.0.1 is correct. Verified both server+wrapper compile on H100.

**Problems**
- H100 `git push fork` prompted interactive GitHub auth (no credential helper/token on
  H100). Workaround: H100 kept no unique content (skip its `9ca0fea` .gitignore commit),
  so all pushes were done from local (which has cached GitHub auth).
- `map_spawnarea_info.json` is huge (~35k lines); diffs look enormous but H100 vs fork
  was byte-identical (0 diff lines).

**Current state**
- Fork `m-amin-sanati/AeroVLA` `main` @ `dda2728` is the single source of truth.
  Local `main` = fork `main`. H100 `main` = fork `main` (tracked clean; untracked
  H100-only scripts preserved).
- Remotes: local + H100 both have `origin` (upstream) + `fork` (personal).

**Next steps**
- None critical. Future work: pull/push via `fork`. To update H100 from a new local
  commit: `git push fork main` locally, then on H100 `git fetch fork && git reset --hard fork/main`.

## Session 2026-09-10 (cont.) — Extracted + verified BrushifyCountryRoads engine locally

**Goal**: unblock local server launch by extracting the BrushifyCountryRoads UE4
engine into the canonical `envs/<Map>/engine/<Map>/` layout so split eval can run.

**What I did**
- Audited `envs/` current state:
  - `BrushifyCountryRoads/`: `envs/BrushifyCountryRoads.zip` = complete standalone
    engine build (launcher .sh + 165M binary + 2.47G pak + Engine/); `data_raws/` =
    3 raw episode parts (.z01/.z02/.zip). Engine NOT extracted before this session.
  - `BrushifyForestPack/`: engine already extracted but nested as
    `envs/BrushifyForestPack/envs/BrushifyForestPack/` (wrong schema path) AND not
    registered in `env_exec_path_dict` → not launchable by server (leaving as-is;
    brushify eval does not need it).
- Extracted engine per `docs/ADD_ENV_RUNBOOK.md` §1:
  `7z x -y -o. ../envs/BrushifyCountryRoads.zip` from `envs/BrushifyCountryRoads/engine/`.
  Result: `engine/BrushifyCountryRoads/` with launcher + binary + pak + Engine/.
  Verified pak = 2,472,379,918 bytes (2.4G, NOT truncated).
- **Problem**: 7z preserved non-exec perms — `.sh` and `Binaries/Linux/BrushifyCountryRoads`
  were `-rw-rw-r--`, so `split.sh` sanity check and `resolve_env_launcher` (exec test)
  failed.
- **Solution**: `chmod +x BrushifyCountryRoads.sh BrushifyCountryRoads/Binaries/Linux/BrushifyCountryRoads`
  and `chmod -R u+X BrushifyCountryRoads/Binaries`. Added the chmod step to
  `ADD_ENV_RUNBOOK.md` §1 so future extractions don't repeat this.
- Verified end-to-end from the server's cwd (`airsim_plugin/`): `resolve_env_launcher('BrushifyCountryRoads')`
  → `../envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/BrushifyCountryRoads.sh`,
  `exists: True exec: True`. `split.sh` launcher sanity check passes. `bash -n split.sh` OK.
- Server default `--root_path ../envs` matches the resolved path.

**Current state**
- `envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/` extracted + executable.
  `envs/` gitignored → no git impact. Disk: 16G→14G free after ~2.5G extraction (fine).
- `split.sh` is now launch-ready (its line-65 sanity check passes).

**Next steps**
- Run the actual split eval when the user wants: local `bash scripts/split.sh [PORT] [--windowed]`,
  then H100 `bash scripts/run_eval.sh`. (This session stopped before launching the server,
  to get user go-ahead.)
- Optionally reorganize ForestPack engine to `engine/` schema + register in
  `env_exec_path_dict` if a ForestPack eval is ever needed (not now).

## Session 2026-09-10 (cont. 2) — Data prep flow + windowed split eval launch

**Goal**: generate the missing `merged_data.json` for every BrushifyCountryRoads
episode and launch the **windowed** split eval (local UE4 server + H100 client).

**What I did**
- **Synced H100 to `bf728b0`** (which added `scripts/prepare_env_data.sh`):
  `git fetch fork && git reset --hard fork/main`.
- **Ran the prep on H100** (`bash scripts/prepare_env_data.sh BrushifyCountryRoads`):
  - `episodes total: 320, missing merged_data.json: 320` → generated all via the
    TravelUAV generator (`generate_merged_json.py --root_dir envs/data_raws --map_list ...`).
  - `still missing after generation: 0`
  - `symlink OK: dataset_raw/BrushifyCountryRoads -> envs/data_raws/BrushifyCountryRoads`
  - Verified 320/320 episodes now have `merged_data.json`; the script's trailing
    "WARNING: no episode with merged_data.json" is a **false alarm** — its `find`
    check doesn't traverse the symlink. `find -L` + `ls -d` both resolve fine.
- **Sanity-checked the exact eval read**: `dataset_raw/<map>/<uuid>/merged_data.json`
  for entry 0 of `seen_valset_splits/BrushifyCountryRoads.json` → **exists: True**,
  mark.json present, 412 frames, instruction text present. All **123/123 split
  entries resolve** (0 missing). `chmod -R ugo+rX envs/data_raws/BrushifyCountryRoads`
  → `ubuntu CAN read merged_data`. `map_spawnarea_info.json` has the map.
- **Launched the split** (user go-ahead): local `bash scripts/split.sh --windowed`
  → server `0.0.0.0:30000`, reverse tunnels `:30000–:30016`, server pid + tunnel pid
  alive, `OK: H100 -> 127.0.0.1:30000 reachable`. Then on H100
  `nohup bash scripts/run_eval.sh > /tmp/split_eval.log &`.

**Problems faced**
1. **Eval crash #1: `ModuleNotFoundError: No module named 'tkinter'`** in
   `src/model_wrapper/aerovla_wrapper_ui.py:9-10` on H100. The pristine upstream
   wrapper imports `tkinter` + `ImageTk` unconditionally, but those are only used in
   commented UI blocks and the H100 venv has no tkinter.
   - **Fix**: wrapped the two imports in `try/except ImportError` (`tk = None;
     ImageTk = None`), committed as `f0be9b2`, pushed to fork, H100 `git reset --hard
     fork/main` → wrapper import OK on H100.
2. **msgpack `TypeError: object of type 'int' has no len()`** entries in local
   server log — confirmed **benign**: they come from the `echo >/dev/tcp` port-probe
   closing mid-RPC in `split.sh`, not from real eval traffic. Verified by a real RPC
   from H100 through the tunnel (`msgpackrpc.Client.call("get_state")` → returned
   `RPCError 'get_state' method not found`, i.e. framing works, method just unregistered).

**Current state**
- Split eval **RUNNING**: H100 `eval_aerovla.py` stepping through navigation
  (`Step: 74 → 81, Completed: 0 / 123` at check), local UE4 window alive+rendering
  (`Binaries/Linux/BrushifyCountryRoads`, etime ~1-2min), server listening
  `127.0.0.1:30000`. Eval reports `Loaded dataset 63` — that's the eval's own
  post-filter count of the 123 split entries (grouping/filtering), expected.
- Result dir: `eval_results/checkpoints/seen_valset/BrushifyCountryRoads` (ubuntu).

**Next steps**
- Let eval finish (~remaining 122 episodes × ~1.5s-2 min each, hours).
- Document this session in SPLIT/AEROVAL_RUNBOOK + PROJECT_HANDOFF; commit+push+sync.
- After completion: pull `eval_results` metrics, write `docs/AEROVLA_EVAL_RUNBOOK.md`
  results section.

---

## Session 2026-09-10 — One-command teardown + independent run_eval.sh + relaunch (session cont.3)

### Goal
Make the split stack fully operable with **single-command teardown**: an independent
`scripts/run_eval.sh` (tracked, standalone-capable) auto-launched by `scripts/split.sh`;
killing either script tears down everything (local server + tunnel + viewer + remote H100
eval) with no manual cleanup. Then relaunch the resumed windowed eval.

### What I did
- `git log 7670988` (prev session): ViewMode Manual + viewer + `--cameras` already pushed.
- **Diagnosed teardown bug**: `kill -TERM <split-pid>` did NOT fire the cleanup trap.
- **Root cause**: split.sh's resident loop was `tail -f /dev/null` (an external command);
  bash defers signal dispatch while a foreground child runs, so TERM to the script pid
  sat until `tail` exited (never). **Fix**: replace with `while true; do wait || true; done`
  — `wait` (builtin) catches TERM immediately and runs the trap. Verified in isolation
  (`/tmp/tsh3.sh`) and end-to-end: `kill -TERM <MAIN_PID>` now stops server+tunnel+viewer,
  ports free, teardown messages logged.
- **Added UE4-orphan kill (cleanup step 1b)**: killing the server left the UE4 scene
  binary alive (`BrushifyCountryRoads` grandchild of `bash <env>.sh`; `pkill -P` misses it).
  Fix: `fuser -k -TERM/-KILL` on scene ports `LOCAL_PORT+1..+16` + `pkill -9 -f settings/<PORT>/`.
  Verified with a fake scene bound to :30012 → killed on teardown.
- **Created `scripts/run_eval.sh`** (NEW, independent): args `PORT`(30000)/`LOG`(/tmp/split_eval.log);
  writes `/tmp/aerovla_eval_<PORT>.pid` on H100 via `su ubuntu -c 'nohup $VENV ... eval_aerovla.py
  ... & echo $! >$PIDFILE'`; cleanup trap on EXIT/INT/TERM; keep-alive wait loop. Command matches
  the validated manual eval invocation. `bash -n` OK.
- **split.sh**: `MAIN_PID=$$`; `--no-eval`; `--cameras`; pid vars; `_CLEANED` double-trap guard;
  clean trap EXIT/INT/TERM; step 5 auto-launches `ssh H100
  "bash /workspaces/AeroVLA/scripts/run_eval.sh <PORT> /tmp/split_eval.log"` and verifies remote
  pidfile; footer `kill ${MAIN_PID}`; pidfile `/tmp/aerovla_split_<PORT>.pid`.
- **camera_viewer.py bug**: `TclError: image "pyimage29" doesn't exist` crash. Root cause: the
  retry loop created a **new Tk root** per attempt; a mid-flight frame error destroyed the Tk
  interpreter while the Label still referenced a PhotoImage from the dead interpreter.
  **Fix**: single persistent Tk window + reconnectable AirSim client inside `_update`
  (set `self.client=None` on error, retry next tick); keep `self.lbl.image` reference; drop
  unused `cv2` import. Verified: viewer alive 2+ min @ ~12% CPU (old one died <30s).
- **run_eval.sh log-permission bug**: `bash: line 2: /tmp/split_eval.log: Permission denied` →
  eval died instantly, "could not confirm remote eval start". Root cause: the log redirect is
  inside `su ubuntu -c`, and `/tmp/split_eval.log` was pre-created root-owned by split.sh's
  ssh launcher. **Fix**: `touch $LOG; chmod 666 $LOG; rm -f $PIDFILE` before the `su` launch.

### Problems faced
- `kill -TERM` swallowed by `tail -f /dev/null` (see above).
- H100 reverse-forward ports 30000-30016 re-bound by a stale test tunnel → cleared before relaunch.
- H100 ssh flaky/noisy (`version mismatch`, banner timeouts) → all ssh wrapped in `timeout`
  + `LogLevel=ERROR` + `grep -v` filter; some ssh calls still hung the 120s tool timeout
  (bounded later with `timeout 30 ssh` + `tail -c 400`).
- Earlier "image request timed out" hang in the first relaunch was a **red herring**:
  it occurred because the eval failed at launch (log Permission denied → eval never really
  ran; the `1 / 84` progress line was from a prior stale log block). Once the log-permission
  fix landed, the real run shows **0 image timeouts**.

### Current state (IMPORTANT — live)
- **Split eval RUNNING** (real, healthy), port 30000 windowed + cameras:
  - Local split `1311614`, server `1311646`, tunnel `1311751`, UE4 scenes alive
    (3× `BrushifyCountryRoads`), ports 30000 (RPC)+30001 (scene) bound.
  - Camera viewer **1366278** running with the fixed single-window client.
  - H100 eval pidfile `/tmp/aerovla_eval_30000.pid` → pid `2028537`; log
    `/tmp/split_eval.log`; **`Completed: 34 / 82`** at 13:33 windowed, actively navigating,
    0 image timeouts. Resume works (skips prior result dirs; 82 remaining of 123).
  - To stop everything: `kill $(cat /tmp/aerovla_split_30000.pid)` (i.e. 1311614).
- Commits pushed to fork `main`: `398ac2b` (run_eval.sh+split.sh teardown fixes),
  `9fa1637` (log perm fix), `7a45cdb` (viewer single-window fix). H100 synced to `9fa1637`
  before launch (viewer fix `7a45cdb` is local-only so far — H100 doesn't need it for the
  running eval; sync it when next touching H100 scripts).

### Next steps
1. Let eval finish (34/82; ~remaining × ~1.5-2 min/ep ≈ 1.5-2 h more at current pace).
2. After completion: pull `eval_results/checkpoints/seen_valset/BrushifyCountryRoads`
   metrics, run `bash scripts/metric.sh`, update `docs/AEROVLA_EVAL_RUNBOOK.md` results.
3. On next H100 script touch: `git fetch fork && git reset --hard fork/main` to bring
   `7a45cdb` (viewer) onto the H100.
4. Update `docs/SPLIT_RUNBOOK.md` with the one-command teardown + independent run_eval.sh.

---

## Session 2026-09-10 — View mode change: manual -> chase cam (session cont.4)

### Goal
User wanted to "freely move" in the main UE4 window; explained that's impossible
during a run (eval drives the drone pose every step + KeyboardInput is None). User
then asked to switch from Manual view to the original chase view, and to list the
view options.

### What I did
- Diagnosed: the running scene on port **30001** launched with `ViewMode: Manual`
  (free-cam). Ports 30002/30003 already used `SpringArmChase`.
- Checked the airsim python client API for a runtime view switch: only
  `simSetCameraPose`/`simSetCameraFov` exist — **no runtime `ViewMode` toggle**;
  ViewMode is baked into `settings.json` at UE4 launch.
- **Changed `airsim_plugin/settings/30001/settings.json` → `SpringArmChase`**
  (copied the proven 30002 chase config; kept `ApiServerPort: 30001`, `ClockSpeed: 10`).
  Committed `b59fc25` + pushed to fork.
- **Did NOT touch the live run** (UE4 reads settings at launch only): eval still
  healthy at `Completed: 64 / 82` when checked (7a07z → 14:07z).
- Documented view modes in `docs/SPLIT_RUNBOOK.md` §5.5 + `AGENTS.md` §6.

### Solved
- View options (this AirSim build): `SpringArmChase` (chase/follow cam — the
  original view), `Manual` (free cam), `NoDisplay` (no viewport).
- Next `split.sh --windowed` launch automatically gets the chase cam.

### Known
- During a run the eval sets the drone pose every step → no keyboard flight even
  in chase cam; the window is a watch-debug view. Manual flight = kill eval +
  drive via AirSim API.

### Next steps
- Let current run finish (`Completed: 64 / 82`); on next launch the chase cam is
  active. When the run completes, pull metrics + `scripts/metric.sh`.

---

## Session 2026-09-10 — Stopped eval / teardown (session cont.5)

### Goal
User asked to stop the eval process.

### What I did
- `kill $(cat /tmp/aerovla_split_30000.pid)` (= 1311614) — one-command teardown.
- Verified: local server (1311646), tunnel (1311751), viewer, UE4 all gone;
  no 300xx ports bound; split log shows `==> teardown complete.`
- Confirmed H100 eval dead: pidfile `/tmp/aerovla_eval_30000.pid` removed,
  process gone.

### Problems / notes
- H100 log tail shows the eval's final line is a multiprocessing shutdown
  artifact (`AttributeError: 'NoneType' object has no attribute 'pack'` in
  `send_bytes`) from being killed mid-send — expected on kill, not a real error.
- Run ended at `Completed: 64 / 82` (resume work preserved in result dirs).

### Next steps
- Results dirs on H100: `eval_results/checkpoints/seen_valset/BrushifyCountryRoads`
  — 64 completed episodes this run (plus prior partials). Pull metrics +
  `scripts/metric.sh` when desired.
- Next launch uses `SpringArmChase` view (commit `b59fc25`), and will resume
  skipping already-evaluated episodes.

---

## Session 2026-09-11 — Prep BrushifyForestPack end-to-end (session 1)

### Goal
Prepare the whole BrushifyForestPack env for eval: fold ALL prep steps into one
parameterized `scripts/prepare_env_data.sh [MAP] [CATEGORY]` so any future env has
a one-command prep.

### What I did
- Verified local ForestPack engine at canonical `envs/BrushifyForestPack/engine/
  BrushifyForestPack/` (launcher `.sh` + 158M binary + 2.79G pak); `chmod +x` both.
- Registered `BrushifyForestPack` in `env_exec_path_dict`
  (`airsim_plugin/AirVLNSimulatorServerTool.py`), commit `61d4283`.
- Parameterized `scripts/run_eval.sh` with `AEROVLA_MAP` (commit `6412237`).
- **Rewrote `scripts/prepare_env_data.sh`** to a 5-step parameterized pipeline:
  1. merged_data.json generation (missing only), 2. spawn-area rows regeneration
  from mark.json, 3. object_description coverage check, 4. split-json creation
  (valid eps only), 5. dataset_raw symlink + `find -L` sample verify.
  Commits: `80b4bc9` (spawn step) → `7a6399d` (full pipeline) → `df2a4ce`
  (fix `find -L`). Args `[MAP] [CATEGORY=seen_valset] [PYTHON]`.
- H100: synced fork; ran the full prep; 444/446 valid episodes in split (2 corrupt
  excluded), 130 spawn rows, object-desc coverage 45/45.
- Data files (spawn + split) committed locally as `a0759ef` and pushed to fork
  (H100 git push needs browser auth → pull via base64-over-ssh, verify md5, commit
  locally). Both repos now on `a0759ef`, clean.

### Problems
- `find -type d` does NOT descend through the `dataset_raw/<Map>` symlink →
  false "no valid episode found" WARNING. **Solution:** use `find -L` in the
  sample check (verified in isolation + on H100).
- H100 `git push fork main` fails (coder needs browser auth:
  `Open the following URL to authenticate with Git`). **Solution (git-sync):
  commit+publish data/script changes from the LOCAL box; H100 stays read-only for
  pushes and does `git reset --hard fork/main`.**

### Current state / next steps
- Prep complete & verified on both boxes. **Next: launch eval**
  (local `bash scripts/split.sh --windowed`; H100 `AEROVLA_MAP=BrushifyForestPack
  bash scripts/run_eval.sh`; 444 eps). See runbook §5.5 for chase-cam view.

---

## Session 2026-09-12 — R&D: LiDAR sensor on AeroVLA drone (verified live)

### Goal
R&D task from user: add a **LiDAR sensor** to the AeroVLA drone (AirSim/UE4) and
**verify** `getLidarData` actually returns real points from the running
BrushifyForestPack UE4 engine. Research-first (AirSim docs as reference). ForestPack
eval launch remains the user's pending action.

### What I did
- **Researched AirSim LiDAR schema** (official docs,
  https://microsoft.github.io/AirSim/lidar/ ): `SensorType: 6`, `Enabled`,
  `NumberOfChannels`, `Range`, `PointsPerSecond`, `RotationsPerSecond`,
  `HorizontalFOVStart/End`, `VerticalFOVUpper/Lower` (deg, NED), `X/Y/Z` +
  `Roll/Pitch/Yaw` (relative to vehicle), `DataFrame`
  (`SensorLocalFrame`/`VehicleInertialFrame`), `DrawDebugPoints`. Client API:
  `getLidarData(lidar_name='', vehicle_name='')` → `LidarData(.point_cloud flat
  [x,y,z]×N, .time_stamp, .pose, .segmentation)`. Confirmed present in BOTH
  local (`aero_vla` py3.10) and H100 (`.venv` py3.12) airsim clients.
- **Server template edit** `airsim_plugin/AirVLNSimulatorServerTool.py`
  `AIRSIM_SETTINGS_TEMPLATE["Vehicles"]["Drone_1"]["Sensors"]`: added `"Lidar1"` —
  16 ch, Range 100.0, 100000 PPS, 10 RPS, HFOV ±90°, VFOV −5..−35 deg,
  `SensorLocalFrame`, `DrawDebugPoints: False`. (Template propagates
  automatically to the per-port `settings/<port>/settings.json` at scene launch.)
- **Client capture** `airsim_plugin/AirVLNSimulatorClientTool_AeroVLA.py`:
  added `Lidar(BaseSensor)` class (`retrieve()` → `getLidarData('Lidar1')` →
  point_cloud/time_stamp/pose/segmentation), wired into BOTH `getSensorInfo()`
  (init state) and `move_path_by_actions` (per-step) as `{'sensors': {...,
  'lidar': l_info}}`. Backward-compatible extra key.
- **Live verification**: launched local server (G0, port 30000) + reopened
  BrushifyForestPack scene (port 30001); probe connected, enabled API control,
  armed, unpaused, ticked (~30 s). `getLidarData('Lidar1')` returned **0 points**
  while the drone sat at the template spawn (Z≈1248, global-NED — far from
  geometry). After `simSetKinematics` teleport to `(40, 20, -10)` + `simContinueForFrames`
  ticking → **25 real points** (flat [x,y,z] × 25, sane timestamp). `default`
  unnamed lidar name also resolves to `Lidar1`. **VERIFIED WORKING.**
- Cleanup: closed the scene (server RPC `close_scenes` → True, UE4 gone, 30001
  closed), killed test server (pidfile), removed /tmp logs.
- Confirmed eval spawn uses episode `trajectory[0]['position']`
  (`src/vlnce_src/env_uav.py:298-299`) — near-ground dataset poses, NOT the
  template's high-altitude default → real lidar hits in real evals. Per-frame
  lidar is serialized by `save_logs` (`closeloop_util.py:58-63`) into
  `log/000000.json` as part of `{'frame', 'sensors'}`.
- Found + fixed a **dangling template inconsistency**: HEAD template had
  `"ViewMode": "Manual"` while committed `settings/30001-30002/settings.json`
  have `SpringArmChase` (prior `b59fc25` changed only the generated files → next
  regeneration would silently revert chase cam). Working tree already had the
  template fix to `SpringArmChase`; included it in this commit.

### Problems faced / solutions
1. **0 lidar points at spawn** — sim paused/idle with drone at template high
   spawn far from geometry. **Fix**: `simPause(False)` + `simContinueForFrames`
   ticking + teleport to a geometry-rich near-ground pose → 25 pts.
2. **Probe crash on `.close()`** — `airsim.MultirotorClient` has **no `close()`
   method** (`AttributeError` at script end), which skipped scene teardown once.
   **Fix**: dropped `.close()`; torn down via explicit server RPC `close_scenes`.

### Current state
- Local working tree: LiDAR edits + ViewMode template fix (uncommitted at session
  start) → **committed** this session (see commit). All test processes cleaned up.
- Eval-side sensor loop: lidar now captured at init + every step; lands in
  `save_logs` JSON output. Real evals spawn near-ground → non-empty scans.
- `sentinel`: point cloud size per frame is up to 16ch×~100m; `save_logs` dumps
  full JSON each frame → eval log dir grows (acceptable for R&D; consider
  point-cloud downsampling / opt-out if it becomes too large).

### Next steps
- Sync to H100: local `git push fork main`, then on H100
  `git fetch fork && git reset --hard fork/main`.
- If user wants, run a short live lidar probe on ForestPack again or launch the
  pending ForestPack eval (user action) — lidar now flows into eval `log/` output.
- Update `docs/PROJECT_HANDOFF.md` + AGENTS.md §6 (done this session; verify
  after push).
