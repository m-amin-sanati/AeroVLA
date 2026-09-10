# AeroVLA — BrushifyCountryRoads Closed‑Loop Eval Runbook

Run the closed‑loop evaluation for the custom `BrushifyCountryRoads` environment on
a remote VM **with a large GPU (e.g. H100 80GB)**, then pull the results back to the
local machine.

This runbook assumes the AeroVLA project was fully prepared on this laptop and is now
being transferred to the VM. All data‑generation and config steps are **already done**;
only environment setup + execution remain.

---

## 1. What is already done (do NOT redo on the VM)

| # | Artifact | Location |
|---|----------|----------|
| 1 | 123 `merged_data.json` trajectories (+ `mark.json` beside each) | `dataset_raw/BrushifyCountryRoads/<uuid>/` |
| 2 | `map_spawnarea_info.json` `BrushifyCountryRoads` entry (56 areas, 28 assets), verified 0 asset mismatches | `data/meta/map_spawnarea_info.json` |
| 3 | Eval split (123 episodes, `frame:1`) | `data/uav_dataset/seen_valset_splits/BrushifyCountryRoads.json` |
| 4 | Server mapping | `airsim_plugin/AirVLNSimulatorServerTool.py` → `env_exec_path_dict` has `"BrushifyCountryRoads": {bash_name, exec_path:'BrushifyCountryRoads'}` |
| 5 | Eval script pointed at checkpoint + Brushify task | `scripts/eval_aerovla.sh` → `MODEL_DIR=checkpoints/`, `TASK_ID=seen_valset/BrushifyCountryRoads` |
| 6 | UE4 launcher exists (`#!/bin/sh`) | `envs/BrushifyCountryRoads/BrushifyCountryRoads.sh` |
| 7 | raw env binary present | `envs/BrushifyCountryRoads/BrushifyCountryRoads/Binaries/Linux/BrushifyCountryRoads` |

> `2cd3b36c-d435-4b13-977f-865216d77400/` in the raw set is **invalid** (no `log/`, no
> `mark.json`) and is correctly excluded from all splits/areas.

---

## 2. Architecture (why two processes)

The eval is split into a **client** (runs the model, decides actions) and a **server**
(hosts the UE4/AirSim environment, executes physics, returns observations).

```
eval_aerovla.sh  ──►  eval_aerovla.py (client wrapper + model, GPU)
                        │  AirVLNSimulatorClientTool  (msgpack-RPC)
                        ▼
AirVLNSimulatorServerTool.py  (msgpack-RPC server, launches UE4 binary)   ◄── start FIRST
                        │
                        ▼  ./envs/BrushifyCountryRoads/BrushifyCountryRoads.sh
                     UE4 / AirSim world
```

- Server: `airsim_plugin/AirVLNSimulatorServerTool.py`
- Ports (GPU_ID=0 → port 30000, DDP master 80005).

---

## 3. Transfer the project to the VM

From the **local** machine (replace host/user/path):

```bash
# rsync only what's needed; skip huge caches & git history
rsync -avz --progress \
  --exclude '.git' \
  --exclude '**/__pycache__' \
  --exclude '*.pth' \
  /home/sanati/projs/sensreson/AeroVLA/ \
  user@VM_HOST:/path/to/AeroVLA/
```

If you prefer a tar:

```bash
cd /home/sanati/projs/sensreson/AeroVLA && tar czf /tmp/aerovla.tgz \
  --exclude='.git' --exclude='**/__pycache__' --exclude='*.pth' .
scp /tmp/aerovla.tgz user@VM_HOST:/tmp/
# on VM:
#   mkdir -p /path/to && tar xzf /tmp/aerovla.tgz -C /path/to/AeroVLA
```

> The `envs/BrushifyCountryRoads/.../BrushifyCountryRoads` binary is ~165 MB; the
> `openvla-7b/` base is ~22 GB; `dataset_raw` is symlinked to `envs/.../raw` so it
> transfers automatically.

---

## 4. Create the Python environment on the VM (H100)

The H100 has 80 GB VRAM, so **bf16 full‑precision** works (highest action quality).
Recommended: build a fresh env from `requirements.txt` and revert the 4‑bit wrapper
change (see §5). The 4‑bit path (§6, alternate) is only needed for GPUs ≤ ~12 GB.

