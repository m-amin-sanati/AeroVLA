# AeroVLA — Project Handoff / Context Document

> Written for a fresh agent to rapidly understand **what this project is**, **what the
> user is trying to accomplish**, **what has already been done**, **the current state on
> disk**, and **exactly what to do next**.
>
> Companion docs:
> - `docs/AEROVLA_EVAL_RUNBOOK.md` — step-by-step ops for running the eval on a GPU VM + pulling results
> - `docs/assets/troubleshooting.md` — AirSim `simGetImages` slowness fix

---

## 1. What this project is

**AeroVLA** = *A Vision-Language-Action Model for UAV Navigation via Minimalist End-to-End
Control* (ECCV 2026). It maps raw visual observations + fuzzy linguistic instructions
directly to continuous 3-DoF physical control signals for drone flight, plus an intrinsic
landing signal. It uses dual-view perception (mosaic of two images), a fuzzy directional
prompt derived from onboard sensors, and a unified control space. Benchmark: **TravelUAV**
(Unreal Engine / AirSim).

This is the **official AeroVLA repository**, which ships:
- Training code (`src/train_aerovla.py`, `src/aerovla_dataset.py`)
- Evaluation code (`src/vlnce_src/eval_aerovla.py`)
- Model wrapper (`src/model_wrapper/aerovla_wrapper_ui.py`)
- Closed-loop simulator server (`airsim_plugin/AirVLNSimulatorServerTool.py`)
- Pre-trained base `openvla-7b/` (OpenVLA-derived, bf16, ~14.7 GB, 3 shards) + LoRA
  adapter (`checkpoints/`, 441 MB)
- Scripts (`scripts/eval_aerovla.sh`, `scripts/metric.sh`)

---

## 2. The user’s goal (why we are here)

The user is **NOT running the stock benchmarks**. They have:

1. **Trained their own LoRA** on **their own TravelUAV raw data**. The adapter config:
   - `base_model_name_or_path: ./openvla-7b`
   - `r=64`, `lora_alpha=128`, `lora_dropout=0.05`, CAUSAL_LM
   - `target_modules`: `[down_proj, k_proj, gate_proj, v_proj, o_proj, up_proj, q_proj]`
   - **`modules_to_save: ["projector"]`** ← important (projector is a full module, not LoRA)
   - Saved as `checkpoints/adapter_model.safetensors` (441 MB)

2. **A custom UE4/AirSim environment named `BrushifyCountryRoads`** (downloaded &
   extracted from TravelUAV-style env binaries — a "country roads" map). Raw data lives
   in `envs/BrushifyCountryRoads/data_raws/` archives and, on the H100, as
   `envs/BrushifyCountryRoads/<uuid>/` (each UUID = one episode with `mark.json`,
   telemetry, images). **Env layout schema (since 2026-09-10):** per-env folder with
   `data_raws/` (episode archives) and `engine/<Map>/` (extracted UE4, launched by the
   server via `resolve_env_launcher` → `envs/<Map>/engine/<Map>/<Map>.sh`).

3. **Wants to run closed-loop evaluation** of their LoRA on this custom map:
   `bash scripts/eval_aerovla.sh` with `TASK_ID=seen_valset/BrushifyCountryRoads`,
   executed on a **remote GPU VM (H100 80 GB)**, then bring results back locally.

The local machine is the development box (RTX 3050, 6 GB VRAM — **too small to run the
model**, verified). The evaluation itself must happen on the big-GPU VM.

---

## 3. Architecture (how eval works — 2 processes)

```
scripts/eval_aerovla.sh  ──►  src/vlnce_src/eval_aerovla.py   (client: model + decisions, GPU)
                                  │   AirVLNSimulatorClientTool (msgpack-RPC)
                                  ▼
airsim_plugin/AirVLNSimulatorServerTool.py  (msgpack-RPC server)  ─►  start FIRST
                                  │  launches env binary via env_exec_path_dict
                                  ▼
              envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/BrushifyCountryRoads.sh  (UE4 launcher, engine/<Map> schema)
                                  ▼
                        UE4 / AirSim world (renders, physics)
```

