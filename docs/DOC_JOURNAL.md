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

---

## Session 2026-09-12 (part 2) — Live view verification on ForestPack + simGetImages bug hunt

### Goal
- Verify the launched UE4 ForestPack scene **renders** the three views: UE4 chase-cam
  main/user window, the `scripts/multiview.py` window (drone cameras + lidar top-down).
- Cross-check the carried-over eval launch readiness (user action pending).

### What I did
- Confirmed live stack: server (AirVLNSimulatorServerTool) pid, UE4 BrushifyForestPack
  windowed 1280x720 at (+517,+164) on `:1`, multiview viewer — all running; drone parked
  at `(150,150,-30)` → lidar 4974-4981 pts.
- Diagnosed + fixed a **camera-capture bug** that broke `simGetImages` (plural).

### Problems faced / solutions
1. **`simGetImages` RPCError for all 5 cameras** while physics + lidar worked, and
   `simGetCameraInfo` worked. Persisted across fresh UE4 restarts, reset, re-arms,
   spawn/pose changes, and even after a full server+scene restart.
   - Root causes (two stacked): **(a)** I relaunched the server from **project root**,
     but the tool's `--root_path` defaults to `../envs` (relative) → `reopen_scenes`
     "succeeded" (returned `[True,[...]]`) yet **silently skipped spawning UE4**
     (`p_s.append(None)` path) because `../envs/...` didn't resolve. **Fix**: launch the
     server with CWD = `airsim_plugin/` so `../envs` → `AeroVLA/envs` (see AGENTS §6
     cmd); verified UE4 then spawned (pid 1806583).
   - **(b)** Even with UE4 up, **`simGetImages` (list-request API) throws
     `RPCError: ... not derived from std::exception`**, but **`simGetImage(cam,
     ImageType)` (singular, returns PNG bytes) works** for all 5 cameras (256x256).
   - **Fix**: patched `scripts/multiview.py` to use `simGetImage` per camera
     (`fetch_drone_cams`, `fetch_scene` → `_png_to_ndarray`) instead of `simGetImages`.
     Verified: viewer log empty (no recovery/errors), window renders main view (std ~89),
     lidar panel (~215 mean), camera grid (std ~84) on the right half of `:1`.
2. **Server RPC freeze mid-test**: wedged handler (wchan futex) after a stuck
   `reopen_scenes`; entire single-threaded server became unresponsive. **Fix**: full
   kill/relaunch from correct CWD; use client `timeout=` < server timeout so the shell
   never hangs on RPC.

### Current state
- Working: server (pid 1806163) in `airsim_plugin/` (CWD-correct), UE4 ForestPack
  windowed (:1, chase cam), multiview viewer (pid 1809895) with singular-API camera
  fetch, drone at `(150,150,-30)` with live lidar 4981 pts.
- `scripts/multiview.py` modified (simGetImage); syntax-checked; NOT yet committed.
- Settings `airsim_plugin/settings/30001/settings.json` left in template-regenerated
  state (Lidar1 + SpringArmChase); backup `settings.json.lidar-backup` = same content.

### Next steps
- Confirm 60s uptime of multiview (tick + log) then report to user.
- Commit multiview fix (and note CWD gotcha) + push to fork, sync H100.
- Update AGENTS.md §6 (CWD gotcha + simGetImage) + PROJECT_HANDOFF §12.
- Pending user action: launch ForestPack eval (split.sh + H100 run_eval.sh).

---

## Session 2026-09-12 (part 3) — Drone keyboard flyer (`drone_keyboard.py`) + pose-control research

### Goal
- Let the user actually fly the drone manually (arrow keys in the UE4 window only
  move the chase cam, since the sim is API-driven, not keyboard-driven).

### What I did
- Wrote `scripts/drone_keyboard.py`: a tkinter window that maps WASD/arrows to
  movement, R/F to up/down, Q/E to yaw via `simSetKinematics` every 30 ms tick.
- Discovered two hard requirements for moving the drone sanely on this build.
- Launched it live (pid 1861931, window 560x300 at +50+119 on `:1`); verified
  a controlled move = exactly speed*dt distance, z stable.

### Problems faced / solutions
1. **`simSetKinematics(pose, ignore_collision=True)` makes the drone NON-solid →
   it free-falls/sinks through the map forever.** Symptom: drone z climbed 1.5 →
   111 → 159 → 396 over seconds even idle; lidar/cam still "work" but it's below
   ground. **Fix**: use `ignore_collision=False` so it rests on terrain.
2. **Even with collisions on, the drone inherited the stored fall velocity** (z
   kept moving when the sim was unpaused). **Fix**: pass a full `airsim.KinematicsState`
   (position + orientation + `linear_velocity/angular_velocity = (0,0,0)`) to
   `simSetKinematics`, NOT a bare `Pose`.
3. **AirSim auto-pauses when the drone rests on the ground** (this build +
   ExternalPhysicsEngine). If the flyer unpaused on idle ticks, the stored
   velocity made it drift. **Fix**: flyer only unpauses while a movement key is
   held; idle does nothing.

### Current state
- `scripts/drone_keyboard.py` (new, uncommitted): WASD/arrows=RFT/QE, space=up,
  c=down, P toggle pause, +/- speed, Esc exit. Uses `KinematicsState` +
  `ignore_collision=False`, only moves when a key is held.
- Drone parked at ForestPack spawn `(-86.1, 283.5, -10.1)`, sim paused, stable.
- Flyer running (pid 1861931). Multiview (pid 1833129) + UE4 (1822194) + server
  (1821614) all up.

### Next steps
- Let user fly. If they want a visible drone-moving test, press keys in the flyer
  window and watch engine chase cam + multiview cameras/lidar update.
- (Optional) commit multiview + drone_keyboard + docs, push to fork, sync H100.
- Update AGENTS.md §6 + PROJECT_HANDOFF §12 with the KinematicsState + collision
  + auto-pause gotchas (below).

---

## Session 2026-09-12 (part 4) — F/up didn't work → root cause + real fix

### Goal
- User reported "when i try to go up with f it can't".

