# ADD_ENV_RUNBOOK — Add a new UE4 environment to the AeroVLA closed-loop eval

> **Status**: ACTIVE (created `2026-09-09`). Mirrors exactly how
> `BrushifyCountryRoads` was integrated end-to-end.
> Companion docs: `SPLIT_RUNBOOK.md` (split/windowed launch), `AEROVLA_EVAL_RUNBOOK.md`
> (eval ops), `PROJECT_HANDOFF.md` (project context).

This runbook tells you everything needed to add **another** UE4 environment
(call it `<Map>`) to evaluate the custom LoRA on, in the **split** setup
(local UE4 server + H100 model client). It is derived by reverse-engineering
the working `BrushifyCountryRoads` integration — every step listed here is
known to be required for that env.

---

## 0. Flow overview

```
 [RAW EPISODE DATA]            [UE4 ENV BINARY]       [REGISTRIES]
 envs/<Map>/<uuid>/      +     envs/<Map>/            env_exec_path_dict
 (mark.json, log/,            (launcher .sh +         map_spawnarea_info.json
  merged_data.json)            bin + pak + Engine)    object_description.json
       |                            |                          |
       v                            v                          v
 dataset_raw/<Map>/<uuid> -> symlink -> envs/<Map>/<uuid>     (used by find_closest_area
 (H100, --dataset_path)                                        + setObjects)
       |                              |
       +---- data/uav_dataset/<CAT>_splits/<Map>.json  (the eval split list)
       +---- run_eval.sh (H100) sets --eval_save_path/--eval_json_path
                                     |
                                     v
        LOCAL: UE4 server + ssh -R tunnels   <--  H100: model client (eval)
        bash scripts/split.sh --windowed            bash scripts/run_eval.sh
                                     |
                                     v
                eval_results/checkpoints/<CAT>/<Map>/  (per-episode json)
```

Important: **eval does NOT need `feature.tensor` or preprocessed images.**
During closed-loop eval the model is fed **live AirSim camera images**
(`env_uav.py:358 getImageResponses()` → 5 RGB + 5 depth from the running UE4
server). The dataset needs only the **trajectory + spawn info** (`mark.json` +
`merged_data.json`), and the UE4 env must be able to spawn the object assets.

---

## 1. Get the UE4 env binary onto the LOCAL box → `envs/<Map>/engine/<Map>/`

> **Layout schema per env (canonical, since 2026-09-10):**
> ```
> envs/<Map>/
> ├── data_raws/                    # raw episode archives (.zip + .z01/.z02 parts)  — NOT used by server
> │   └── <Map>.zip (+ .z01 ...)    # raw episode data parts
> └── engine/                       # extracted UE4 (what the local server launches)
>     └── <Map>/                    #   <- the extracted dir
>         ├── <Map>.sh              #   launcher script
>         ├── <Map>/                #   Binaries/Linux/<Map>, Content/Paks/*.pak
>         └── Engine/
> ```
> The server resolves the launcher as
> `envs/<Map>/engine/<Map>/<Map>.sh` (via `resolve_env_launcher`,
> `AirVLNSimulatorServerTool.py`). The UE4 box launches the binary from
> `envs/<Map>/engine/<Map>/`. Steps used for `BrushifyCountryRoads`:

```bash
cd /home/sanati/projs/sensreson/AeroVLA/envs/<Map>
# New env arrives as <Map>.zip (+ maybe .z01/.z02 parts).
# CRITICAL: do NOT let 7z auto-merge the .z0x parts.
#   - <Map>.zip in data_raws/  = raw episode-data parts (some corrupt/empty)
#   - <Map>.zip under engine/  = the complete standalone UE4 env (launcher + bin + .pak + Engine)
mkdir -p data_raws engine
# move the raw episode archive parts aside into data_raws/
mv <Map>.z01 <Map>.z02 data_raws/ 2>/dev/null || true
# extract the ENGINE zip ALONE into engine/
mkdir -p engine && cd engine
7z x -y -o. ../<Map>.zip          # extract .zip ALONE
cd ..
```

