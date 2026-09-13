"""
train_step_a.py -- Option A: end-to-end Behaviour-Cloning (BC) NLL training for
the 3D LiDAR-visual fusion model (`AerialVLAModel`), matching the AeroVLA BC
training conventions.

What is trained (all simultaneously):
  * `LiDAREncoder`        (setup: pointpillars/voxelnet -> BEV tokens)
  * `CoordinatesEncodingModule` (CEM: Gamma_im / Gamma_pc position encodings)
  * `SoftLiDARVisualCrossAttention` (Q = Z_vis + Gamma_im, K/V = Z_lidar + Gamma_pc)
  * the fine-tuned image projector (via PEFT `modules_to_save=["projector"]`)
  * LLaMA-2 LoRA adapters (the standard `q/k/v/o+gate/up/down` set)

It uses the exact same data/action/label conventions as `src/train_aerovla.py`:
  * text            = f"{instruction}\\nAction: BB BB BB[ LAND]<eos>"
  * prompt_only     = f"{instruction}\\nAction: "            (masked in labels)
  * labels          = input_ids with prompt tokens -> -100 and padding -> -100
  * loss            = cross-entropy over the *action* tokens only (BC NLL)

Unlike `train_aerovla.py` this script drives `forward_with_3d_fusion` directly
(not the HF Trainer's `model(**batch)`), because the fused path needs the
sensor tensors (lidar, intrinsics, CEM inputs).  We keep the raw
`prismatic.base_model.model` handle for the fused forward so gradients flow
into the in-place-injected LoRA adapters + fusion modules + projector.

Usage:
    python src/train_step_a.py \
        --model_path ./openvla-7b \
        --data_root ./dataset_raw \
        --split_json ./data/aerovla_train_dataset.json \
        --output_dir ./checkpoints/aero_vla_step_a \
        [--micro_batch 1] [--grad_accum 8] [--lr 2e-4]
        [--epochs 5] [--lora_r 64] [--max_steps 100000] [--no_lidar] ...

Env vars honoured: LOCAL_RANK, WORLD_SIZE (torchrun / DDP).

The model is loaded with fusion DISABLED by default and only enabled via
`config.enable_3d_fusion=True` passed at load time (this is what constructs the
AerialVLAModel so its params exist in the state dict).
"""

import argparse
import os
import random
import sys
import time

# Ensure the repo root is importable (run as `python src/train_step_a.py` from
# the repo root; sys.path[0] would otherwise be `src/`).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("NCCL_P2P_DISABLE", "1")
os.environ.setdefault("NCCL_IB_DISABLE", "1")
import numpy as np
import torch
import torch.nn.functional as F
import transformers
from peft import LoraConfig, get_peft_model
from torch.utils.data import DataLoader
from transformers import AutoModelForVision2Seq, AutoProcessor, AutoTokenizer

from aerovla_dataset import AeroVLADataset  # noqa: F401  (reuse label/action helpers)
from datasets.uav_lidar_dataset import UAVLiDARCollator, UAVLiDARDataset

# --------------------------------------------------------------------------
IGNORE_INDEX = -100