```bash
conda create -n aero_vla python=3.10 -y
conda activate aero_vla
pip install -r requirements.txt

# AeroVLA additionally needs the AirSim client + patched msgpack-rpc:
pip install tornado==4.5.3
pip install msgpack-rpc-python-fix-msgpack-dep.zip   # not in this repo root .zip is present
pip install airsim==1.8.1 --no-build-isolation
pip install --force-reinstall msgpack==1.1.2
```

> **Install the CUDA‑12 (or future cuda) bitsandbytes** matching your H100 torch build.
> For H100 (Hopper) use torch cu124/cu128 (e.g. `torch 2.4+`). If you keep
> `requirements.txt`'s torch 2.1.2, install the CUDA‑11 libs shown in §7‑Troubleshooting.

`requirements.txt` pins: `torch==2.1.2`, `torchvision==0.16.2`, `transformers==4.42.4`,
`peft==0.11.1`, `accelerate==0.32.1`, `bitsandbytes==0.43.1`, `timm==0.9.10`.

**AirSim client patch (one‑time, required to avoid 4–5 s/step latency):**
In `<env>/lib/python3.10/site-packages/airsim/client.py`, in `VehicleClient.__init__`,
change
```python
self.client = msgpackrpc.Client(msgpackrpc.Address(ip, port), timeout = timeout_value, pack_encoding = 'utf-8', unpack_encoding = 'utf-8')
```
to
```python
self.client = msgpackrpc.Client(msgpackrpc.Address(ip, port), timeout = timeout_value)
```

---

## 5. Wrapper: choose precision for H100 (recommended: back to bf16)

The local wrapper was changed to 4‑bit load to fit the 6 GB RTX 3050. On the H100 you
get better results by reverting to plain **bf16**. In
`src/model_wrapper/aerovla_wrapper_ui.py`, replace the loading block so it reads:

```python
        self.model = AutoModelForVision2Seq.from_pretrained(
            base_model_path,
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True,
            trust_remote_code=True
        )
        self.model.resize_token_embeddings(len(self.tokenizer))
        self.model = PeftModel.from_pretrained(self.model, adapter_path)
        # self.model = self.model.merge_and_unload()
        self.model.to(self.device)
        self.model.eval()
```

and the pixel‑values cast:
```python
        if hasattr(self.model, "dtype"):
            pixel_values = pixel_values.to(self.model.dtype)
```

(Keeps `BitsAndBytesConfig` import harmless/removable.) If you keep 4‑bit on a smaller
GPU, leave the current wrapper and see §6.

---

## 6. (Alternate) 4‑bit path for GPU ≤ 12 GB

The current local wrapper already implements this (4‑bit NF4 base + bf16 projector
rebuild). It needs the CUDA‑11 libs + `LD_LIBRARY_PATH` (see §7). Note it empirically
**fails on 6 GB** — use ≥ 12 GB, ideally ≥ 16 GB.

```bash
export LD_LIBRARY_PATH=$CONDA_PREFIX/lib/python3.10/site-packages/nvidia/cusparse/lib:$CONDA_PREFIX/lib/python3.10/site-packages/nvidia/cublas/lib:$LD_LIBRARY_PATH
```

---

## 7. Start the simulator server (do this before the eval)

Run from the `airsim_plugin/` directory (the server resolves the env via
`../envs/...` relative to `airsim_plugin/`; `root_path` default is `../envs`).

```bash
cd /path/to/AeroVLA/airsim_plugin

# GPU_ID=0 → --port 30000 (must match the eval's --simulator_tool_port)
python AirVLNSimulatorServerTool.py --gpus 0 --port 30000
```

**Headless display**: if the VM has no X display (typical SSH), wrap with a virtual
display:

```bash
sudo apt-get install -y xvfb   # once
cd /path/to/AeroVLA/airsim_plugin
xvfb-run -a -s "-screen 0 1280x720x24" \
  python AirVLNSimulatorServerTool.py --gpus 0 --port 30000
```

You should see:
```
PROJECT_ROOT_DIR /path/to/AeroVLA
start listening 127.0.0.1:30000
```
(The UE4 window will open on the virtual display once the client requests actions.)

---

## 8. Run the closed‑loop evaluation

In a **second** terminal (keep the server running), from the project root:

```bash
cd /path/to/AeroVLA
bash scripts/eval_aerovla.sh
```