Verify the layout (must contain the launcher + binary + pak):

```bash
ls -la envs/<Map>/engine/<Map>/
test -x envs/<Map>/engine/<Map>/<Map>.sh && echo OK
test -x envs/<Map>/engine/<Map>/<Map>/Binaries/Linux/<Map> && echo OK   # .sh runs this binary
```

Brushify reference layout (once extracted):
`envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/` with
`BrushifyCountryRoads.sh` + `BrushifyCountryRoads/` `Binaries/Content/...` +
`Engine/`; the `.sh` runs `Binaries/Linux/BrushifyCountryRoads`.

The **raw episode data** (`mark.json`, `log/`, 5× rgb + 5× depth) does NOT go
into the local env dir — see §4.

---

## 2. Register the map in the server registry

**File**: `airsim_plugin/AirVLNSimulatorServerTool.py`, dict `env_exec_path_dict`
(line ~241). Add an entry whose key is the map name **as it appears in the
spawn-area json / eval flow**:

```python
"<Map>": {
    'bash_name': '<Map>',
    'exec_path': '<Map>',
    'engine_dir': 'engine',
},
```

- `exec_path` + `engine_dir` + `bash_name` resolve via `resolve_env_launcher`
  (`AirVLNSimulatorServerTool.py`) to
  `os.path.join(root_path, exec_path, engine_dir, bash_name, bash_name + '.sh')`.
  With `--root_path` default `../envs` and the schema above:
  `exec_path='<Map>', engine_dir='engine'` → `envs/<Map>/engine/<Map>/<Map>.sh`.
- If the env is a Carla-style shared env whose launcher sits inside a fixed
  subdir, omit `engine_dir` and it falls back to the legacy resolution
  `os.path.join(root_path, exec_path, bash_name + '.sh')`.
- **Prefix matching** (lines ~522-526): any scene id starting with a registered
  key is matched to that entry — you can register the basename and eval frees a
  longer name, or re-use an existing env key if the new env is a variant.

Sanity check after editing:
```bash
bash -n airsim_plugin/AirVLNSimulatorServerTool.py
AEROVLA_SERVER_PYTHON=<your-python-with-msgpackrpc> python -c \
  "import ast,sys; src=open('airsim_plugin/AirVLNSimulatorServerTool.py').read();
   ast.parse(src); print('OK')"
```

(Commit 1 of the Brushify integration changed server-side only in this file;
the H100 client does not list maps — it sends the map name and the server
resolves it.)

---

## 3. Register spawn areas → `data/meta/map_spawnarea_info.json`

**File**: `data/meta/map_spawnarea_info.json` — top-level dict keyed by map name,
each value a **list** of spawn-area rows.

Row format (18 fields, index `r`):

| idx | meaning | source |
|-----|---------|--------|
| 0-2 | bbox min `[x,y,z]` | mark.target.position - (1,1,0.5) |
| 3-5 | bbox max `[x,y,z]` | mark.target.position + (1,1,0.5) |
| 6-8 | euler `[0,0,0]` | zero |
| 9-11 | spawn/place pos | **mark.target.position** (replicated) |
| 12-15 | quat `[1,0,0,0]` | identity |
| 16 | asset_name | mark.object_name (e.g. `AASM_VolkswagenBeetle`) |
| 17 | scale `1.0` | 1.0 |

How it is used (`env_uav.py`):
- `find_closest_area` (line 61): skips rows with `len(area) < 18`, computes
  anchor `[area[0]+1, area[1]+1, area[2]+0.5]`, returns nearest to the object
  position.
- `load_my_datasets` (line 114+): for each episode reads `mark.json`
  (`object_name`, `target.position`), calls `find_closest_area`, builds
  `trajectory` from `merged_data.json['trajectory_raw_detailed']`, and sets the
  object info from the nearest area row (`env_uav.py` line ~137-150).
- `closeloop_util.py:142` resolves the natural-language object description from
  `object_description.json` via `asset_name.replace("AA","")`.