- **Server**: `airsim_plugin/AirVLNSimulatorServerTool.py` — runs from `airsim_plugin/`
  (it resolves envs as `../envs/...`; `--root_path` default `../envs`).
  - `--gpus 0 --port 30000` (port = `30000 + GPU_ID*5000`; GPU 0 ⇒ 30000).
  - Finding env to launch: `args.root_path/<exec_path>/<bash_name>.sh` where the mapping
    is in `env_exec_path_dict` (see §5).
- **Client**: `src/vlnce_src/eval_aerovla.py` — run by `scripts/eval_aerovla.sh`, connects
  to the server at `--simulator_tool_port 30000`. Uses the model wrapper to predict actions,
  sends them to the simulator, loops until success/failure/waypoint cap (maxWaypoints 200).

---

## 4. Full list of changes we made to the repo

Git status: 5 modified tracked files + several untracked new files/dirs.

### Modified files

| File | Change |
|------|--------|
| `airsim_plugin/AirVLNSimulatorServerTool.py` | Added `"BrushifyCountryRoads": {'bash_name':'BrushifyCountryRoads','exec_path':'BrushifyCountryRoads'}` to `env_exec_path_dict` (after ModernCityMap, ~line 267) so the server can find/launch the custom env. |
| `data/meta/map_spawnarea_info.json` | Added `BrushifyCountryRoads` entry: **56 spawn areas / 28 assets**. Rows built so `find_closest_area` matches every target; each row = `[bbox_min_x,y,z, bbox_max_x,y,z, euler(3), place_pos(3), quat(w,x,y,z), asset_name, scale]` (≥18 elements). Verified **0 asset mismatches** across all episodes. |
| `scripts/eval_aerovla.sh` | `MODEL_DIR="$PROJECT_ROOT/checkpoints"`, `TASK_ID="seen_valset/BrushifyCountryRoads"`. |
| `requirements.txt` | (pins as-shipped: torch 2.1.2, transformers 4.42.4, peft 0.11.1, accelerate 0.32.1, bitsandbytes 0.43.1, timm 0.9.10 — see §6 about env changes.) |
| `src/model_wrapper/aerovla_wrapper_ui.py` | **Switched model loading to 4-bit NF4** to try to fit 6 GB RTX 3050: added `BitsAndBytesConfig` import; `quantization_config=bnb_config, device_map="auto"` instead of `torch_dtype=torch.bfloat16` + `.to(device)`; pixel cast changed to `torch.bfloat16`. **NOTE:** this is a workaround only; on the H100 it should be **reverted to bf16** (see runbook §5). |

### Untracked / new files & dirs