It runs (already configured):
```bash
CUDA_VISIBLE_DEVICES=0 python -u src/vlnce_src/eval_aerovla.py \
    --run_type eval --name AerialVLA_Eval --gpu_id 0 \
    --simulator_tool_port 30000 --DDP_MASTER_PORT 80005 --batchSize 1 --maxWaypoints 200 \
    --dataset_path ./dataset_raw/ \
    --eval_save_path ./eval_results/checkpoints/seen_valset/BrushifyCountryRoads \
    --model_path ./checkpoints \
    --eval_json_path ./data/uav_dataset/seen_valset_splits/BrushifyCountryRoads.json \
    --map_spawn_area_json_path ./data/meta/map_spawnarea_info.json \
    --object_name_json_path ./data/meta/object_description.json
```

- 123 episodes run sequentially (each `merged_data.json` = one episode).
- Results/per‑episode JSONs are written to `eval_results/checkpoints/seen_valset/BrushifyCountryRoads/`.

---

## 9. Verify / aggregate results (on the VM)

```bash
cd /path/to/AeroVLA
bash scripts/metric.sh
```

This aggregates SR, OSR, NE, SPL into:
- `eval_results/checkpoints/evaluation_detailed.csv`
- `eval_results/checkpoints/evaluation_summary_aggregated.csv`

> `metric.sh` iterates category dirs under `eval_results/checkpoints/`; it will pick up
> `seen_valset/BrushifyCountryRoads`.

---

## 10. Pull results back to the local machine

From the **local** machine:

```bash
rsync -avz --progress \
  user@VM_HOST:/path/to/AeroVLA/eval_results/ \
  /home/sanati/projs/sensreson/AeroVLA/eval_results/
```

Or, if you want a single packed bundle:

```bash
# on VM:
cd /path/to/AeroVLA && tar czf /tmp/aerovla_results.tgz eval_results/
# on local:
scp user@VM_HOST:/tmp/aerovla_results.tgz /tmp/
tar xzf /tmp/aerovla_results.tgz -C /home/sanati/projs/sensreson/AeroVLA/
```

---

## 11. Troubleshooting

**bitsandbytes can't load native library / `libcusparse.so.11 not found`**
torch `cu118` needs CUDA 11 libs not installed by default. Install and export:
```bash
pip install nvidia-cusparse-cu11==11.7.4.91 nvidia-cublas-cu11==11.11.3.6
export LD_LIBRARY_PATH=$CONDA_PREFIX/lib/python3.10/site-packages/nvidia/cusparse/lib:$CONDA_PREFIX/lib/python3.10/site-packages/nvidia/cublas/lib:$LD_LIBRARY_PATH
```

**`ValueError: .to is not supported for 4-bit/8-bit` at load**
Wrong accelerate version. Use `accelerate==0.32.1` (from requirements.txt).

**`.to`-style / dispatch errors on H100 bf16**
Ensure you reverted the wrapper to bf16 (§5) and are not passing a
`quantization_config`.

**CUDA out of memory**
- 80 GB H100 + bf16 = OK . If OOM on smaller GPUs, use the 4‑bit path (§6) and check VRAM is ≥ 12 GB (6 GB empirically **fails**).

**`FileNotFoundError: .../merged_data.json` / missing `mark.json`**
Confirm `dataset_raw/BrushifyCountryRoads/<uuid>/` has both files (symlink into raw).

**`KeyError: 'BrushifyCountryRoads'` in span area / server**
Confirm §1 items 2, 4 are present in the transferred copy.

**Server "start listening" never prints / client can't connect**
Use `xvfb-run` (§7); ensure both processes use the same port (30000).

**Very slow `simGetImages` (4–5 s/step)**
You skipped the airsim `client.py` utf‑8 patch (§4). Apply it and restart.

---

## 12. Quick sanity checklist before transferring

```bash
# on local, from project root:
python - <<'PY'
import json, os
d = json.load(open("data/uav_dataset/seen_valset_splits/BrushifyCountryRoads.json"))
assert len(d) == 123, "split episodes != 123"
for it in d:
    p = os.path.join("dataset_raw/", it["json"])
    assert os.path.isfile(p), p
    assert os.path.isfile(p.replace("merged_data.json","mark.json"))
info = json.load(open("data/meta/map_spawnarea_info.json"))
assert "BrushifyCountryRoads" in info, "spawn area missing"
print("OK: 123 episodes + spawn areas present")
PY
```