So the generation rule: **one row per distinct `(object_name, target.position)`
seen in the new env's episodes, with the row's `asset_name == mark.object_name`
and `place_pos == mark.target.position`.** In Brushify the 123-episode set
collapsed into 56 rows / 28 asset types. Generate it from the episode marks:

```python
# run anywhere the episodes' mark.json files live (e.g. on H100)
import json, glob, os
rows = []
seen = set()
for m in glob.glob('envs/<Map>/*/mark.json'):
    d = json.load(open(m))
    key = (d['object_name'], tuple(d['target']['position']))
    if key in seen:
        continue
    seen.add(key)
    x, y, z = d['target']['position']
    rows.append([x-1, y-1, z-0.5, x+1, y+1, z+0.5, 0, 0, 0,
                 x, y, z, 1.0, 0.0, 0.0, 0.0, d['object_name'], 1.0])
sp = json.load(open('data/meta/map_spawnarea_info.json'))
sp['<Map>'] = rows
json.dump(sp, open('data/meta/map_spawnarea_info.json', 'w'), indent=2)
```

Notes:
- A few episodes may not snap to *any* row (Brushify had 2/131 with 35-50 m
  distance) — the env appears to still work (falls back to a coarse row). Don't
  panic if not every episode snaps exactly; the row union just needs to exist.
- The same json must exist on **H100** (`/workspaces/AeroVLA/data/meta/...`) —
  it is read by the model client, not the server.

---

## 4. Put episode data on the H100 → `envs/<Map>/<uuid>/` + symlinks

The model client reads `--dataset_path` (default `./dataset_raw/`) and for each
entry `{"json": "<Map>/<uuid>/merged_data.json", "frame": 1}`:
- joins `dataset_path` + `item['json']`
- requires `merged_data.json` AND – via `merged_json.replace('merged_data.json','mark.json')` –
  `mark.json` **beside it** (lines 127-152).
- **skips any episode already present in `eval_save_path`** (fresh/empty dir required).

On H100:
```bash
ssh main.copper.sanati-emp.coder
cd /workspaces/AeroVLA

# 1. raw episode dirs land under envs/<Map>/<uuid>/ (mirrors Brushify)
#    per uuid: mark.json + merged_data.json + object_description.json,
#    11 dirs: log/, downcamera/, downcamera_depth/, frontcamera/, frontcamera_depth/,
#    leftcamera/, leftcamera_depth/, rearcamera/, rearcamera_depth/,
#    rightcamera/, rightcamera_depth/

# 2. dataset_raw/<Map>/ symlinks -> envs/<Map>/<uuid>
mkdir -p dataset_raw/<Map>
for u in envs/<Map>/*/; do
  [ -e "${u}merged_data.json" ] || continue
  ln -sfn "/workspaces/AeroVLA/envs/<Map>/$(basename $u)" "dataset_raw/<Map>/$(basename $u)"
done
ls dataset_raw/<Map> | wc -l   # == episode count (123 for Brushify)
```

Each `envs/<Map>/<uuid>/merged_data.json` is produced by
`TravelUAV/Model/LLaMA-UAV/tools/generate_merged_json.py` (upstream tool,
`--root_dir` + `--map_list`; outputs keys `trajectory, trajectory_raw,
trajectory_raw_detailed, image_feature_path, index, length, conversations`).
Crashes on bad/corrupt episodes (Brushify: `2cd3b36c-...`) → exclude those uuids
from the split.

### 4b. Optional: preprocess features (training only — NOT needed for eval)
`TravelUAV/Model/LLaMA-UAV/tools/preprocess_image2tensor.py` writes the
`feature.tensor` pixels (`image_feature_path`) that **training** consumes. The
closed-loop eval uses live AirSim images instead, so you can skip it.

---

## 5. Create the eval split json → `data/uav_dataset/<CATEGORY>_splits/<Map>.json`

The eval runner reads `--eval_json_path`. Brushify's split is
`data/uav_dataset/seen_valset_splits/BrushifyCountryRoads.json`, 123 entries,
one per episode, **sorted** paths, all `"frame": 1`:

```json
[
  {"json": "BrushifyCountryRoads/0008c004-9c02-40d3-928f-b7228c17a39d/merged_data.json", "frame": 1},
  ...
]
```