| Path | What |
|------|------|
| `data/uav_dataset/seen_valset_splits/BrushifyCountryRoads.json` | Eval split: **123 episodes**, format `{"json":"BrushifyCountryRoads/<uuid>/merged_data.json","frame":1}`. Verified all entries resolve. |
| `dataset_raw/BrushifyCountryRoads/` | On **H100**: symlinks → `/workspaces/AeroVLA/envs/BrushifyCountryRoads/<uuid>/` (used by eval `--dataset_path`). On **local**: stale/broken symlinks to a nonexistent `raw/` (not used — episodes live on H100). |
| `docs/AEROVLA_EVAL_RUNBOOK.md` | Ops runbook for VM execution (this repo's companion). |
| `envs/` | Split zip chunks of the env (see §7 — NOT yet extracted on local). |
| `openvla-7b/`, `checkpoints/`, `TravelUAV/` | Base weights (~14.7 GB), LoRA adapter, upstream benchmark data — untracked, must exist for eval. |

### Artifacts we generated (verification results)

- Ran upstream `generate_merged_json.py`: produced `merged_data.json` beside `mark.json`
  for **123 valid sequences**; shape verified: keys `['trajectory','trajectory_raw',
  'trajectory_raw_detailed','image_feature_path','index','length','conversations']`,
  `length=84`, valid conversations, `trajectory_raw_detailed` has position/orientation.
- One sequence is **invalid and excluded**: `2cd3b36c-d435-4b13-977f-865216d77400` (no
  `log/`, no `mark.json`, crashes the generator).
- Verified spawn-area construction via `find_closest_area` for all 123 targets.

---

## 5. Key data / format facts (do not re-derive)

- **Eval split format**: `{"json": "<MapName>/<uuid>/merged_data.json", "frame": <int>}`.
  `frame` is effectively unused — each sequence = one episode.
- **`merged_data.json` must sit beside `mark.json`.** `load_my_datasets` reads
  `merged_json.replace('merged_data.json','mark.json')`.
- **Spawn-area rows** (`env_uav.py::find_closest_area`) need ≥18 elements, in order:
  `[0..2]` bbox min, `[3..5]` bbox max, `[6..8]` euler, `[9..11]` place pos,
  `[12..15]` quat (order in file `w,x,y,z`; env reads `[13],[14],[15],[12]` for
  `Quaternionr`), `[16]` asset name, `[17]` scale.
- **Server launcher resolution**: `resolve_env_launcher` → for `engine_dir`-style
  envs `args.root_path/<exec_path>/<engine_dir>/<bash_name>/<bash_name>.sh`, else
  legacy `args.root_path/<exec_path>/<bash_name>.sh` (`root_path` default `../envs` from
  `airsim_plugin/`). Brushify → `envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/BrushifyCountryRoads.sh`.
- **Model token count**: tokenizer 32001 vs base model vocab 32000 →
  `resize_token_embeddings(len(tok))` is called in the wrapper (caused a CUDA-deepcopy
  OOM on the small GPU).
- **Wrapper uses relative paths** (`./openvla-7b`, `model_args.model_path` for adapter) →
  **run from PROJECT_ROOT**.

---

## 6. Python environment (aero_vla) — what was installed

Local conda env `aero_vla` (/media/sanati/DriveE/miniconda3/envs/aero_vla, py3.10):
torch 2.1.2+cu118, torchvision 0.16.2, transformers 4.42.4, peft 0.11.1,
**accelerate 0.32.1** (0.32.x is required — newer versions break .to() on quantized),
bitsandbytes 0.43.1, timm 0.9.10, opencv-python 4.10.0.84, numpy 1.26.4 (forced back from
2.x), scipy, yacs, airsim 1.8.1 (`--no-build-isolation`), msgpack 1.1.2,
msgpack-rpc-python 0.4 (from repo zip `msgpack-rpc-python-fix-msgpack-dep.zip`), tornado
4.5.3. `opencv-contrib-python 5.0.0.93` was **uninstalled** (airsim dep, needed numpy<2).

**AirSim client patch** (troubleshooting fix): in
`<env>/lib/python3.10/site-packages/airsim/client.py:17`, removed
`pack_encoding='utf-8', unpack_encoding='utf-8'` from the `msgpackrpc.Client(...)` call to
fix 4–5 s `simGetImages` latency.

**bitsandbytes / CUDA-11 libs** (only needed on the cu118 build):
`LD_LIBRARY_PATH=$ENV/lib/python3.10/site-packages/nvidia/cusparse/lib:$ENV/lib/python3.10/site-packages/nvidia/cublas/lib:...`
(cu11 libcusparse/cublas installed via pip). Without it, `import bitsandbytes` fails.

---

## 7. Current on-disk state at this moment (BE HONEST — verify on transfer)

| Item | State |
|------|-------|
| `envs/BrushifyCountryRoads/` (env folder, **new `engine/`+`data_raws/` schema since 2026-09-10**) | Locally contains `data_raws/` (`.zip`+`.z01`+`.z02` archives) and `envs/` → reorganized to put **engine** under `envs/<Map>/engine/<Map>/` (launcher = `.../engine/BrushifyCountryRoads/BrushifyCountryRoads.sh`). **NOT yet extracted to the new `engine/` layout** (user will extract; no space now). `BrushifyForestPack` IS extracted at `envs/BrushifyForestPack/envs/BrushifyForestPack/` (to be moved into `engine/` per schema). |
| `envs/BrushifyCountryRoads.zip` + `.z01` + `.z02` | **Archive parts intact** under `envs/BrushifyCountryRoads/` (+ nested copies). `.zip` alone = complete env; `.z01` = raw episode data (contains bad-UUID `2cd3b36c...`), `.z02` = empty. Do NOT let 7z auto-merge `.z0x` when extracting — move them aside into `data_raws/`. |
| `dataset_raw/BrushifyCountryRoads/` symlinks | **On H100** point to `envs/BrushifyCountryRoads/<uuid>/` (valid, 123 uuids). **Locally** they are stale/broken symlinks → local `raw/` (not used; episodes live on H100). If ever needed locally, extract `.z01` alone. |
| `openvla-7b/` base weights | **Present** (~14.7 GB, 3 safetensors shards + trust-remote-code `.py` files). |
| `checkpoints/` LoRA adapter | **Present** (`adapter_config.json` + `adapter_model.safetensors`, 441 MB). |
| Split JSON | **Present** (123 episodes). |
| Generated `merged_data.json` / `mark.json` | On H100 under the extracted raw tree (`merged_data.json` beside `mark.json` — already generated there). |
| map_spawnarea_info / server mapping / eval script | **Present** (modified files above). |
| `eval_results/` | **DONE — synced locally (2026-09-09).** `eval_results/checkpoints/seen_valset/BrushifyCountryRoads/` = 123 episode dirs (50 `success_`, 73 plain → SR ≈ 40.65%). Each has `log/`, `ori_info.json`, `object_description.json`, camera dirs. No CSVs (metric.sh never ran). **2026-09-09 (cont.): the H100 copy of these 123 was moved to `BrushifyCountryRoads.bak_20260909_priorCPU123` and a fresh 123-ep split re-run is ACTIVE over the tunnel** (see "Split full-run state" row). |
| Split tooling | `scripts/split.sh` (**python-env FIXED 2026-09-09**: uses `aero_vla` python via `$SERVER_PYTHON` + deps check), `HOST=0.0.0.0` applied to `AirVLNSimulatorServerTool.py:696`. **Smoke test VERIFIED end-to-end** (local server `0.0.0.0:30000`, reverse tunnel up, H100→`127.0.0.1:30000` = OK). |
| Split tooling MSGPACK | **2026-09-09: H100 venv must have `msgpack==1.1.2`** (was 1.2.2 → msgpack-RPC framing breaks; server crashes `transport/tcp.py:27` on first real RPC; client silently dies). Fixed + verified via real RPC `ping`. |
| Split full-run state | **2026-09-09: FULL 123-ep SPLIT RE-RUN ACTIVE + VERIFIED WRITING RESULTS** to fresh `eval_results/checkpoints/seen_valset/BrushifyCountryRoads/` (ubuntu-owned). Prior 123 CPU results moved to `BrushifyCountryRoads.bak_20260909_priorCPU123`. At last check **10/123** done (1 success, 3 oracle, rest plain), client alive, ~5 h projected. |

**Implication for local env use:** the local UE4 server now resolves the launcher via
`envs/<Map>/engine/<Map>/<Map>.sh`. Brushify must be unpacked into that layout before
`split.sh --windowed` will pass its sanity check. Do **not** merge `.z01`/`.z02`
into it (corrupt/empty, separate contents). The H100 client needs only the model weights
and the eval script, not `raw/`.

---

## 8. Known blockers & findings already proven

- **Local GPU cannot run the eval.** RTX 3050 = 6 GB VRAM, single device. openvla-7b in
  bf16 = 15.1 GB → immediate OOM. In **4-bit NF4** we still OOM: base+vision+embeddings ≈
  4.79 GB used; LoRA injection / `resize_token_embeddings` / inference all OOM
  (GPU has ~63 MB free after base load). **Empirically, ≥12 GB (ideally 16–24+) is
  required.** Hence: run on the H100 VM, and prefer **bf16** (highest quality).
- **`modules_to_save: ["projector"]` + 4-bit quantization conflict**: peft's
  `ModulesToSaveWrapper` needs fp tensors to set `requires_grad`; a bnb-4bit `projector`
  fails `RuntimeError: only Tensors of floating point dtype can require gradients`.
  Fix (tested): rebuild `model.projector` as bf16 `nn.Sequential` of `nn.Linear`s matching
  `PrismaticProjector` (`fc1/fc2`, or `fc1/fc2/fc3` for fused) before applying PeftModel.
- The env extraction is currently absent locally (see §7) — must be re-done on the VM.

---

## 9. Next steps (what the agent/run should do)

> **CURRENT STATUS (2026-09-09):** the full H100 eval is **COMPLETE — 123/123**
> (SR ≈ 50/123 ≈ 40.65%) and results are **synced locally** to
> `eval_results/checkpoints/seen_valset/BrushifyCountryRoads/`. Steps 1-6 done.
> Step 7 (aggregate metrics) is **DEFERRED** (user: "let skip this and go on our
> plan") — see note below. Step 8 (pull) done.
> **Split path smoke-tested and VERIFIED end-to-end (2026-09-09)** — ready for a
> fast future re-run (local UE4 server + reverse tunnel + H100 client).
> **2026-09-09 (cont.): SPLIT FULL RE-RUN ACTIVE + VERIFIED.** After fixing 3
> blockers (msgpack framing, "nothing to evaluate" silent exit, root-owned save
> dir) the full 123-ep eval over the split infra is RUNNING and writing results
> (~5 h projected). Prior CPU results backed up as
> `BrushifyCountryRoads.bak_20260909_priorCPU123`. Details + runbook in
> `docs/SPLIT_RUNBOOK.md`.
> **2026-09-09: `docs/ADD_ENV_RUNBOOK.md` created** — end-to-end procedure for
> adding ANOTHER env (unzip → env_exec_path_dict → spawn areas → H100 episodes +
> symlinks → split json → run_eval.sh → launch). Derived from the Brushify
> integration; verified live-image eval path (no feature.tensor needed).

Primary path (as documented in `docs/AEROVLA_EVAL_RUNBOOK.md`):
1. **Transfer** the project to the H100 VM (rsync/tar; the env chunks + base weights +
   adapter are what matter; `raw` and `merged_data` will be regenerated on the VM).
2. **Extract** the env: join & unzip the multi-part archive into `envs/` (verify
   `envs/BrushifyCountryRoads/raw/...` and the `BrushifyCountryRoads.sh` launcher exist).
3. **Recreate data**: run upstream `generate_merged_json.py` to regenerate the 123
   `merged_data.json` next to `mark.json`; recreate `dataset_raw/BrushifyCountryRoads/`
   symlinks (or point `--dataset_path` at the extracted tree).
4. **Recreate `aero_vla` env on the VM** per §6 (README install steps + AirSim patch +
   msgpack zip). On H100, prefer a cu12+ torch + matching bitsandbytes; **revert the
   wrapper to bf16** (runbook §5 shows the original load block from `git diff`).
5. **Start the server**: `cd airsim_plugin && (xvfb-run -a) python
   AirVLNSimulatorServerTool.py --gpus 0 --port 30000` (headless ⇒ xvfb).
6. **Run the eval**: `cd /path/to/AeroVLA && bash scripts/eval_aerovla.sh` (123 episodes,
   ~long-running). Results → `eval_results/checkpoints/seen_valset/BrushifyCountryRoads/`.
7. **Aggregate**: `bash scripts/metric.sh` → `evaluation_detailed.csv` +
   `evaluation_summary_aggregated.csv`. **NOTE (2026-09-09):** not yet run —
   deferred by user. To run: needs per-episode GT `merged_data.json` (only on
   H100; local `raw/` absent). Either scp GT from H100 (tiny, ~62 KB) into
   `envs/BrushifyCountryRoads/raw/<uuid>/` so local `dataset_raw` symlinks
   resolve, or run `metric.sh` on the H100. Also fix `metrics.sh` `RESULTS_DIR`
   (`eval_results/aerial_vla` → actual `eval_results/checkpoints`).
8. **Pull results back** to local via rsync/scp into `eval_results/`. ✅ DONE
   for the completed run (123 dirs local).

**FUTURE FAST RE-RUN (split, VERIFIED 2026-09-09):** instead of CPU-rendering on the
H100 (~20 h/123 eps), render on the local RTX 3050 over a reverse tunnel:
1. Local: `bash scripts/split.sh` (starts local UE4 server + `ssh -R 30000..30016` tunnels).
2. H100: `bash scripts/run_eval.sh` (client, unchanged — goes through the tunnel).
Details in `docs/SPLIT_RUNBOOK.md` (tunnel approach, §3T/§4). Local machine must
stay on + tunnel stable for the whole eval; leftover procs can be stopped with
`kill $(cat /tmp/aerovla_server.pid /tmp/aerovla_tunnel.pid)`.
**IMPORTANT (2026-09-09):** before step 2, on the H100 make `eval_save_path`
fresh: back up existing results (`mv ...BrushifyCountryRoads
...BrushifyCountryRoads.bak_<tag>`), `mkdir` a new one, and `chown ubuntu:ubuntu`
it (root-created = PermissionError crash). Also ensure venv
`msgpack==1.1.2` (run once: `.venv/bin/pip install msgpack==1.1.2`). The prior
123-dir backup is kept in the same dir as `BrushifyCountryRoads.bak_20260909_priorCPU123`.

Alternate (smaller GPU only, not this case): 4-bit path with CUDA-11 libs + LD_LIBRARY_PATH
(empirically fails at 6 GB; needs ≥ 12 GB).

---

## 10. Quick file map (where things live)

```
README.md                                  # official project intro
docs/
  assets/troubleshooting.md                 # AirSim RPC fix
  AEROVLA_EVAL_RUNBOOK.md                  # ops runbook (VM)
  SPLIT_RUNBOOK.md                         # split/windowed launch runbook
  ADD_ENV_RUNBOOK.md                       # how to add a NEW env to evaluate (2026-09-09)
  PROJECT_HANDOFF.md                       # this file
airsim_plugin/AirVLNSimulatorServerTool.py # msgpack-RPC server; env_exec_path_dict (~line 241)
src/model_wrapper/aerovla_wrapper_ui.py    # model load/action wrapper (4-bit edit pending revert)
src/vlnce_src/eval_aerovla.py              # closed-loop eval client
scripts/eval_aerovla.sh                    # eval entry (TASK_ID=seen_valset/BrushifyCountryRoads)
scripts/metric.sh                          # SR/OSR/NE/SPL aggregation
data/meta/map_spawnarea_info.json          # per-map spawn areas (Brushify added)
data/meta/object_description.json          # object descriptions for prompts
data/uav_dataset/seen_valset_splits/BrushifyCountryRoads.json  # 123-episode split
dataset_raw/BrushifyCountryRoads/          # symlinks -> envs/.../raw/<uuid>
envs/BrushifyCountryRoads.(zip|z01|z02)    # multi-part env archive (NOT extracted locally)
openvla-7b/                                # base model (14.7 GB) + trust-remote-code files
checkpoints/                               # user LoRA adapter (441 MB)
TravelUAV/                                 # upstream benchmark materials
```

---

## 11. Jargon glossary (so the next agent isn't confused)

- **VLA / VLN**: Vision-Language-Action / Vision-Language-Navigation.
- **LoRA adapter (peft)**: small trainable low-rank deltas applied to base `openvla-7b`;
  `modules_to_save=["projector"]` means the vision projector is saved/loaded as full
  weights (not LoRA).
- **`merged_data.json`**: per-episode file combining telemetry + image feature paths +
  prompt/answer conversations, consumed by the eval data loader.
- **`mark.json`**: TravelUAV per-episode metadata (target object, bbox, asset), needed by
  the spawn-area matcher.
- **`map_spawnarea_info.json`**: map → list of ground-truth spawn areas (asset + pose) used
  to place the agent/target.
- **`AirVLNSimulatorServerTool.py`**: the msgpack-RPC "world server" that owns UE4/AirSim
  and spawns the environment binary.
- **`find_closest_area`**: matches a merged target to the nearest spawn area row.
- **SR/OSR/NE/SPL**: Success Rate, Oracle Success Rate, Navigation Error, Success weighted
  by Path Length — TravelUAV eval metrics.
- **`image_feature_path`** in merged_data: relative path to cached vision features/images.

---

## 12. Crucial gotchas (transfer these to the next session)

1. **Run everything from PROJECT_ROOT** (wrapper uses `./openvla-7b` and
   `./checkpoints`; eval uses relative `./dataset_raw/`).
2. **Server first, then eval.** They talk over msgpack-RPC on the same port
   (GPU 0 ⇒ 30000). Eval will fail to connect if the server isn't up.
3. **Headless VM ⇒ use `xvfb-run -a`** for the server so UE4 can open a virtual display.
4. **On H100: use bf16, not 4-bit.** Revert the wrapper change (see `git diff
   src/model_wrapper/aerovla_wrapper_ui.py` for the exact original code).
5. **`merged_data.json` was regenerated on the H100** (sits beside `mark.json`
   under `dataset_raw/BrushifyCountryRoads/<uuid>/`). It is NOT local (local
   `raw/` only in the corrupt `.z01`). For local metric runs, scp the GT JSONs
   from H100 (tiny) into `envs/BrushifyCountryRoads/raw/<uuid>/`.
6. **accelerate must be 0.32.x**; newer breaks quantized `.to()`.
7. **airsim `client.py` utf-8 patch** is mandatory or each sim step is 4–5 s.
8. **`scripts/split.sh` needs the `aero_vla` conda python** (`msgpackrpc`+`tornado`+
   `airsim`) — base miniconda python lacks `msgpackrpc`. split.sh now defaults to
   `$SERVER_PYTHON=/media/.../envs/aero_vla/bin/python` (override via
   `AEROVLA_SERVER_PYTHON`), checked at launch.
9. **Detect the split server with `ss -ltnp | grep ':30000'`** — do NOT use
   `pgrep -af "AirVLNSimulatorServerTool\|ssh -o ..."`; a literal `\|` matches
   nothing and reports a false "DEAD".
10. **Tornado `Uncaught exception` lines in `/tmp/aerovla_server.log` are
     benign** — from the H100's `/dev/tcp` port-probe closing mid-msgpack-RPC; the
     server stays up.
11. **Debugging the split client: remember it dies SILENTLY if `eval_save_path`
     is non-empty (all split entries already done) or if the dir is root-owned
     (PermissionError).** Both killed the eval with no useful traceback on
     2026-09-09 before being diagnosed. See `docs/SPLIT_RUNBOOK.md` §6 items 6-7.
12. **If a scene re-open fails later in a long split run**, the UE4 process
     survives client deaths — it may need a manual restart
     (`kill <ue4_pid>` + relaunch via the server) rather than killing the server.
```