### Diagnosis
- Old `drone_keyboard.py` used `simSetKinematics` (teleport). Testing proved this
  build's drone is a REAL multirotor relying on motor control; teleporting cannot
  beat gravity, so vertical commands (both signs) produced downward drift/free-fall
  (z went to +4000). "F can't go up" = the teleport approach is fundamentally broken
  for altitude, not a key bug. (The earlier AGENTS.md gotcha claiming
  `KinematicsState`+`ignore_collision=False` fixes drift was WRONG — that still
  doesn't fly; the real flight is motor-control.)

### Solution
- Rewrote `scripts/drone_keyboard.py` to use the SAME motor-control API the eval
  uses (`AirVLNSimulatorClientTool_AeroVLA.py` `move_path_by_actions`):
  `enableApiControl(True)` + `armDisarm(True)` + non-blocking `moveByVelocityAsync`
  per 30 ms tick + `rotateToYawAsync` for Q/E. NED: `vz<0` = up (R/Space), `vz>0` = down (F/C).
- Verified live: `moveToZAsync(-20)` climbed z −9.3 → −19.0; non-blocking
  `moveByVelocityAsync(0,0,-3,1.0)` climbed −19.1 → −21.7 and returned in 0 ms (no hang).
- Recovering a wrongly-fallen drone: freeze with `KinematicsState` zero-velocity +
  `simSetKinematics(..., ignore_collision=True)` + `simContinueForFrames(2)` +
  `simPause(True)` (same as eval `setPoses`), then unpause and fly.
- Do NOT `.join()` flight commands in a UI tick (hangs); use fire-and-forget.

### Current state
- `scripts/drone_keyboard.py` rewritten (motor-control). Running pid 1929914,
  drone at z≈−14.5 altitude, sim unpaused, flyer + multiview windows up.
- Old gotchas in AGENTS.md §6 + PROJECT_HANDOFF §13/§13 need correcting (next).

### Next steps
- Correct the stale simSetKinematics docs (AGENTS.md §6 gotcha, PROJECT_HANDOFF).
- Let the user try R (up) / F (down) again.

## Session 2026-09-12 (part 5) — even after motor-control fix: drone sinks, can't hover

### Problem
- With the motor-control flyer working, freeing a vertical key still lost altitude:
  `moveByVelocityAsync(0,0,0)` (hover-by-zero-velocity) does NOT hold altitude on
  this build — measured steady sink of ~0.27 m/s (z −16.6 → −14.1 over 5.4 s).

### Solution
- Use `hoverAsync()` when idle (moves → one hover tick holds). Measured clean:
  climb vz=−3 → −14.5→−23.2 in 3s; then hoverAsync 4s → drift 0.00 m. Perfect.
- Flyer `_apply()` rewritten: `moving` flag; if moving → non-blocking
  `moveByVelocityAsync(vx,vy,dz,duration=0.1,MaxDegreeOfFreedom,yaw_mode=current)`;
  if idle → `hoverAsync()`.

### Gotchas
- A live flyer sending hoverAsync every 30 ms WILL defeat standalone test scripts
  (each hover cancels your test velocity command) — kill the flyer (pid in
  /tmp/aerovla_flyer.pid) before running headless flight tests, restart after.

### Current state
- Flyer pid 1943022 (hover fix), drone airborne and holds altitude. Uncommitted:
  `M scripts/drone_keyboard.py`, `M AGENTS.md`, `M docs/*`.

### Next steps
- User to test R=up / F=down then release → should hold altitude.
- Commit + push fork + sync H100 when user is happy.

## Session 2026-09-12 (part 6) — lateral "cycles a lot" on release: stuck arrow keys

### Problem (user report)
- After moving left/right then releasing, the drone "hover and cycle a lot", doesn't
  stop immediately.

### Diagnosis
- Clean headless test (flyer stopped): after left 1.5s + hover, lateral position
  stabilized within ±0.15 m and v within ±0.15 — hover itself was fine.
- Real culprit in the flyer script: the dedicated per-key bindings for arrows/space
  (`<Up> <Down> <Left> <Right> <space>` → `_on_key_guard` → `_press` only ADDED the
  capitalized keysym like `"Left"` to `self.keys`) and there was NO release handler
  for them. The generic `<KeyRelease>` strips only the lowercase form. So an arrow
  key stayed `"Left"` in `self.keys` **forever** → `_apply` kept commanding lateral
  velocity every tick → drift + hover fighting it = the "cycle".
- Also dropped `+`/`-` speed controls that were only wired via those broken bindings.

### Solution
- Removed the broken per-key bindings and `_on_key_guard`/`_press`. Now a single
  generic `<KeyPress>`/`<KeyRelease>` handler (`_on_key`, keysym.lower()) manages
  add/remove for ALL keys (arrows included). BUG-FIX:
  `scripts/drone_keyboard.py` (removed bind lines 77-79, guard/press methods;
  `_on_key` handles both press+release).
- Re-added `+`/`-` speed up/down in `_apply` via keysym (single-step, removed after
  use like `p`).

### Current state
- Flyer pid 1948191 (fix live, window on :1).

### Next steps
- User re-test: press Left/Right (or W/A/S/D), release → drone should stop
  immediately (hover holds now that key actually clears).
- Commit + push fork + sync H100 when satisfied.

## Session 2026-09-12 (part 7) — WASD jitter (step-like) + lidar direction

### Problem
- User: W/A/S/D motion is jittery / step-by-step, not smooth.
- User: "what direction does lidar capture points?"

### Jitter diagnosis & fix
- `moveByVelocityAsync(..., duration=0.1)` re-sent every 30ms tick → 100ms velocity
  bursts separated by controller decel gaps → visible stepping.
- Fix: `duration=0.5` (still refreshed every 30ms) so the controller holds a
  continuous velocity = smooth glide. Also lowered default `--speed` 5→3 m/s.
  `scripts/drone_keyboard.py:167-180` (+ argparse default line 208). Flyer pid
  1955136.

### Lidar direction (verified live, SensorLocalFrame)
- Config (`AirVLNSimulatorServerTool.py:236-252`): 16ch, Range 100m, 100k PPS,
  10 rot/s, HFOV ±90°, VFOV −5..−35°, zero mount rot, output in body/local frame.
- Live sample (exists 4490 pts): all point z > 0 (min 0.0, mean +4.7, max 22) in
  local frame (NED, +z=down) → the cone points DOWNWARD-only (nothing above
  horizon). Effectively: downward-looking, forward-tilted fan that spins 10 Hz
  around drone vertical axis; covers ground ahead-of and below drone, never above.
- So: lidar captures the terrain/objects BELOW and slightly AHEAD of the drone in
  whatever direction the drone is yawed at.

## Session 2026-09-12 (part 8) — yaw broken + lidar FOV to 0°..90° vertical

### Problem (user reports)
1. Yaw (Q/E) no longer works after the jitter fix.
2. Want lidar vertical FOV changed from −5°..−35° (downward fan) to 0°..90°
   (horizontal front → straight-down bottom).

### Yaw root cause & fix
- `_apply` called `rotateToYawAsync(target)` then IMMEDIATELY `moveByVelocityAsync(..., yaw_mode=YawMode(is_rate=False, yaw_or_rate=current_yaw))`.
  The velocity command's YAW-HOLD override cancelled the rotation every tick → never turns.
- Fix (`scripts/drone_keyboard.py:168-180`): drive yaw INSIDE the velocity command —
  `yaw_mode` = rate mode (`is_rate=True, yaw_or_rate=d_yaw`) while turning, else hold
  current yaw. One command per tick, no fight.

### Lidar FOV change (verified live)
- Template `AirVLNSimulatorServerTool.py:245-246`:
  `VerticalFOVUpper: 0.0`, `VerticalFOVLower: -90.0` (0° front/horizontal → 90° straight down).
- Settings are baked at env launch (server `_open_scenes` writes
  `airsim_plugin/settings/<port>/settings.json` from template, lines 577-584, then
  launches UE4 with it). Server does NOT respawn UE4 on start → must call
  `reopen_scenes`/`_open_scenes` client RPC to regenerate + relaunch.
- Full restart: killed server+UE4+flyer+multiview, relaunched server (CWD
  `airsim_plugin/`), called `reopen_scenes('127.0.0.1',[('BrushifyForestPack',0)])`,
  relaunched flyer+multiview.
- Verified: settings/30001/settings.json now `VerticalFOVUpper:0.0 / Lower:-90.0`,
  UE4 launched with it, 4988 pts near geometry (Drone had spawned at
  (0,0,12004) → 0 pts; moved to (-86.12,283.52,-11.13) → 4988 pts, z 0..7).
- Render: fix +45° lidar top-down in `scripts/multiview.py`.

### Current state
- Server pid 1966943, UE4 3 pids (~1969720/21/28), flyer 1975892, multiview 1975894.
- Settings regenerated with 0°→90° lidar FOV.

### Next steps
- User to test Q/E yaw (should turn smoothly now) and re-sample lidar at a
  location where steep near-vertical geometry exists to see the 0°..90° sweep.

## Session 2026-09-12 (part 9) — lidar half-sphere (360° horizontal)

### Change
- Reinforced the 0°→90° vertical fan to a full DOWNWARD HALF-SPHERE:
  `HorizontalFOVStart: -180.0`, `HorizontalFOVEnd: 180.0` (template
  `AirVLNSimulatorServerTool.py:243-244`), keeping
  `VerticalFOVUpper: 0.0 / Lower: -90.0`.
- Applied live: full stack restart + `reopen_scenes` regen; verified baked in
  `settings/30001/settings.json` (`-180.0 .. 180.0`).

### Verified (live sample at (-86.1,283.5,-11.1))
- 10161 pts; x min=-61..max=24, y min=-4..max=97 → all directions.
- Per-quadrant: front 5298 / back 4863 / right 4828 / left 5333 → uniform 360°.
- z min=0 (horizon) .. max=7.2 (below) → full vertical sweep to straight down.
- => downward half-sphere confirmed.

### Gotcha (repeated)
- Drone spawns high (0,0,~900-12000) on fresh UE4 launch → lidar 0 pts. Teleport
  to near-geometry spot (-86.12,283.52,-11.13) before sampling.

### Current state
- Server pid 1988533, flyer 1989751, multiview 1989753 (all `--windowed`, :1).
- Templates: HFOV ±180°, VFOV 0..-90°.

### Next steps
- User validates multiview top-down lidar pane shows circular footprint.
- Commit + push fork + sync H100 when user is done testing.

## Session 2026-09-12 (part 10) — spawned vehicle objects in ForestPack (test)

### What
- Added vehicle objects into the live ForestPack sim via the eval's own
  `simSpawnObject` path (`AirVLNSimulatorClientTool_AeroVLA.py:513`).
- `air_sim_client_script`: spawned
  - `my_object_0` = `AASM_FiretruckParked` @ (-86.1, 283.5, -9.0)
  - `my_object_1` = `AASM_LincolnParked` @ (-71.1, 283.5, -11.0)
  both `physics_enabled=False`, scale 1.
- Verified: `simGetObjectPose('my_object_1')` returns (-71.1,283.5,-11.0);

### Key facts learned
- Asset catalog: `data/meta/object_description.json` (94 entries incl. vehicles
  SM_AudiA2, SM_EtronParked, SM_Cybertruck, SM_TeslaM3_parked, SM_Harley, etc.).
- Spawn spot data: `data/meta/map_spawnarea_info.json` — row layout:
  idx [0..2]=area min, [3..5]=area max, [6..8]=?, [9..11]=object xyz,
  [12..15]=quaternion (w,x,y,z), [16]=asset_name, [17]=scale.
- **Brushify maps use `AA` prefix**: asset `AASM_*` (e.g. AASM_FiretruckParked);
  Carla maps plain `SM_*`. Eval strips `AA` at env_uav.py:143, but the live
  simSpawnObject needs the full name.
- ForestPack spawn spot row0 == drone spawn (-86.12, 283.52, -11.13).

### Problem
- After spawning + simContinueForFrames + simPause(True), drone ended up at
  (321,445,-0.2) (drifted far); cause not fully chased (flyer hoverAsync may
  fight, or physics after unpause). Not blocking.

### Next
- User to confirm vehicles render in multiview front/down cams.
- If useful: `setObjects` in the client already takes object_list with
  asset_name/pose/scale — eval-integratable.

## Session 2026-09-12 (part 11) — real-time multiview + BottomCamera + 5x lidar

### User request
Cameras must refresh in real time; add a bottom camera; increase lidar points.

### Changes
1. **Lidar density ↑** (`AirVLNSimulatorServerTool.py:265-267`):
   `NumberOfChannels` 16->32, `PointsPerSecond` 100000->500000 (5x).
   Verified: 50868 pts at forest spot (was ~10k).
2. **BottomCamera added** (`AirVLNSimulatorServerTool.py:172`): straight-down
   (Pitch -90), 512x512, scene+seg. Verified: returns 595KB scene image.
3. **Multiview real-time** (`scripts/multiview.py`, rewritten):
   - `WorkerPool`: 8 dedicated AirSim clients (one per RPC worker) +
     ThreadPoolExecutor; main/5 cams/bottom/lidar fetched in PARALLEL each frame,
     no single camera blocks the window.
   - Tick 60ms -> 40ms; `_schedule` re-arms every frame; per-future timeout so a
     stalled cam can't hang the frame.
   - New dedicated **BOTTOM** panel (row 1, right column) fed by BottomCamera.
   - Layout: left MAIN | right col [LIDAR, BOTTOM, 5-CAM grid].

### Restart applied (settings baked at launch)
- torn down server+UE4+flyer+multiview -> relaunched server (CWD airsim_plugin/,
  pid 2038343) -> reopen_scenes -> flyer (2043363) + multiview (2043365).
- Verified baked settings: NumberOfChannels 32, PointsPerSecond 500000,
  BottomCamera present. Drone spawned high (0,0,674) again -> teleported to
  (-86.12,283.52,-11.13) for the lidar sample.

### Gotchas
- High spawn on fresh UE4 launch (0,0,~600-12000) -> lidar 0; teleport near geometry.
- BottomCamera REQUIRES the settings regen + UE4 restart (camera list baked at launch).

### Next
- User to eyeball the fresh multiview (bottom pane + denser lidar cloud).
- Commit + push fork + sync H100 after user sign-off.

## Session 2026-09-12 (part 12) — Mission Console (Option A) + manual takeover

### Goal
Replace the active 6-camera panel during a running eval (`camera_viewer.py`,
launched by `split.sh --cameras` as 6-cell FRONT+DOWN/LEFT+RIGHT/REAR) with the
consolidated **mission console**: per-episode TARGET box + FRONT + BOTTOM + LIDAR
+ telemetry + AUTO/MANUAL takeover (M). User approved Option A and said we can
kill the running eval.

### What I did
1. **Confirmed active panel**: `split.sh 30000 BrushifyForestPack --windowed --cameras`
   (pid 2095490, 34 min in) → server 2095516 + `scripts/camera_viewer.py 30001`
   (pid 2095810) — that's the "6 camera view". `multiview.py` 2067889 was stale
   (prior session, unrelated). H100 eval live: `Completed: 6 / 444`.
2. **Killed the eval** per user OK: `kill $(cat /tmp/aerovla_split_30000.pid)` →
   local server/UE4/tunnel/viewer + H100 eval all down (ports free, H100 pid gone).
3. **New `scripts/mission_viewer.py`** (untracked) — consolidated console:
   - TARGET box (polls `/tmp/aerovla_target.json` local-first, else ssh cat H100)
   - FRONT (drone view) + BOTTOM (BottomCamera) + LIDAR top-down (height colored,
     magenta crosshair at target x/y)
   - telemetry line (pos/speed/mode/paused, key legend)
   - `M` toggles AUTO/MANUAL; MANUAL uses motor-control flight (nonblocking
     `moveByVelocityAsync`, hover when idle) — lifts the flyer logic from
     `drone_keyboard.py` into this one window.
   - Reuses per-camera `simGetImage` (plural RPCErrors on this build — AGENTS.md §6).
4. **H100-side hooks**:
   - `src/vlnce_src/closeloop_util.py`: `EvalBatchState.__init__` →
     `_write_target_beacon(env_batchs)` (line 135/150) writes per-mission
     `/tmp/aerovla_target.json` (asset_name, object_position, object_desc,
     instruction, target_positions). Added `import time`.
   - `src/vlnce_src/eval_aerovla.py`: `_manual_takeover_active()` + a
     `while _manual_takeover_active(): time.sleep(0.2)` before `makeActions`
     (blocks autopilot while an operator flies; resumes + re-syncs on clear).
5. **`scripts/split.sh`** `--cameras` now launches `mission_viewer.py` instead of
   `camera_viewer.py`; help text + summary updated.
6. py_compile OK on all patched files; core viewer functions unit-exercised
   (lidar_to_image, _png_to_ndarray); verified ssh push (`/tmp/aerovla_manual.json`
   written + REMOVED) and beacon cat path (`NO_BEACON_YET` since eval stopped).

### Problems
- `time` not imported in closeloop_util.py → added.
- Viewer must use per-camera `simGetImage` not plural `simGetImages` (build RPCError).
- Manual flag + target beacon cross the tunnel: tunnels are port-only, so the
  viewer pushes flag/pulls beacon via direct local→H100 ssh (works; tested).

### Current state
- All prior eval/viewer processes stopped. Code changes in place + py_compile clean.
- `scripts/camera_viewer.py` superseded but kept (mark SUPERSEDED in its docstring,
  and in the runbook below). Files added: `scripts/mission_viewer.py`.
- Next: commit + push fork, sync H100, relaunch `split.sh 30000 BrushifyForestPack --windowed --cameras`.

### Next steps
- Commit `M AGENTS.md, split.sh, closeloop_util.py, eval_aerovla.py, docs/*`,
  `?? mission_viewer.py, drone_keyboard.py, multiview.py`.
- Push fork, sync H100 (`git fetch fork && git reset --hard fork/main`), relaunch eval.

---

## Session: 2026-09-12 mission console live-debug + relaunch (bugfix round 2)

### Goal
Land the Option A mission console against the live ForestPack split eval; fix the
rendering bugs surfaced once it connected to a real scene + beacon.

### What you did
- Confirmed live beacon shape: `/tmp/aerovla_target.json` has
  `object_position: [[x,y,z]]` (array of triples, parallel to `asset_name[]`).
- **Bug 1 fix** (`scripts/mission_viewer.py`): `_draw_target_label` +
  `_target_xy` treated `object_position` as flat `[x,y,z]` → `TypeError:
  float()... not 'list'`. Now take `pos[0]` triple, use `pos[0][0]`/`pos[0][1]`.
- **Bug 2 fix**: `__init__` called `self._tick()` (nonexistent; renamed to
  `_schedule`) AND `_start()` re-booted `_pump_target`+`_schedule` → double loop.
  Moved startup out of `__init__`; `_start()` is the single entry.
- Restored regenerated `settings/30001/settings.json` to HEAD (live server
  re-minifies the template each launch; it now contains BottomCamera + Lidar1 +
  SpringArmChase, but we treat the template as source of truth to avoid churn).
- Relaunched viewer: `kill -9 $(cat /tmp/aerovla_cameras.pid)`;
  `DISPLAY=:1 nohup /media/sanati/DriveE/miniconda3/envs/aero_vla/bin/python
  scripts/mission_viewer.py 30001 --retry 120 > /tmp/aerovla_cameras.log 2>&1 &`;
  pid 2273272, wrote pidfile.
- Verified via API probe: FrontCamera 129 KB PNG, BottomCamera 690 KB PNG, Lidar
  47815 pts (drone low/near), drone pos live → scene API healthy.
- Confirmed tkinter window on `:1`: `xwininfo -root -tree` shows
  `"AeroVLA mission console (AirSim :30001)"` (tk 403x279 + mutter frame).
- Committed `3438b81` "mission_viewer: fix target_position list-of-lists parsing
  + single loop start"; pushed `f76f09f..3438b81` to fork.
- **Did NOT reset H100** (running eval would be disturbed; H100 files unchanged).

### Problems / solutions
- `object_position` nested → normalize `pos[0]` (see Bug 1).
- Double `root.after` loop from `__init__`+`_start` → `_start` only (Bug 2).
- Server regen minifies `settings.json` → restore to HEAD (template is truth).

### Current state (verified)
- Full stack alive: split 2257244, server 2257497, tunnel 2260184, viewer 2273272;
  mission console window rendering on `:1`, log 0 bytes / 0 errors.
- Eval healthy on H100 (pid 39888): `Completed: 1 / 438`, ep1 done, ep2 in
  progress (target <203,-432>). Confirm ALIVE each check.
- Git: local `main` = `3438b81`; fork pushed. H100 still at `f76f09f` (fine).

### Next steps
- Monitor eval; when H100 not yet reset, a later sync to `3438b81+` is optional
  (only doc/committed-state drift — code on H100 unchanged by this fix).
- Next user knob to test live: MANUAL takeover (M) while eval runs (pauses loop
  until flag cleared; resumes). Suggested after an ep boundary to avoid disruption.

---

## Session 2026-09-12 — Mission console live-render + target-desc fix

### Goal
- Fix the mission console (`scripts/mission_viewer.py`) not showing any view, and
  the target description rendering as incomplete/list-repr in the live ForestPack
  split eval.

### What I did
- **Diagnosed with real AP/API checks** (not guessing): window was tk 403x279
  (tiny), and views were placeholders. Root cause was TWO compounding bugs:
  1. `__init__` ended with `self.root.mainloop()` → it blocked → `main()` never
     reached `v._start()` → `_schedule`/`_frame` (the update loop) never ran.
  2. `WorkerPool._run` **permanently** marked workers faulty on any transient
     RPC error → all subsequent submits returned None (no traceback).
  3. `object_desc`/`asset_name`/`position` are **lists** (`["white cow"]`) →
     label showed ugly list repr + truncated.
- **Fixes applied, committed, pushed (viewer-only, H100 untouched)**:
  - `8dcf696`: WorkerPool no-longer-permanent-fault (+per-worker fail counter +
    auto-reconnect at >=25 consecutive fails, throttled logging); `_draw_target_label`
    unwraps list fields + shows the **full 408-char instruction** (stripped of
    `<image>` tag); window `geometry("1200x800")` + `minsize(760,560)`.
  - `d6a56cf`: moved `mainloop()` to `main()` after `_start()` (the actual "no
    view" root cause).
- **Window moved on relaunch** to `+37+106` — first screenshot analysis used the
  stale `+1970+87` coords and misdiagnosed a "flat grey" FRONT until I rechecked
  `xwininfo`.
- Final verification at `+37+106`: FRONT 55410 unique colors (live, diff ~24/4s),
  BOTTOM 58099, LIDAR 22833, TARGET label renders, log 0 errors.

### Problems / solutions
- `mainloop()` in `__init__` blocked `_start()` → move to `main()` (commit `d6a56cf`).
- Permanent worker faulting hid live-render failures → soft-fail + reconnect
  (`8dcf696`).
- List-typed beacon fields / missing full instruction → `_un()` + instruction
  body (`8dcf696`).
- Window WM position changes between relaunches → always re-read `xwininfo`
  before cropping screenshots.

### Current state (verified)
- Viewer pid 2427839 ALIVE on `:1`, 1200x800 at `+37+106`, log 0 errors.
- Split stack alive: split 2257244, server 2257497, tunnel 2260184, viewer
  2427839 (note: viewer relaunched manually, kill it separately, NOT via split.)
- H100 eval pid 39888 ALIVE: `Completed: 5 / 438`, drone moving toward current
  target (~32 m out). No manual flag on H100 → eval not blocked.
- Git: local `main` = `d6a56cf`, pushed to fork. H100 still `f76f09f` (not reset
  mid-eval; only local viewer/docs drift).

### Next steps
- Monitor eval; optional later H100 sync once reset is safe.
- Test MANUAL takeover (M) with the live viewer after an episode boundary.
- If a Debugger later re-opens scenes: viewer is independent read-only clients,
  it reconnects via WorkerPool auto-recover.

---

## Session 2026-09-12 (cont.) — Camera "freeze-then-jump" FIX + MANUAL mode verified

### Goal
- User reported cameras "update about after 1 sec then freeze then update" even
  after views rendered.
- Verify MANUAL takeover (M) works against the live ForestPack split eval.

### What I did
- **Diagnosed the freeze root cause**: `_pump_target` → `load_target()` falls back
  to a **synchronous `ssh cat` to the H100** when the local beacon
  `/tmp/aerovla_target.json` is missing (it has never existed locally). Measured
  3 consecutive SSH calls at **6.06s each**. Since `_pump_target` is a `root.after`
  callback running **on the tkinter main thread**, every 2s the whole UI blocked
  ~6s on SSH → exactly "freeze ~1-6s then jump".
- **Fix (`scripts/mission_viewer.py`)**: moved beacon fetching off the main thread —
  `_pump_target` (main thread, every 2s) spawns a daemon thread `_poll_target` that
  runs `load_target()` (the slow ssh) and puts the result in a `queue.Queue`;
  `_drain_target()` is called in `_schedule()` (main thread) to apply + redraw.
  Added `import threading`, `import queue`, `self._target_q`, `self._fetching` guard.
- **Verified fix live**: telemetry strip changed 40/40 scans (UI fully alive);
  FRONT repaints every ~0.75s with max no-change gap 1.27s (was 6s). Log 0 errors.
- **Verified MANUAL (H100 side) live**: wrote `{"manual": true}` to
  `/tmp/aerovla_manual.json` on H100 → eval **froze at Step 114 for 40s (0 new
  steps)**; cleared to `{"manual": false}` → eval resumed (Step 116 + drone moved).
  The `_manual_takeover_active()` block (eval_aerovla.py:92) works.

### Problems / solutions
- 6s UI freeze → beacon ssh off the main thread via queue + `_drain_target` in loop.
- H100 ssh flaky (coder agent) → use a single persistent ssh session for repeated
  sampling; robust flag-write with SET_OK retry loop.
- No xdotool/xte/xvkbd on local → cannot inject the M key into the tk window; the
  M→flag path is code-verified (writes identical format) and the H100 pause/resume
  is live-verified; the actual keypress-to-flag is the only untested link.

### Current state (verified)
- Viewer pid 2450609 ALIVE, window at +1894+167 (moved again on relaunch),
  1200x800, 0 log errors, live views.
- H100 eval running: Step ~116, Completed 14/438, manual flag now `false`, eval
  back to normal.
- Git: local main `8594601` + uncommitted mission_viewer fix.

### Next steps
- Commit + push the SSH-freeze fix + docs.
- If a real M-keypress test is wanted: run a key-injection (install xdotool with
  sudo, or use a minimal XTest binary) against the viewer; optional.

---

## Session 2026-09-12 (cont.) — DRONE SPEED ×2 (CRUISE_SPEED 1.0→2.0, applied to running eval)

### Goal
- User: "multiply speed by 2 and affect on current running."

### What I did
- **Located the speed knob**: `airsim_plugin/AirVLNSimulatorClientTool_AeroVLA.py:369`
  `CRUISE_SPEED = 1.0` — the only flight-velocity scaler for the eval forward
  moves. For displacement ≥ `MICRO_MOVE_THRESHOLD` (1 m): velocity magnitude =
  `CRUISE_SPEED`, `duration = total_dist / CRUISE_SPEED`, so **path per action is
  unchanged, only speed doubles**. Micro-moves (<1 m) keep fixed 1 m/s over 1 s
  (lines 384-388); `moveToZAsync` vertical-only uses `velocity=2.0` (line 406) —
  both untouched.
- **Changed** `CRUISE_SPEED = 1.0` → `2.0`, py_compile OK, committed `263c83d`,
  pushed to fork.
- **Synced H100**: `git fetch fork && git reset --hard fork/main` → H100 at
  `263c83d`, confirmed `CRUISE_SPEED = 2.0`.
- **Restarted the running eval** (user approved; no checkpoint/resume exists so it
  re-runs from ep 1, old run was only 14/438 → ~3% loss):
  - Killed old eval pid 39888 (via `run_eval.sh` TERM on its launcher pid 39514 →
    cleanup trap) → confirmed dead, no `eval_aerovla` leftovers.
  - **Gotcha hit**: first relaunch used plain `bash scripts/run_eval.sh 30000`
    which defaults `AEROVLA_MAP=BrushifyCountryRoads` → crashed
    `FileNotFoundError: dataset_raw/BrushifyCountryRoads/.../mark.json`. The split
    eval actually targets **BrushifyForestPack**. Relaunched with
    `AEROVLA_MAP=BrushifyForestPack` → `eval client launched (pid 99768)`.
  - New eval loaded model 3/3 shards, opened `BrushifyForestPack` scene on
    127.0.0.1:30000 (49.6s), started ep 1/418 at Step 0 (15:57:51).

### Verification
- Old cadence @1.0 m/s: ~8-10 s/step. New @2.0 m/s: steps 1-10 in 15:57:56→15:58:35
  ≈ **4 s/step → ~2× faster, matches 2.0 m/s**. Same `batch[0/1] distance` per step
  (path unchanged). Could not read live `getMultirotorState` velocity (that single
  machine's RPC errors on this build) — cadence is the reliable proxy.

### Current state
- Local stack: split 2257244, server 2257497, viewer 2450609 all ALIVE (eval
  reuses port 30000 — no viewer change needed).
- H100 eval pid **99768** RUNNING, ep 1/418 at 2.0 m/s, log `/tmp/split_eval.log`.
- Git: local + H100 at `263c83d` (fork source of truth).

### Next steps
- Monitor progress (now 418 eps dataset; ~4 s/step @2x → est much faster total).
- Docs updated in this session.

---

## Session 2026-09-12 (cont.) — Mission console froze after eval restart (fixed by viewer relaunch)

### Goal
- User reported "the mission console doesnt work properly" after the speed-2x eval restart.

### Problem
- Mission console (pid 2450609, launched 18:38 BEFORE the eval restart) **wedged**: its
  AirSim RPC sockets to the scene on :30001 were invalidated when the scene was respawned
  (new UE4 pid 2522549) by the restart. Log showed `mission: fetch err (TimeoutError:
  Request timed out)` then went **silent** (39 lines, delta 0 over 6s, process alive at
  19% CPU, tkinter window up but views stale/frozen = "doesn't work").

### Solution
- Restarted the viewer exactly as `split.sh` does:
  `kill $(cat /tmp/aerovla_cameras.pid)`; relaunch
  `DISPLAY=:1 nohup <py> -u scripts/mission_viewer.py 30001 --retry 120 > /tmp/aerovla_cameras.log 2>&1 &`
  → new pid **2530679**, "Connected!" repeatedly, 0 errors after 12s.

### Verification
- FRONT camera updating (7/9 diff samples, values up to 15.7 while drone flies).
- TARGET label text present (cols 318-479 at rows 5-40), telemetry region populated.
- H100 healthy: eval Step 57/418, beacon `SM_Cape_Buffalo "black cow"` (updated 15:56),
  `manual:false` — eval unaffected by viewer restart.
- Window position after relaunch moved to `+37+106` (WM moves it; re-read `xwininfo`).

### Gotcha / runbook (important)
- **A viewer running across an eval restart WILL wedge.** Any eval restart
  (e.g. CRUISE_SPEED change) OR server/UE4 scene respawn invalidates the viewer's
  AirSim connection → stale/frozen console. **Always relaunch the mission console
  after restarting the eval/server.**

---

## Session 2026-09-12 (cont.) — FULL teardown of the split evaluation

### Goal
- User: "now kill the entire evaluation".

### What I did
1. Snapshot stack: split 2257244, server 2257497, viewer 2530679, UE4 (:30001) 2522549,
   H100 eval (pidfile /tmp/aerovla_eval_30000.pid, ~Step 57+/418).
2. `kill -TERM 2257244` → split.sh `cleanup()` trap: killed server + UE4 children
   (pkill -P + fuser -k ports 30001..30016) + tunnel + attempted remote H100 kill.
3. Trap left the manually-relaunched viewer (2530679) alive → `kill -TERM/KILL 2530679`.
4. Removed stale pidfiles (`/tmp/aerovla_split_30000.pid`, `/tmp/aerovla_cameras.pid`).

### Problem
- The H100 kill inside split's trap uses `ssh` with 30s timeout; SSH was flaky
  (coder hangup/dial timeouts) so the remote kill may have been skipped.
- First remote check "eval_aerovla: RUNNING" with CHANGING pids
  (106356→106494→106551→106609→106668) — initially misread as a respawning
  supervisor/watchdog. Root cause: my own `ssh`/`pgrep` pipe command lines
  contained "eval_aerovla.py" and self-matched.

### Solution
- Fixed the self-match by using `pgrep -f [e]val_aerovla.py` (bracket trick) and a
  full `ps` scan that excludes its own command string. Final definitive check:
  `eval_aerovla=0 run_eval=0 any_aerovla=0`, pidfile gone → **eval fully stopped,
  no supervisor/wrapper left**.

### Runbook
- Full teardown: `kill -TERM $(cat /tmp/aerovla_split_30000.pid)` (or pid of split.sh)
  → trap tears down server/tunnel/UE4 + ssh-kills H100. Then:
  - `pgrep -af "mission_viewer" | grep -v grep` → kill any manually-launched viewer.
  - `rm -f /tmp/aerovla_split_30000.pid /tmp/aerovla_cameras.pid`
  - Verify H100: `ssh H100 'pgrep -f [e]val_aerovla.py | wc -l'` → expect 0.
  - **Gotcha: use the `[e]val` bracket trick (or full `ps -eo pid,ppid,cmd` + grep -v grep)
    to avoid your own ssh command self-matching pgrep -f.**
- Current state: server ports 30000/30001 free; no local eval procs; H100 idle.

---

## Session 2026-09-13 — 3D LiDAR-Visual Cross-Attention Fusion module (research deliverable)

### Goal
- Implement the pinned research task: `models/encoders/cem.py`
  (`CoordinatesEncodingModule`), `models/encoders/lidar_encoder.py`
  (`LiDAREncoder`), `models/fusion/cross_attention_fusion.py`
  (`SoftLiDARVisualCrossAttention`), `models/aerial_vla_model.py`
  (`AerialVLAModel`), plus pipeline integration into the real
  Prismatic/OpenVLA base model. No `models/` existed before; this is the
  first model-side (non-eval-infra) code in the repo.

### What I did
- **Architecture confirmed**: base = `openvla-7b/` Prismatic fused
  `dinosiglip-vit-so-224px` (DINOv2+SigLIP), `PrismaticProjector` (`no-align+
  fused-gelu-mlp`), LLaMA-2. Integration point = `PrismaticForConditionalGeneration`
  multimodal forward (`modeling_prismatic.py:366` `vision_backbone(pixel_values)`
  → `:369` `projector(...)` → insert after BOS → `language_model`).
- **Created `models/` tree** (git-tracked):
  - `models/encoders/cem.py` — CEM: `psi_im` MLP embeds `p_k^im = T_ci^l·K^-1·p_k(u,v)`
    (depth-sampled rays), `psi_pc` embeds `p_k^pc = (u·u_d, v·v_d, h_k, 1)`. Both output
    heads zero-init → no-op at init.
  - `models/encoders/lidar_encoder.py` — `PointPillarsEncoder` / `VoxelNetEncoder` +
    `LiDAREncoder` (`D_lidar → D_vis` linear). Dense research-simplified backbones,
    not full PointPillars/VoxelNet.
  - `models/fusion/cross_attention_fusion.py` — `SoftLiDARVisualCrossAttention`
    (Q = Z_vis+Γ_im, K/V = Z_lidar+Γ_pc, multi-head, optional SMCA 2D distance mask,
    residual+LayerNorm).
  - `models/aerial_vla_model.py` — `AerialVLAModel(base_model, d_vis, ...)` wrapper:
    fused forward + fallback pass-through; auto project + inject after BOS; mirrors
    Prismatic return type (`PrismaticCausalLMOutputWithPast`).
- **Smoke tests** (`tests/test_aerovla_3d_fusion.py`): CEM/LiDAR[pp+voxel]/cross-attn/
  full-model shapes all pass on local box (torch 2.1.2 CPU).
- **Optional integration hook** in `openvla-7b/modeling_prismatic.py`:
  `config.enable_3d_fusion` flag (default False) attaches
  `self.fusion_module = AerialVLAModel(...)` + `forward_with_3d_fusion()` method.
  **Byte-for-byte additive** — original `forward` untouched, default eval/train path
  unchanged. **2026-09-13 (follow-up): committed** inside the nested `openvla-7b/`
  repo as **`df5d0eb`** (+82/-0) so it survives and is revertable, instead of being
  lost in the git-ignored dir.

### Problems faced
- `psi_im`/`psi_pc` input-dim bug: `psi_im` must embed the 3D de-homogenised ray point
  (dim = waypoint_dims=3), `psi_pc` embeds `(u·u_d, v·v_d, h_k, 1)` (dim 4). First cut
  used waypoint_dims+1 for both → matmul shape error (8192x3 vs 4x128).
- `VoxelNetEncoder` missing `self.grid_res` assignment → AttributeError.
- Smoke-test N_vis mismatch (fake backbone 1024 tokens vs 256 assumption) + depth-sample
  count mismatch → fixed test to 1024 / D=16.

### Solutions applied
- CEM: `psi_im(d_in=waypoint_dims)`, `psi_pc(d_in=waypoint_dims+1)`; `pc_pe` passes the
  full 4-vector.
- Added `self.grid_res = grid_res` in `VoxelNetEncoder`.
- Test now uses `N_vis = 224*224//49 = 1024`, `N_lidar = 32*32 = 1024`, `cem_num_depth_samples=16`.

### Current state
- `models/` + tests created, all smoke tests pass.
- `openvla-7b/modeling_prismatic.py` has the optional hook (untracked/local).
- No training/eval changes; existing LoRA path fully preserved.
- Dirty: `airsim_plugin/settings/30001/settings.json` (pre-existing, not committed).

### Next steps
- Optionally wire LiDAR data source into `src/aerovla_dataset.py` (AirSim `Lidar1`
  point cloud → padded `lidar_points` + `valid`), intrinsics/extrinsics from
  `AIRSIM_SETTINGS_TEMPLATE` / `CameraExtrinsics`.
- When ready: enable `config.enable_3d_fusion=True` in training config; train adapters.
- Consider upgrading `PointPillarsEncoder` to a real topK scatter (research-simplified
  per-voxel max loop is slow).

---

## Session 2026-09-13 (2nd) — UAVLiDARDataset + collator (LiDAR-visual data pipeline)

### Goal
- Build the dataset+collator that feeds real AirSim episode trees (image + LiDAR)
  into `AerialVLAModel.forward(...)`, fixed against the verified model contract.

### What I did
- **Verified model contract**:
  - Fused backbone `cat → [B,256,2176]` (`openvla-7b/modeling_prismatic.py`) → **N_vis=256** (16×16, patch 14) and **d_vis=2176** (1024 DINOv2 + 1152 SigLIP), NOT 11776. Confirmed projector `fc1 [8704,2176]` in `checkpoints/adapter_model.safetensors`.
  - **K⁻¹ bug confirmed real**: `AerialVLAModel.forward` passes `camera_intrinsics`
    straight to `CEM.image_pe(px, intrinsics_inv, ...)` (`models/encoders/cem.py:105`);
    CEM does `K_inv @ pix_hom` (:133-136). Collator now emits **K⁻¹**.
  - Processor contract: `openvla-7b/processing_prismatic.py` `apply_transform` stacks
    the SAME image twice (ImageNet → DINOv2, [0.5] → SigLIP) → **channel-stacked [6,224,224]**.
  - Current eval logs have **no lidar** (32,883 frames, sensors keys `['state','imu']`) →
    added `require_lidar=False` zero-cloud fallback.
- **Created `datasets/` package**:
  - `datasets/uav_lidar_dataset.py` — `UAVLiDARDataset` + `UAVLiDARCollator` +
    `quantize_action` + `build_uav_lidar_batch`.
    - Collator `_process_images` → **explicit 6-channel** [B,6,224,224] via processor
      (if given) else same resize + double-normalize (DINO vs SigLIP stats).
    - **K⁻¹ emitted** under `camera_intrinsics` (inverted in `__call__`).
    - `compute_cem_rays` pixel-coord grid fixed: patch centres = 224-wide final image
      → uu/vv span [0.03125, 0.96875] (was [0,0.5] bug).
    - `require_lidar` flag; missing-lidar → clean zero cloud, strict raises.
    - docstring updated (sample `camera_intrinsics` = K^1 = inverse).
  - `datasets/__init__.py` — exports.
- **Created `tests/test_uav_lidar_dataset.py`**: synthetic episode tree (front/down
  PNGs + `log/<frame>.json` with/without lidar), asserts dataset shapes (N_vis=256,
  N_lidar=4096, max_points), K⁻¹ inverse property, strict-mode KeyError, action bins.

### Problems faced
- `test_uav_lidar_dataset.py` run via `python -m pytest` → `No module named 'pytest'`
  in the `aero_vla` env.
- Probe revealed `composite_image` was [B,3,224,224] (fallback path) and pixel-coord
  grid only spanned [0,0.5] (img_w=W*2 bug).

### Solutions applied
- `pip install pytest` into `/media/sanati/DriveE/miniconda3/envs/aero_vla` (writable venv).
- Rewrote `_process_images` to the fine-tuned training convention (same-image double
  norm → 6ch) and fixed `compute_cem_rays` to tile the full final-image grid.

### Current state
- 4/4 tests pass; fusion tests (`test_aerovla_3d_fusion.py`) still pass.
- `datasets/` + test untracked, ready to commit.
- Dirty (pre-existing, intentional): `airsim_plugin/settings/30001/settings.json`.

### Next steps
- Wire LiDAR into `src/aerovla_dataset.py`? (deferred — dataset is separate optional path).
- Real-episode lidar validation when fresh lidar-enabled eval logs exist
  (current 32,883-frame logs have no `lidar` key).

## Session 2026-09-13 (3rd) — Option A BC NLL training script (`src/train_step_a.py`) + prompt-masked collator

### Goal
- Implement **Option A first**: end-to-end Behavior-Cloning (BC) NLL training
  script that trains `LiDAREncoder` + `CEM` + `SoftLiDARVisualCrossAttention` +
  fusion projector + LLaMA-2 LoRA simultaneously, then validate forward/backward.

### What I did
- **Wrote `src/train_step_a.py`** (new, 312 lines) per the settled design:
  - Load `AutoModelForVision2Seq.from_pretrained(..., enable_3d_fusion=True)`
    (constructs `AerialVLAModel` inside `fusion_module`).
  - LoRA wrap: `LoraConfig(r=64, alpha=128, modules_to_save=["projector"])`
    (same targets as `train_aerovla.py`).
  - Call `peft_raw.forward_with_3d_fusion(...)` on the RAW model with the full
    collator batch (lidar_points, lidar_valid, K⁻¹ intrinsics, extrinsics,
    normalised_pixel_coords, depth_samples, pillar_*, q_pix, k_pix).
  - Explicitly `requires_grad=True` on `peft_raw.fusion_module.*`.
  - Prompt-masked labels from `UAVLiDARCollator(mask_prompt=True)`.
  - Manual loop (not HF Trainer): AdamW wd 0.03 + cosine warmup, grad accum,
    grad clipping, gradient checkpointing (CUDA only), DDP support, `--no_lidar`
    fallback to standard forward.
- **Added repo-root to sys.path** so `python src/train_step_a.py` runs from anywhere
  (`sys.path[0]` was `src/`).
- **Verified prompt-masked collator batch end-to-end** with synthetic episode tree:
  batch keys/input_ids/labels/pillar/q/k intrinsics all present; labels non-mask=30
  (action-only); composite [3,6,224,224] float32; lidar [3,20000,4]; N_vis=256;
  N_lidar=4096.
- **Proved the PEFT+fusion gradient path with a dummy mirroring the real
  architecture**: after unfreeze, `lora_A`/`lora_B`, fusion `fuse_lin`, and
  `projector.modules_to_save.default` all receive grads through
  `raw.forward_with_3d_fusion(...)`; loss decreases over 3 steps (3.46→3.23→3.01).
  Confirmed PEFT `modules_to_save` wraps projector so its `forward()` routes to the
  trainable copy (grads to `modules_to_save.default`, none to `original_module` —
  which stays frozen as a reference). `lora_A` gets no grad at iter 0 only because
  `lora_B` is zero-init (standard PEFT); grad flows from iter 1 onward.

### Problems faced
- Typo `os.enenv.setdefault` in first draft → fixed to `os.environ.setdefault`.
- `UAVLiDARDataset.__init__` takes `samples` (not root/split_json) → updated script
  to `json.load` the split and pass `samples=` + `training=True`.
- Synthetic test failed: `_getitem__` needs front/down PNGs + `log/<frame>.json` →
  built the tree; dataset synthesizes K/T calib when absent (get_camera_calib).
- `No module named 'datasets'` when running `python src/train_step_a.py` (cwd
  resolution) → added repo root to `sys.path`.
- PEFT named-param paths: `raw.named_parameters()` has no `base_model.` prefix
  (that's the wrapper's view); fixed dummy test lookups.

### Solutions applied
- (see above; the full fix log is in this entry)

### Current state
- `src/train_step_a.py` written; CLI `--help` works; modules import; all tests
  (8/8) pass; `train_step_a` syntax OK.
- Uncommitted: `src/train_step_a.py` (new), `datasets/uav_lidar_dataset.py`
  (prompt-masking: `make_prompt_parts`, `UAVLiDARCollator(mask_prompt=True)`,
  `_tokenize` masking labels[:plen]=-100).
- Real fused forward/backward on the actual 7B **cannot run on the local 6 GB
  box** — must validate on the H100.

### Next steps
- Commit `src/train_step_a.py` + datasets prompt-masking; `git push fork main`.
- On H100: fetch + `python src/train_step_a.py --micro_batch 1 --max_steps 2`
  small-batch smoke to validate the REAL fused forward/backward + LoRA grads, then
  kick the real run.
- After Option A is validated, implement Option B: InfoNCE pre-alignment
  (frozen DINOv2/SigLIP) → LoRA BC fine-tune.

## Session 2026-09-13 (4th) — H100 smoke `loss=nan` ROOT-CAUSED + FIXED (HF loader discards fresh fusion params → uninitialized memory)

### Goal
- Get the H100 smoke (`src/train_step_a.py --micro_batch 1 --max_steps 2`)
  to a **finite, non-NaN loss** so real Option A training can start.

### What I did
- Ran the H100 smoke → `loss=nan`. Ran a per-stage `nan_probe_h100.py` on H100
  → located NaN exactly at `lidar_encoder` output (`(1,4096,2176) nan=True`) while
  `vision_backbone`, `cem pe_im`, `cem pe_pc` were all clean.
- Ran `lidar_isolate_h100.py` (per-sub-layer CUDA-bf16 probe, all-zero/empty cloud):
  `pillar_mlp` clean, `voxelize` clean, **`conv_stack` → NaN**, `proj` → NaN.
- Ruled out by standalone CUDA-bf16 repros (`bn_zero_cuda.py`, `lidar_layers_h100.py`):
  fresh `nn.BatchNorm2d`/`Conv2d`/`ReLU` conv_stacks on all-zero input are **clean**
  on CUDA bf16 (train + requires_grad + fp32→bf16 cast). So it was NOT a pure
  bf16/CUDA kernel issue.
- Dumped actual loaded model params (`lidar_nan_params.py`):
  - `fusion.norm.weight` / `fusion.norm.bias` (fusion LayerNorm) = **NaN**.
  - `lidar_encoder.backbone.conv_stack.*.weight` (BatchNorm) = **~8e35 garbage**
    (expected fresh-init = 1.0); `running_mean=0/var=1/num_batches_tracked=0`
    (fresh BN, so they were freshly made but with garbage scale).
  - conv/LN weights that *do* init look right (kaiming ~0.08); only **BN weight
    and the fusion LayerNorm** are garbage/NaN.
- **Root cause**: HF `from_pretrained(..., low_cpu_mem_usage=True,
  device_map={"": rank})` first moves the whole model to **meta device**,
  discarding the fresh initialization done in `AerialVLAModel.__init__`
  (BN weight=1, LayerNorm weight=1). Then it materializes only the params that
  appear in the checkpoint. The fresh `fusion_module` params are NOT in the
  checkpoint → they come back as **uninitialized raw memory** (NaN / huge
  garbage) instead of the proper init. The `.to(base_dtype)` cast then only
  changed dtype, baking in the garbage. Standalone repros were clean because
  they never went through the meta-discard path.
- Why `lidar_encoder` NaNs *first*: the BN in `conv_stack` has `weight ~8e35`
  and `bias ~8e35`; with a non-zero BEV the `x_hat * weight + bias` overflows
  bf16 → NaN, which then propagates to `proj`/loss. With a fully zero BEV it
  stays 0/CLEAN → hence the all-zero isolate looked clean at `conv_stack` in
  earlier runs but the real (nonzero) batch NaNs.
- **Fix**: added `AerialVLAModel.reset_fusion_parameters()` (`models/aerial_vla_model.py`)
  that deterministically re-initializes cem/lidar_encoder/fusion (Conv/Linear
  kaiming+proper bias, BatchNorm `reset_parameters()`, LayerNorm weight=1/bias=0).
  Called it in `src/train_step_a.py` immediately after `get_peft_model`, because the
  meta-discard happens at `from_pretrained` time.
- Verified:
  - Local unit test corrupts all fusion params→NaN then `reset_fusion_parameters()`
    → 0 NaN, BN=1, LN=1, proj sane; dtype stays bf16 after reset.
  - Local `pytest tests/test_aerovla_3d_fusion.py` → **10/10 passed** (added
    `test_reset_fusion_parameters_recovers_nan` + `test_reset_fusion_parameters_bf16`).
  - Synced `models/aerial_vla_model.py` + `src/train_step_a.py` to H100.
  - **H100 smoke re-run: PASSED** — `[stepA] epoch=0 step=1/2 loss=10.7706`,
    `step=2/2 loss=14.6890` (finite; per-step loss varies with random data),
    checkpoint saved, exit 0.

### Problems faced
- `loss=nan` on H100 smoke (end-to-end ran, but forward → NaN).
- Buffered stdout on H100 made per-param dumps vanish (no flush) — fixed by
  writing to a file + `flush=True`.
- 2nd-guessed the `conv_stack` NaN as CUDA-bf16 kernel; standalone repros
  clean → pointed to the model's own (uninitialized) params.

### Solutions applied / runbook
1. Add `reset_fusion_parameters()` to `AerialVLAModel` (boxed in AGENTS §6
   "current key context" as the mandatory post-load step when fusing).
2. Call `peft_raw.fusion_module.reset_fusion_parameters()` right after
   `get_peft_model` in any script that loads the fused model with
   `low_cpu_mem_usage`+`device_map`.
3. Re-run smoke: `ssh H100; cd /workspaces/AeroVLA && setsid nohup bash
   .stepa_smoke/run.sh >.stepa_smoke/smoke2.log 2>&1 &` → watch
   `grep '\[stepA\]' .stepa_smoke/smoke2.log` for finite losses.

### Current state
- **H100 smoke passes with finite loss.** The fuse path is now trainable end-to-end.
- Uncommitted: `models/aerial_vla_model.py` (reset_fusion_parameters + math import),
  `src/train_step_a.py` (reset call), `tests/test_aerovla_3d_fusion.py` (2 new tests),
  plus the earlier pending: `datasets/uav_lidar_dataset.py`, `models/encoders/cem.py`.
- `tests/test_uav_lidar_dataset.py` still NOT on H100.

### Next steps
- Commit + push `fork main` (6 files), H100 `git reset --hard fork/main`.
- Launch real Option A run on `dataset_raw`/`data/aerovla_train_dataset.json`
  with the full settings (MICRO_BATCH=2, GRAD_ACCUM=8, lora r64 α128, bf16).
- Then start Option B (InfoNCE pre-alignment).

---

## Session 2026-09-13 PM — Launch real Option A BC-NLL training on ForestPack split

### Goal
Build the ForestPack training split and launch the real `src/train_step_a.py`
run on the user's own AirSim episodes on the H100; then Option B.

### What you did
- **Built the split** with `scripts/build_training_split.py` (reads only
  `merged_data.json` per episode, derives fwd/down/yaw labels from
  `trajectory_raw_detailed` between sampled `index` keyframes): wrote
  `data/aerovla_train_dataset_forestpack.json` = **27,885 samples / 446 episodes**
  (20.9 MB), skipping 2 episodes with no `merged_data.json`.
- **Validated dataset load** (`/tmp/validate_split*.py` on H100): samples load
  through `UAVLiDARDataset(data_root=./dataset_raw, require_lidar=False)`; labels
  finite; images 448x224; collator inputs all finite.
- **Fixed 2 training bugs found by a `--micro_batch 2` memcheck**:
  1. `UAVLiDARCollator._tokenize` prompt-probe tokenize had no
     `padding=` → `ValueError: Unable to create tensor ... excessive nesting`
     on any batch > 1 with variable-length prompts. Fix: `padding="longest"`
     on the prompt-length probe (`datasets/uav_lidar_dataset.py:686`).
  2. `tokenizer_max_length=96` (collator default) was smaller than the ~110-token
     AeroVLA instructions, so `mask_prompt=True` truncated away EVERY action token
     → all labels `-100` → cross-entropy NaN. Fix: pass
     `tokenizer_max_length=256` to the collator in `src/train_step_a.py:225`.
- **Committed & pushed** `77bf2b2` to `fork` (`scripts/build_training_split.py` +
  both fixes). Local 15/15 tests pass. H100 synced via `git fetch fork &&
  git reset --hard fork/main`.
- **Launched the real run** (H100, `nohup`, pid visible via `pgrep -af
  train_step_a.py`, log `/tmp/train_stepa.log`):
  ```
  .venv/bin/python -u src/train_step_a.py --model_path /workspaces/AeroVLA/openvla-7b \
    --data_root /workspaces/AeroVLA/dataset_raw \
    --split_json ./data/aerovla_train_dataset_forestpack.json \
    --output_dir ./checkpoints/aero_vla_step_a \
    --micro_batch 2 --grad_accum 8 --lora_r 64 --lora_alpha 128 \
    --cem_num_depth_samples 32 --max_steps 100000 --save_steps 2000 --save_total_limit 5
  ```

### Problems faced
- `loss=nan` at `--micro_batch 2` even though the micro_batch-1 smoke was finite.
- Every row of the collator labels was fully masked (`non_masked=0`).

### Solutions applied
- Root causes above → both fixes committed. Verified on H100 with a probe
  (`/tmp/full_fwd2.py`): labels `non_masked=10/row`, `loss=10.99` finite, logits
  0 NaN. Then full `train_step_a.py --micro_batch 2` memcheck: losses 11.00→9.18
  finite.

### Current state
- **Real run LIVE on H100**: `dataset size=27885`, `trainable=252,167,872
  (3.24%)`, losses descending 11.12 → 1.89 by step 350, ~0.58 s/step, capped at
  `8710` total steps (5 epochs × ~1742 opt steps) → ~1.5 h runtime.
- `checkpoints/aero_vla_step_a/` is the output; 5.5 GB disk caution noted.

### Next steps
- Monitor run; check `grep stepA /tmp/train_stepa.log` for descending losses.
- Next checkpoint saved at step 2000 (`checkpoints/aero_vla_step_a/step_2000`).
- Then start Option B (InfoNCE pre-alignment → BC fine-tune).
- Update `docs/PROJECT_HANDOFF.md` + this journal at session end.

---

## Session 2026-09-13 AM (cont. — fog/alt/lidar env work)

### Goal
After the live visual-only Option A run finishes: modify env conditions (fog + max
10 m altitude AGL + existing dense forest obstacles) and re-capture episodes WITH
lidar, then re-run Option A with `require_lidar=True`.

### What I did (investigation)
- Confirmed existing H100 episodes have `sensors: ['cameras','imu','state']` (NO
  lidar) → current Option A run is a visual-only baseline.
- Mapped full capture pipeline: eval (`eval_aerovla.py` → `env_uav.makeActions` →
  `AirVLNSimulatorClientTool_AeroVLA.move_path_by_actions`) flies the reference
  `trajectory_raw_detailed` from `merged_data.json`, and on episode end
  `save_to_dataset_eval` writes a NEW tree (`log/` + images + `object_description.json`)
  to `eval_save_path/<prefix>uuid/`. That new tree CAN include lidar (the client's
  `Lidar(BaseSensor).retrieve()` is already in `move_path_by_actions` + `getSensorInfo`).
- AirSim weather: `simEnableWeather` + `simSetWeatherParameter(WeatherParameter.Fog=7)`
  exists in the installed airsim client (python3.10 site-packages).
- Reference path altitudes: NED z ∈ [−14.9, −29.9] (15–30 m AGL) → 10 m cap will
  visibly clamp flight.
- `map_spawnarea_info.json` has BrushifyForestPack entries (ground NED ≈ −10.6 to −32.1).
- No native AirSim down-raycast (`simTraceLine` absent) → ground reference for the
  altitude cap must come from the reference path's max z (ground-line proxy) per
  episode, or a fixed per-map GROUND_Z.

### Plan (per user decision: fog + alt-cap + re-capture, existing forest obstacles)
1. Fog: add a client-side `simEnableWeather(True)` + `simSetWeatherParameter(Fog, 1.0)`
   callable from the capture/eval path (or a server-side hook) — verify it renders
   (CPU llvmpipe caveat).
2. Altitude cap: clamp `target_z` in `move_path_by_actions` so drone never flies
   above `GROUND_Z − 10`.
3. Re-capture: run a fresh eval/capture pass with the current client (lidar on),
   writing new episode trees; rebuild split; rerun train.
4. Then Option B InfoNCE pre-alignment → BC fine-tune.

### Problems & open questions
- Fog rendering on CPU (llvmpipe) backend unverified — may need UE4-level weather
  injection instead of AirSim weather API.
- Ground reference for 10 m cap TBD (per-episode ground proxy vs per-map const).

## Session 2026-09-14 AM — AeroVLA re-capture launch with official LoRA

### Goal
Launch the split re-capture of BrushifyForestPack (444 eval episodes) on H100 with
fog=1.0 + 10 m AGL alt-cap + **lidar** capture, using **AeroVLA's own pretrained LoRA**
(NOT our `aero_vla_step_a` finetune), windowed + mission console per user.

### What I did
1. **Located AeroVLA's official checkpoint**: upstream README says pretrained LoRA from
   Hugging Face `XuPeng23/AerialVLA` → `./checkpoints/aerial_vla/`. The HF repo keeps the
   adapter under `aero_vla/` subfolder (`adapter_config.json` + `adapter_model.safetensors`,
   462,655,064 B). Confirmed `adapter_config.json` is **identical** to ours (r=64, α=128,
   `modules_to_save=["projector"]`, base `./openvla-7b`) → our Option A hyperparams match official.
2. **Downloaded** it on H100 to `/workspaces/AeroVLA/checkpoints/aerial_vla/` (curl -L, verify size).
3. **`run_eval.sh`**: added `MODEL_PATH=${AEROVLA_MODEL_PATH:-./checkpoints/aerial_vla}` and use it
   for `--model_path` (was `./checkpoints` = our old finetune). Commit `02054b1`, pushed to fork,
   H100 synced `git fetch fork && git reset --hard fork/main`.
4. **Smoke-tested the official LoRA on H100**: base `openvla-7b` config has
   `use_fused_vision_backbone=True` (dinosiglip, 6-channel input); `PrismaticImageProcessor`
   returns `[1,6,224,224]` from the 224×448 mosaic → wrapper path is correct. Load OK
   (base 5.9s, adapter 5.6s, CUDA bf16), generate works (`SMOKE OK`). Two smoke-script-only
   bugs found+fixed along the way: (a) `models` import fails when script runs from `/tmp`
   (sys.path[0]=/tmp) → explicit `sys.path.insert(0,"/workspaces/AeroVLA")`; (b)
   `model.peft_config` is a dict in modern peft → index `["default"]`.
5. **Launched split re-capture**: `bash scripts/split.sh 30000 BrushifyForestPack --windowed --cameras`.
   Local server UP (`127.0.0.1:30000`), reverse tunnels, mission console. Remote eval started
   manually after a transient `coder ssh` hang (H100: `run_eval.sh 30000`).

### Verification (mission 1 `success_79312b39-…`, saved as `…/eval_results/checkpoints/seen_valset/BrushifyForestPack/`)
- **Lidar CAPTURED**: `sensors` = `[state, imu, lidar]`, frame 0 has 144,969 lidar points.
- **Fog ACTIVE**: front-cam mean RGB ≈ (126,113,118), std 22 → heavy whiteout, consistent with fog 1.0.
- **Alt-cap wired**: H100 `env_uav.py:433-444` (`ground_zs` + `max_alt_agl` clamp) + fog at
  `env_uav.py:286-287`; flown NED z range −12.34…16.34 (bounded).
- **Pace**: ~3 eps done in first ~6 min, missions terminate early on success (Step ~38).
  This is a **multi-hour run** (llvmpipe + 6-ch fused inference, ~2 min/ep → ~15-20 h for 444).

### Problems faced & solutions
- `coder ssh` transient hangs/timeouts during launch → retried; remote eval launched separately.
- Smoke `split_with_sizes([3,3])` error was my fake 3-ch pixel_values; real processor yields 6-ch.
- Missing `models` package in remote smoke → sys.path fix (eval itself is fine: `eval_aerovla.py:9`).

### Current state
- Run LIVE on H100 (pid `/tmp/aerovla_eval_30000.pid` = 418000); local stack pid
  `/tmp/aerovla_split_30000.pid`. Results → `eval_results/checkpoints/seen_valset/BrushifyForestPack/`.
- Progress `Completed: N / 444`. First ep verified (lidar+fog+alt-cap).

### Next steps
- Let run finish (multi-hour). If aborted: `kill $(cat /tmp/aerovla_split_30000.pid)` locally tears all.
- Post-run: rebuild `merged_data.json` + split; Option A with `require_lidar=True`; then Option B.

## Session 2026-09-14 (cont.) — Mission metrics beacon + 5 m alt cap + H100 outage diagnosis

### Goal
Continue the split BrushifyForestPack re-capture (fog 1.0, now **5 m AGL cap**), deliver live
mission metrics to the mission console, then monitor the restarted eval.

### What I did
1. **Metrics beacon** (commit `ca5c8c1`, pushed to fork, H100 synced):
   - `scripts/run_eval.sh`: `ENV_MAX_ALT_AGL` default **10 → 5** (user: "limit max alt to 5 meter").
   - `src/vlnce_src/closeloop_util.py`: `EvalBatchState` gains `mission_start`, `step_count`,
     `collision_count` (False→True transitions only), `_prev_collisions`, `term_classes`;
     `_write_metrics_beacon(force=False)` writes `/tmp/aerovla_metrics.json`
     `{step, time_s, collision:[...], term:[...], at}` every 10 eval steps + mission start +
     termination; `check_batch_termination` sets term class
     ('success'/'oracle'/'collision'/'early_end'/'timeout') + final forced beacon.
   - `src/vlnce_src/eval_aerovla.py`: calls `_write_metrics_beacon()` after `update_metric()`.
   - `scripts/mission_viewer.py`: `METRICS_BEACON_LOCAL/REMOTE`, `load_metrics()`,
     `_pump_metrics`/`_poll_metrics`/`_drain_metrics`, wired into `_start()`+`_schedule()`;
     telemetry renders `metrics: step=… elapsed=…s collisions=… term=…`. Local-File-first,
     ssh-fallback pattern (same as target beacon), worker thread + queue (never tkinter main thread).
   - `py_compile` OK on all touched files.
2. **Eval restart**: H100 synced `git reset --hard fork/main`; old run torn down (it had errored
   `ERROR - _worker_env:184/186 - command is: get_obs_at` at 07:25:31 anyway); new run relaunched
   `AEROVLA_MAP=BrushifyForestPack AEROVLA_ENV_FOG=1.0 nohup bash scripts/run_eval.sh 30000 /tmp/split_eval.log`
   → pid 442730 (launcher 442380). Verified live: cmdline `--max_alt_agl 5`; beacon
   `{"step":13,"time_s":58.38,"collision":[0],"term":[null]}`; stepping 5→13, Completed 2/433.
   (New run total **433** episodes, not 444.)
3. **Viewer restart (root cause found)**: running viewer (pid 75690) STARTED 10:19:38 but
   `mission_viewer.py` was edited 10:50:53 → old code, no metrics panel. Killed + relaunched
   (new pid 149628, `:1`, window moved to `+1970+87`). Verified via screenshot analysis
   (PIL, no OCR): FRONT std=53/1530 uniq, BOTTOM std=82/1046, LIDAR std=49/1639 → **all live**,
   not placeholders.
4. **H100 outage diagnosed**: 10 min later the FRONT view froze (frame-to-frame mean abs diff
   0.0 over 6 s) → eval dead. Empty `/tmp/aerovla_eval_30000.pid`, no ssh answer (5×30 s
   attempts + background 8-min retry loop all fail). Local stack unaffected (split 43524,
   tunnel 43577, server 43549, UE4 141791 all alive).

### Problems faced & solutions
- **Viewer had no metrics**: running viewer was on OLD code (started before edit). Fix: restart it.
- **H100 ssh unreachable for 17+ min**: cannot read `/tmp/split_eval.log`, beacon, or restart eval.
  Root cause likely the documented flaky `coder` ssh; also NOTE `split.sh` does **not** forward
  beacon files over the tunnel (only ports 30000-30016), so while H100 is unreachable there is
  no way to see eval progress. Tunnel only forwards AirSim ports.

### Current state
- Local stack UP; viewer (149628) rendering live views (screenshot `/tmp/mission_console_v2.png`).
- H100 eval presumed DEAD (frozen front view, empty pidfile); H100 host unreachable via ssh.
- No local copy of `/tmp/split_eval.log` → cannot confirm the death reason until ssh recovers.

### Next steps
- When ssh recovers: inspect `/tmp/split_eval.log` tail for the error; decide restart.
- Consider adding beacon-file forwarding to `split.sh` (reverse tunnel `-R` over an `http`/file port
  or a `-R` cat) so evals stay observable even when direct ssh to H100 is down.
- After run completes: rebuild `merged_data.json` + split; Option A `require_lidar=True`; then Option B.

### BLOCKER UPDATE (same session, ~15 min later)
- **Root cause of H100 ssh failure**: NOT the eval/tunnel — it's the **coder control plane**.
  `ssh main.copper.sanati-emp.coder 'echo UP'` with `LogLevel=ERROR` shows:
  `error: Get "http://coder.sepidserve.local/api/v2/users/sanati-emp/workspace/copper?include_deleted=false": terminated signal received`
  plus a `version mismatch: client v2.35.3, server v2.33.6` warning. The local `coder` client
  cannot reach `coder.sepidserve.local` to resolve the `copper` workspace agent.
- The **long-lived reverse tunnel (pid 43577, opened 09:57) still works** (established connection
  survives) → the AirSim server/UE4 side is intact. But no NEW `coder ssh` command works until the
  control plane recovers. This is **user dev-infra**, not a repo fix. Did NOT touch `/usr/bin/coder`
  (root-owned, would break IDE ssh on other hosts).
- Action taken: stopped the redundant background ssh-retry loop. Logged exact error for resumption.


---

## SESSION 2026-09-15 — Foggy-LiDAR lidar-usage study: capture + instrumentation (PAUSED per user)

### Goal
Prove whether the cross-attention LiDAR-visual fusion actually uses lidar, and how much:
capture foggy-with-lidar training logs via a local UE4 sim replay pass (NOT a model eval),
finetune on H100 with `require_lidar=True`, and instrument the cross-attention to measure
lidar usage (attention mass + lidar→zero ablation). Scope = small slice (~10-20 episodes).
User: "we dont want to run eval", "just add lidar to env and run finetune to capture the
logs or attentions or any other way that finds out if lidar is used / how much weight".

### What I did
1. **Foggy lidar capture validated end-to-end** on H100 (server pid 35136 + tunnel 35200 to
   local UE4, port 30001): 4 BrushifyForestPack episodes captured under `fog=1.0` with real
   lidar (~40-49k pts/frame) in exactly the `log/<frame>.json` `sensors.lidar` format
   `UAVLiDARDataset` expects (`point_cloud` flat list, `pose`, `segmentation`, `time_stamp`;
   frame keys = `['frame','sensors']`, sensors = `['state','imu','lidar']`). Each episode =
   10 camera subdirs (`frontcamera`,`downcamera`,`leftcamera`,`rearcamera`,`rightcamera` ×
   RGB/depth) × 40 pngs + `log/` 40 jsons.
2. **Freed H100 disk (was 100% full 80G/80G → 27G avail)** per user directive, deleting:
   `/workspaces/AeroVLA/eval_results` (34G), `/workspaces/AeroVLA/evals.zip` (3.1G),
   `/workspaces/AeroVLA/checkpoints/aero_vla_step_a` (2.2G). All confirmed gone. NOTE:
   **`aero_vla_step_a` deleted — the active finetune checkpoint no longer exists**;
   `checkpoints/aerial_vla` (442M) remains and is the default eval/start model
   (`MODEL_PATH="${AEROVLA_MODEL_PATH:-./checkpoints/aerial_vla}"` in `scripts/run_eval.sh`).
3. **Pre-deletion audit** (user-requested): `eval_results/checkpoints/seen_valset/
   BrushifyForestPack` had 60 episodes → 20 plain (failed/short, 6-111 logjson), 16
   `oracle_*`, 24 `success_*` (~20/60 failed).
4. **Episode-4 failure root cause** (first capture): disk full + transient
   `getImageResponses` RPC failure. Fixed by adding an **8-attempt retry wrapper (2s sleep)**
   around `getImageResponses` in `scripts/replay_capture_lidar.py`; second run completed all
   4 episodes cleanly (`[replay] FINISHED: 4 episodes captured under fog=1.0`).
5. **H100 remote shell artifact**: piped stdout over ssh gets mangled. Workaround: scp a
   remote python script to `/tmp`, run it redirecting to a file (`> out 2>&1; echo RC=$?`),
   scp the file back to local, `cat` locally.
6. **Built train split**: `build_training_split.py --eps_dir envs/data_raws/BrushifyForestPack
   --out data/aerovla_train_dataset_fog_lidar.json --map BrushifyForestPack --min_frames 5
   --skip_missing --max_eps 20` → **1053 samples / 20 episodes**; 2 skipped (no
   `merged_data.json`). 18 episode UUIDs → `/workspaces/AeroVLA/data/uav_dataset/
   fog_lidar_episodes.json`.
7. **Dataset wiring**: split `traj_rel_dir = BrushifyForestPack/<uuid>` resolves against
   `--data_root`; finetune uses `--data_root ./envs/lidar_capture` (foggy PNGs + lidar logs).
   `src/train_step_a.py:213` hardcodes `require_lidar=False` → must become True.
   `camera_calib.json` optional (falls back to synth 90° FOV K + default extrinsics).
8. **Instrumented `SoftLiDARVisualCrossAttention`** (`models/fusion/cross_attention_fusion.py`):
   added `capture_attn` flag (default False, no semantic change) + `attn_stats` buffer
   recording per-forward: `per_head_mean_mass` (8 heads × N_k lidar-key attention mass, mean
   over batch+queries; baseline 1/N_k when unused), `mean_lidar_mass`, `max_key_focus`,
   `mean_entropy`; plus a standalone `@torch.no_grad()` method **`lidar_ablation()`** running
   the fusion twice (with and without zeroed lidar tokens/PE) and reporting `rms_delta`,
   `rel_rms_delta`, `cos_sim`, `frac_changed` (how much of the fused representation depends
   on lidar). Verified locally (CPU): random-init gives exactly baseline 0.0156 = 1/64 mass
   (uniform, expected pre-training). Two runtime bugs during dev — `.mean(dim=(0,1)).item()`
   on [B,h,Nq] → `RuntimeError: 100 elements cannot be converted to Scalar`; fixed by
   `.mean()` over all dims; per-head kept as [8,N_k] via `.mean(dim=(0,2))`.

### Problems faced & solutions
- **18-ep capture list format BUG**: `fog_lidar_episodes.json` written as a list of plain
  path strings, but `replay_capture_lidar.py` expects entries as dicts `{"json":
  "BrushifyForestPack/<uuid>/merged_data.json", "frame":1}` (per original split format).
  Crashed immediately at line 117 (`TypeError: string indices must be integers`). **Fixed**:
  regenerated from `data/aerovla_train_dataset_fog_lidar.json` (18 correct dict entries).
- **Relaunched 18-ep capture (pid 962411)** after the format fix; verified it was running
  (`[replay] capture ...` progressing). ~25 min estimated for 18 eps.

### Current state (PAUSED — user said "stop the training" then chose "document, stay idle")
- **H100 replay capture STOPPED by user** (pid 962411 was killed; `pgrep -af
  replay_capture_lidar.py` → confirmed nothing running). Only the original **4-episode
  foggy-lidar capture** exists for real at
  `/workspaces/AeroVLA/envs/lidar_capture/BrushifyForestPack/{0081dd00-…,020454a6-…,
  0243c326-…,033200b8-…}/` (each 10 cam dirs × 40 pngs + 40 log jsons with lidar).
- Train split `data/aerovla_train_dataset_fog_lidar.json` (1053 samples) ready but the
  18 capture dirs it references are NOT yet captured on H100 (only 4 of 18 done).
- Attention instrumentation in `models/fusion/cross_attention_fusion.py` is DONE + locally
  verified (CPU) but **not committed, not pushed, not propagated to H100**.
- `src/train_step_a.py` still hardcodes `require_lidar=False` (line 213) — not yet flipped.
- No finetune was run this session.

### Next steps (when user resumes)
1. Restart the 18-ep foggy lidar capture on H100 (`bash /workspaces/AeroVLA/launch_h100_replay18.sh`;
   ~25 min), or re-scope to a smaller slice if preferred.
2. Commit + push + propagate the fusion instrumentation (and `replay_capture_lidar.py`) to H100.
3. Flip `require_lidar=True` in `src/train_step_a.py:213`; finetune on H100 with
   `--data_root envs/lidar_capture --split_json data/aerovla_train_dataset_fog_lidar.json`,
   starting from `checkpoints/aerial_vla` (since `aero_vla_step_a` is deleted).
4. Analyze `attn_stats` (lidar mass vs 1/64 baseline) + `lidar_ablation()` deltas to quantify
   lidar usage; update docs.

---

## SESSION 2026-09-19 (resume) — capture truncated-frames bug found & fixed

**Goal**: finish foggy-lidar capture (18 eps) + finetune; prove lidar usage.

**Problem**: The first 18-ep capture used `RP_MAX_FRAMES=40` (frames 0-39/ep), but the
training split `data/aerovla_train_dataset_fog_lidar.json` references keyframe `index`
values up to ~150-428 per episode. ALL 18 episodes had samples with `img_name` >= 40
(≈77% of 1053 samples). The 40-frame capture would leave those PNGs/log-jsons missing →
finetune would fail/silently drop most samples. (User caught this: "drone may be close to
target in more frames".)

**Solution (user approved "Re-capture keyframes only")**:
- `scripts/replay_capture_lidar.py`: added keyframe mode — captures exactly each episode's
  `index` list from `merged_data.json` (e.g. `0081dd00` = 53 keyframes, max=258) instead of
  `frames[:max_frames]`. Falls back to max_frames if no `index`. `RP_ALL_FRAMES` env forces
  full-trajectory mode. Loop now iterates real frame index values (writes `000005.json` etc.,
  not `000000..000039`). Also clears (`shutil.rmtree`) any prior capture of that episode so
  stale 0-39 frames can't mix with the new keyframe set.
- New launcher `launch_h100_replay18_keyframes.sh` (RP_MAX_FRAMES=120 fallback, keyframe mode).
- scp'd both to H100. Old `launch_h100_replay18.sh` + `envs/lidar_capture18.log` deprecated.

**Current state**: keyframe capture running on H100 (pid 100887, log
`envs/lidar_capture_keyframes.log`). Verified: `0081dd00` 53 keyframes (0..258) done, also
`020454a6`(33) + `0243c326`(53), on `033200b8`(46). Local UE4 up (pid 1152882) via reverse
tunnel; mission console window on :1 (+1970+87). ~30-40 min to finish 18 eps.

**Next**: wait for capture → commit+push fork (replay script + instrumentation) → H100
`git fetch fork && git reset --hard fork/main` → flip `require_lidar=True` in
`src/train_step_a.py:213` → finetune from `checkpoints/aerial_vla` → analyze attention/ablation.

## SESSION 2026-09-19 (cont.) — keyframe capture completed (18/18, verified)

**Problem**: (a) UE4 scene died mid-capture (process gone, port 30001 down) → RPC `Request timed out`
on `simSetKinematics`, capture loop aborted episodes at frame 0 and then blocked (log frozen ~10 min).
Not an OOM (GPU 1.1 GiB/6 GiB, RAM free). (b) After restart, 5 episodes remained truncated at 40 frames
because the crashed run only reached ~11 before dying, and the restart-3 covered only 3.

**Solution**:
- Killed the wedged H100 capture (pid 100887). Restarting the capture fresh triggered the server to
  respawn UE4 (server + reverse tunnel stayed alive; only the UE4 binary had died).
- Filtered episode lists (`data/uav_dataset/fog_lidar_missing3.json` / `_missing5.json`) + small launch
  scripts (pid 116359 for 3, pid 119597 for 5) to re-capture ONLY the missing episodes (not waste the good ones).
- Capture dir clears (`shutil.rmtree`) before re-capture prevents stale 0-39 frames mixing in.

**Current state**: **18/18 episodes captured in keyframe mode**, integrity-verified on H100:
`log_missing=0`, `image_missing=0` (frontcamera/downcamera/+depth), sampled logs have lidar pts.
Total 1053 samples fully resolvable. Local UE4 dies sporadically mid-capture (~1 in 6 eps during the
18-run; mitigated by filtered re-runs) — capture is now N=18 complete.

**Next**: flip `require_lidar=True` in `src/train_step_a.py:213`; finetune on H100 from
`checkpoints/aerial_vla` (`--data_root envs/lidar_capture --split_json data/aerovla_train_dataset_fog_lidar.json`);
analyze `attn_stats` vs 1/64 baseline + `lidar_ablation()` deltas; update docs.