No split-generator exists in the repo tools — the 123-episode split was crafted
manually: list the valid uuids (exclude ones with bad merged_data), sort, and
emit the entries. Template:

```bash
cd /workspaces/AeroVLA   # H100
mkdir -p data/uav_dataset/seen_valset_splits
python3 - <<'PY'
import json, os, glob
uuids = sorted(b for b in os.listdir('envs/<Map>')
               if os.path.isfile(f'envs/<Map>/{b}/merged_data.json'))
split = [{"json": f"<Map>/{u}/merged_data.json", "frame": 1} for u in uuids]
with open(f'data/uav_dataset/seen_valset_splits/<Map>.json', 'w') as f:
    json.dump(split, f, indent=2)
print(len(split), 'episodes')
PY
```

`eval` also reads `--object_name_json_path data/meta/object_description.json`
(a **list** of `{object_name, object_desc}`). If `<Map>` uses **new asset names**
not yet listed (check `mark.json` names vs the list), append entries:

```python
# data/meta/object_description.json is a list; add anything missing for <Map>
```

---

## 6. H100 launcher → edit `scripts/run_eval.sh`

The H100 client launcher `scripts/run_eval.sh` hardcodes Brushify (as fetched),
mirroring `scripts/eval_aerovla.sh` param resolution:

```
--dataset_path ./dataset_raw/
--eval_save_path ./eval_results/checkpoints/seen_valset/BrushifyCountryRoads
--eval_json_path ./data/uav_dataset/seen_valset_splits/BrushifyCountryRoads.json
--map_spawn_area_json_path ./data/meta/map_spawnarea_info.json
--object_name_json_path ./data/meta/object_description.json
--simulator_tool_port 30000 --batchSize 1 --maxWaypoints 200
```

For `<Map>`:
```bash
# on H100: /workspaces/AeroVLA/scripts/run_eval.sh
#  --eval_save_path ./eval_results/checkpoints/seen_valset/<Map>
#  --eval_json_path ./data/uav_dataset/seen_valset_splits/<Map>.json
# everything else unchanged
```

Also mirror the same env on the local `scripts/eval_aerovla.sh` if you want the
no-tunnel path (set `TASK_ID="seen_valset/<Map>"`; note `SAVE_DIR` resolves to
`eval_results/${EXP_NAME}/...` there).

**Three hard gotchas** (verified the hard way):
1. **H100 venv msgpack version must be `1.1.2`** — msgpackrpc+msgpack mismatch
   breaks the socket JSON-RPC. Check/fix: `pip install msgpack==1.1.2`.
2. **`eval_save_path` must be empty/fresh** — `load_my_datasets` skips episodes
   already present in the save dir; a reused dir silently produces
   `RESULT_COUNT < total`.
3. **`chown ubuntu:ubuntu <eval_save_path>` before running** — the eval runs as
   user `ubuntu`; a `root`-owned save dir fails writes.

---

## 7. Launch the split eval (local server + H100 client)

```bash
# LOCAL box
cd /home/sanati/projs/sensreson/AeroVLA
bash scripts/split.sh --windowed
#   - starts local UE4 server (nohup), opens ssh -R tunnels 30000..30016
#   - pids: /tmp/aerovla_server.pid, /tmp/aerovla_tunnel.pid
#   - logs: /tmp/aerovla_server.log, /tmp/aerovla_tunnel.log
#   - verifies H100 -> 127.0.0.1:30000 through the tunnel
```

Expected local output: `BrushifyCountryRoads.sh -windowed -ResX=1280 ...`
spawned; `OK: H100 -> 127.0.0.1:30000 reachable`.

```bash
# H100 (separate terminal)
ssh main.copper.sanati-emp.coder
cd /workspaces/AeroVLA
nohup bash scripts/run_eval.sh > /tmp/split_eval_<Map>.log 2>&1 &
```