def parse_args():
    p = argparse.ArgumentParser(description="Option A: end-to-end BC NLL fusion training")
    p.add_argument("--model_path", default="./openvla-7b")
    p.add_argument("--data_root", default="./dataset_raw")
    p.add_argument("--split_json", default="./data/aerovla_train_dataset.json")
    p.add_argument("--output_dir", default="./checkpoints/aero_vla_step_a")
    p.add_argument("--micro_batch", type=int, default=2)
    p.add_argument("--grad_accum", type=int, default=8)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--weight_decay", type=float, default=0.03)
    p.add_argument("--warmup_ratio", type=float, default=0.03)
    p.add_argument("--max_grad_norm", type=float, default=1.0)
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--lora_r", type=int, default=64)
    p.add_argument("--lora_alpha", type=int, default=128)
    p.add_argument("--lora_dropout", type=float, default=0.05)
    p.add_argument("--max_steps", type=int, default=100000)
    p.add_argument("--logging_steps", type=int, default=50)
    p.add_argument("--save_steps", type=int, default=2000)
    p.add_argument("--save_total_limit", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no_lidar", action="store_true",
                   help="run the standard forward (no fusion) as a smoke/baseline")
    p.add_argument("--lidar_backbone", default="pointpillars")
    p.add_argument("--lidar_grid_res", type=int, nargs=2, default=(64, 64))
    p.add_argument("--cem_num_depth_samples", type=int, default=32)
    p.add_argument("--use_smca_mask", action="store_true")
    p.add_argument("--coor_grad", action="store_true",
                   help="if set, also unfreeze the (already-fresh) fusion params; "
                        "otherwise they are trained by default anyway")
    return p.parse_args()


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def main():
    args = parse_args()
    set_seed(args.seed)

    local_rank = int(os.environ.get("LOCAL_RANK") or 0)
    world_size = int(os.environ.get("WORLD_SIZE") or 1)
    device = f"cuda:{local_rank}" if torch.cuda.is_available() else "cpu"
    torch_dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

    print(f"[stepA] device={device} world={world_size} dtype={torch_dtype} "
          f"lidar={not args.no_lidar}")

    # ---- tokenizer / processor ----
    tokenizer = AutoTokenizer.from_pretrained(args.model_path, trust_remote_code=True)
    processor = AutoProcessor.from_pretrained(args.model_path, trust_remote_code=True)
    if not tokenizer.pad_token:
        tokenizer.pad_token = tokenizer.eos_token
    if not tokenizer.eos_token:
        tokenizer.eos_token = "</s>"

    # ---- model (fusion enabled at load) ----
    print("[stepA] loading OpenVLA with enable_3d_fusion=True ...")
    # `enable_3d_fusion` is read from the *config* (`getattr(config, ...)` in
    # modeling_prismatic.py), so it must be set on the config object BEFORE
    # instantiation -- passing it as a from_pretrained kwarg would try to feed it
    # to the model __init__ (TypeError).
    from transformers import AutoConfig
    _cfg = AutoConfig.from_pretrained(args.model_path, trust_remote_code=True)
    setattr(_cfg, "enable_3d_fusion", not args.no_lidar)
    model = AutoModelForVision2Seq.from_pretrained(
        args.model_path,
        config=_cfg,
        torch_dtype=torch_dtype,
        trust_remote_code=True,
        low_cpu_mem_usage=True,
        # device_map dispatches params straight to GPU and materializes the NEW
        # random fusion_module params on real devices; a later `.to(device)`
        # fails with "Cannot copy out of meta tensor" for those extra params.
        device_map={"": local_rank} if torch.cuda.is_available() else None,
    )
    model.resize_token_embeddings(len(tokenizer))

    # ---- LoRA wrap (in-place LM injection) ----
    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        modules_to_save=["projector"],
    )
    model = get_peft_model(model, lora_config)
    peft_raw = model.base_model.model          # original PrismaticForConditionalGeneration

    # The fresh fusion stack (cem/lidar_encoder/fusion) is discarded by HF's
    # loader (`low_cpu_mem_usage` + `device_map` move to meta, then only
    # checkpoint keys materialize) and comes back uninitialized -> NaN/garbage
    # params. Restore a deterministic init immediately after load.
    if not args.no_lidar:
        peft_raw.fusion_module.reset_fusion_parameters()

    # Gradient checkpointing (matches `src/train_aerovla.py`); enables 7B+fusion
    # on a single H100 MIG.  Runs below the PEFT wrapper so it applies to the
    # actual language_model/lm_head.
    if torch.cuda.is_available():
        try:
            peft_raw.gradient_checkpointing_enable()
        except Exception as e:  # noqa: BLE001
            print(f"[stepA] gradient checkpointing skipped: {e}")

    # Explicitly unfreeze the fusion modules (cem/lidar_encoder/fusion cross-attn).
    # These live on the ORIGINAL (peft_raw.fusion_module) and are what
    # `forward_with_3d_fusion` actually calls.  LoRA adapters are already
    # trainable via PEFT; the projector is trainable via `modules_to_save`
    # (PEFT's ModulesToSaveWrapper routes forward() to the trainable copy).
    if not args.no_lidar:
        n_unfrozen = 0
        for n, p in peft_raw.fusion_module.named_parameters():
            p.requires_grad = True
            n_unfrozen += int(p.requires_grad)
        print(f"[stepA] unfroze {n_unfrozen} fusion params "
              f"({type(peft_raw.fusion_module).__name__})")

    # Sanity: count trainable params
    tr = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"[stepA] trainable={tr:,} / total={total:,} ({100*tr/total:.2f}%)")

    # ---- dataset + collator ----
    import json as _json
    with open(args.split_json) as f:
        samples = _json.load(f)
    if isinstance(samples, dict):
        samples = samples.get("samples", samples.get("data", []))
    dataset = UAVLiDARDataset(
        samples=samples,
        data_root=args.data_root,
        num_depth_samples=args.cem_num_depth_samples,
        grid_res=tuple(args.lidar_grid_res),
        lidar_sensor_key="lidar",
        require_lidar=False,
        tokenizer=tokenizer,
        device=device,
        dtype=torch_dtype,
        training=True,
    )
    collator = UAVLiDARCollator(
        processor=processor,
        tokenizer=tokenizer,
        device=device,
        dtype=torch_dtype,
        mask_prompt=True,
    )
    loader = DataLoader(
        dataset,
        batch_size=args.micro_batch,
        collate_fn=collator,
        shuffle=True,
        num_workers=0,
        drop_last=True,
    )
    print(f"[stepA] dataset size={len(dataset)}")

    # ---- optimizer / scheduler ----
    steps_per_epoch = max(1, len(loader) // args.grad_accum)
    total_steps = min(args.max_steps, args.epochs * steps_per_epoch)
    warmup_steps = int(args.warmup_ratio * total_steps)

    no_decay = ["bias", "LayerNorm.weight", "layernorm.weight"]
    opt_params = [
        {"params": [p for n, p in model.named_parameters()
                    if p.requires_grad and not any(k in n for k in no_decay)],
         "weight_decay": args.weight_decay},
        {"params": [p for n, p in model.named_parameters()
                    if p.requires_grad and any(k in n for k in no_decay)],
         "weight_decay": 0.0},
    ]
    optimizer = torch.optim.AdamW(opt_params, lr=args.lr)
    scheduler = transformers.get_cosine_schedule_with_warmup(
        optimizer, num_warmup_steps=warmup_steps, num_training_steps=total_steps
    )

    if world_size > 1:
        from torch.nn.parallel import DistributedDataParallel as DDP
        model = DDP(model, device_ids=[local_rank], find_unused_parameters=False)

    # ---- training loop ----
    grad_norm = args.max_grad_norm
    global_step = 0
    running_loss = 0.0
    t0 = time.time()
    model.train()
    print("[stepA] beginning training ...")

    for epoch in range(args.epochs):
        for step, batch in enumerate(loader):
            if global_step >= total_steps:
                break

            # forward (fused or fallback)
            if args.no_lidar:
                out = model(
                    input_ids=batch["input_ids"],
                    attention_mask=batch["attention_mask"],
                    pixel_values=batch["composite_image"],
                    labels=batch["labels"],
                )
            else:
                b = {k: v for k, v in batch.items() if k not in
                     ("rel_dir", "frame_idx", "composite_image")}
                out = peft_raw.forward_with_3d_fusion(
                    input_ids=b["input_ids"],
                    attention_mask=b["attention_mask"],
                    pixel_values=batch["composite_image"],
                    labels=b["labels"],
                    lidar_points=b["lidar_points"],
                    lidar_valid=b["lidar_valid"],
                    camera_intrinsics=b["camera_intrinsics"],
                    camera_extrinsics=b["camera_extrinsics"],
                    normalised_pixel_coords=b["normalised_pixel_coords"],
                    depth_samples=b["depth_samples"],
                    pillar_coords=b["pillar_coords"],
                    pillar_dims=b["pillar_dims"],
                    pillar_heights=b["pillar_heights"],
                    q_pix=b["q_pix"],
                    k_pix=b["k_pix"],
                )

            loss = out.loss if out.loss is not None else F.cross_entropy(
                out.logits[:, :-1].reshape(-1, out.logits.size(-1)),
                batch["labels"][:, 1:].reshape(-1) if batch["labels"] is not None else None,
                ignore_index=IGNORE_INDEX,
            )
            loss = loss / args.grad_accum
            loss.backward()
            running_loss += loss.item() * args.grad_accum

            if (global_step + 1) % args.grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), grad_norm)
                optimizer.step()
                optimizer.zero_grad()
                scheduler.step()

            if (global_step + 1) % args.logging_steps == 0:
                print(f"[stepA] epoch={epoch} step={global_step+1}/{total_steps} "
                      f"loss={running_loss/args.logging_steps:.4f} "
                      f"lr={scheduler.get_last_lr()[0]:.2e} "
                      f"elapsed={time.time()-t0:.1f}s")
                running_loss = 0.0

            if (global_step + 1) % args.save_steps == 0:
                ckpt = os.path.join(args.output_dir, f"step_{global_step+1}")
                model.save_pretrained(ckpt)
                tokenizer.save_pretrained(ckpt)
                print(f"[stepA] saved checkpoint -> {ckpt}")

            global_step += 1

    # final save
    model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"[stepA] done. final checkpoint -> {args.output_dir}")


if __name__ == "__main__":
    main()