Monitor:
```bash
tail -f /tmp/split_eval_<Map>.log            # model loads 3/3 shards, then Step N/200, Completed M/123
ls eval_results/checkpoints/seen_valset/<Map> | grep -c success_   # == episodes done
watch -n 60 'ls eval_results/checkpoints/seen_valset/<Map> | wc -l'
```

Expected: ~30 s/ep → 123 eps ≈ 1.5-5 h depending on windowed/offscreen.
Brushify windowed hit 25/123 in ~16 min.

Stop everything:
```bash
# LOCAL
kill $(cat /tmp/aerovla_server.pid) $(cat /tmp/aerovla_tunnel.pid)
# H100
pkill -f 'eval_aerovla.py|run_eval.sh'   # after verifying results are saved
```

---

## 8. Metrics (aggregation still deferred — see runbook notes)

The canonical aggregator is `scripts/metric.sh`, but its
`RESULTS_DIR=eval_results/aerial_vla` path does NOT match the current
`eval_results/checkpoints/...` location — this is still an open task
(see `PROJECT_HANDOFF.md` §next steps). Until fixed, the quick per-run success
count is `ls ... | grep -c success_` and
`SR ≈ success_/(success_+failure_)`. Do not rely on `metric.sh` output as-is.

---

## 9. Checklist (do all of these before declaring the env ready)

- [ ] Env extracted to `envs/<Map>/engine/<Map>/` (verify `<Map>.sh` + binary + pak).
- [ ] `env_exec_path_dict` entry added; `bash -n`/`ast.parse` OK.
- [ ] `data/meta/map_spawnarea_info.json` has a `<Map>` key (rows from mark.json).
- [ ] `data/meta/object_description.json` covers `<Map>`'s asset names.
- [ ] H100: episodes present at `envs/<Map>/<uuid>/` (mark.json + merged_data.json + log/ + images).
- [ ] H100: `dataset_raw/<Map>/<uuid>` symlinks created, count matches split.
- [ ] H100: split json at `data/uav_dataset/seen_valset_splits/<Map>.json` (frame=1, valid uuids only).
- [ ] H100 `run_eval.sh`: eval_save_path + eval_json_path point at `<Map>`.
- [ ] H100 venv msgpack `1.1.2`; save dir fresh+empty; owned by `ubuntu`.
- [ ] `split.sh --windowed` launches server+tunnels; H100 reaches 127.0.0.1:30000.
- [ ] Eval completes 123/123 into `eval_results/checkpoints/seen_valset/<Map>/`.
- [ ] Docs updated per AGENTS.md (journal / handoff / this runbook if anything changed).

---

## 10. Quick reference — file/line anchors

| what | file:line |
|------|-----------|
| Server env registry | `airsim_plugin/AirVLNSimulatorServerTool.py:241` (`env_exec_path_dict`) |
| Server launcher resolver | `AirVLNSimulatorServerTool.py:307` (`resolve_env_launcher`, uses `engine_dir`) |
| Server launch cmd builder | `AirVLNSimulatorServerTool.py:443` (`make_env_launch_cmd`) |
| Server open-scenes resolution | `AirVLNSimulatorServerTool.py:516-526` |
| Server root_path default | `AirVLNSimulatorServerTool.py:704` (`../envs`) |
| spawn-area read + closest-area snap | `src/vlnce_src/env_uav.py:57`, `:61` |
| dataset load (mark/merged, skip-existing) | `src/vlnce_src/env_uav.py:114-155` |
| live camera obs (5 rgb + 5 depth) | `env_uav.py:358`; client `AirVLNSimulatorClientTool_AeroVLA.py:524` |
| rpc open_scenes call | `AirVLNSimulatorClientTool_AeroVLA.py:194` |
| object description lookup | `src/vlnce_src/closeloop_util.py:76-88,:142` |
| split launcher (local) | `scripts/split.sh` (`--windowed`) |
| eval launcher (H100) | `scripts/run_eval.sh` (Brushify-hardcoded) |
| eval launcher (local alt) | `scripts/eval_aerovla.sh` (TASK_ID resolution) |
| merged-json generator | `TravelUAV/Model/LLaMA-UAV/tools/generate_merged_json.py` |
