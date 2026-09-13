"""Smoke test for the AerialVLA LiDAR-visual fusion modules.

Verifies import + forward-shape invariants for the CEM, LiDAR encoder,
cross-attention fusion, and the full AerialVLAModel wrapper (using a tiny
fake base model so we don't need to load the 14 GB OpenVLA checkpoint).
"""

import os
import sys

import torch
import torch.nn as nn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.encoders.cem import CoordinatesEncodingModule
from models.encoders.lidar_encoder import LiDAREncoder
from models.fusion.cross_attention_fusion import SoftLiDARVisualCrossAttention
from models.aerial_vla_model import AerialVLAModel


def test_cem_shapes():
    torch.manual_seed(0)
    B, N_vis, N_lidar, D, d_hidden = 2, 256, 64, 16, 64
    cem = CoordinatesEncodingModule(d_hidden=d_hidden, num_depth_samples=D)

    px = torch.randn(B, N_vis, 2)
    kinv = torch.eye(3).unsqueeze(0).expand(B, 3, 3)
    depths = torch.rand(B, N_vis, D) + 1.0
    T = torch.eye(4).unsqueeze(0).expand(B, 4, 4)

    pe_im = cem.image_pe(px, kinv, depths, T)
    assert pe_im.shape == (B, N_vis, d_hidden), pe_im.shape

    coords = torch.rand(B, N_lidar, 2)
    pdims = torch.rand(B, N_lidar, 2) + 0.1
    heights = torch.rand(B, N_lidar)
    pe_pc = cem.pc_pe(coords, pdims, heights)
    assert pe_pc.shape == (B, N_lidar, d_hidden), pe_pc.shape
    print("  CEM OK", pe_im.shape, pe_pc.shape)


def test_cem_image_pe_depth_count_mismatch():
    """image_pe must derive D from the input `depths`, not the CEM's own
    num_depth_samples. Regression for the H100 smoke crash:
    `RuntimeError: The size of tensor a (32) must match the size of tensor b
    (8)` when the model was built with 8 depth samples but the collator emits
    32 (models/encoders/cem.py:130)."""
    torch.manual_seed(0)
    B, N_vis, D_input, d_hidden = 2, 64, 32, 64
    cem = CoordinatesEncodingModule(d_hidden=d_hidden, num_depth_samples=8)  # deliberately different
    px = torch.randn(B, N_vis, 2)
    kinv = torch.eye(3).unsqueeze(0).expand(B, 3, 3)
    depths = torch.rand(B, N_vis, D_input) + 1.0
    T = torch.eye(4).unsqueeze(0).expand(B, 4, 4)
    pe_im = cem.image_pe(px, kinv, depths, T)
    assert pe_im.shape == (B, N_vis, d_hidden), pe_im.shape
    print("  CEM depth-count-mismatch OK", pe_im.shape)


def test_lidar_encoder():
    torch.manual_seed(0)
    B, N_max, C = 2, 200, 4
    d_vis = 64
    for backbone in ("pointpillars", "voxelnet"):
        enc = LiDAREncoder(d_vis=d_vis, backbone=backbone, grid_res=(32, 32))
        pts = torch.randn(B, N_max, C) * 10
        valid = torch.ones(B, N_max, dtype=torch.bool)
        valid[:, 150:] = False
        toks = enc(pts, valid)
        assert toks.ndim == 3 and toks.shape[-1] == d_vis, (backbone, toks.shape)
        print(f"  LiDAR[{backbone}] OK", toks.shape)


def test_cross_attention():
    torch.manual_seed(0)
    B, N_vis, N_lidar, d = 2, 256, 64, 64
    fusion = SoftLiDARVisualCrossAttention(d_model=d, n_heads=4, use_smca_mask=True)

    z_vis = torch.randn(B, N_vis, d)
    z_lidar = torch.randn(B, N_lidar, d)
    pe_im = torch.randn(B, N_vis, d) * 0.1
    pe_pc = torch.randn(B, N_lidar, d) * 0.1
    q_pix = torch.rand(B, N_vis, 2)
    k_pix = torch.rand(B, N_lidar, 2)

    out = fusion(z_vis, z_lidar, pe_im, pe_pc, q_pix, k_pix)
    assert out.shape == (B, N_vis, d), out.shape
    print("  CrossAttn OK", out.shape)


class _FakeBase(nn.Module):
    """Mimic the Prismatic model surface we integrate against."""

    def __init__(self, d_vis):
        super().__init__()
        self.d_vis = d_vis
        self.vision_backbone = _FakeVision(d_vis)
        self.projector = nn.Linear(d_vis, 64)
        self.language_model = _FakeLM(64, 8)

    def get_input_embeddings(self):
        return self.language_model.emb

    def __call__(self, **kw):
        import torch.nn.functional as F
        emb = self.language_model.emb(kw["input_ids"])
        logits = F.linear(emb, self.language_model.emb.weight)
        logits = torch.cat([logits, logits], dim=1)
        return type("O", (), {"logits": logits, "loss": None})()


class _FakeVision(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.embed_dim = d

    def forward(self, x):
        B, C, H, W = x.shape
        return torch.randn(B, H * W // 49, self.embed_dim)


class _FakeLM(nn.Module):
    def __init__(self, d, vocab):
        super().__init__()
        self.emb = nn.Embedding(vocab, d)

    def forward(self, input_ids=None, inputs_embeds=None, **kw):
        import torch.nn.functional as F
        emb = self.emb(input_ids) if input_ids is not None else inputs_embeds
        logits = F.linear(emb, self.emb.weight)
        return type("O", (), {"logits": logits, "loss": None})()

    def get_input_embeddings(self):
        return self.emb


def test_full_model():
    torch.manual_seed(0)
    d_vis, B = 64, 2
    vocab = 8
    D = 16
    base = _FakeBase(d_vis)
    model = AerialVLAModel(base, d_vis=d_vis, lidar_backbone="pointpillars",
                           lidar_grid_res=(32, 32), use_smca_mask=True,
                           cem_num_depth_samples=D)

    # The fused vision backbone emits H*W/49 tokens: for 224x224 that is 1024.
    N_vis = 224 * 224 // 49
    # LiDAR grid tokens: 32x32 = 1024.
    N_lidar = 32 * 32

    # Fused path inputs.
    composite = torch.randn(B, 6, 224, 224)
    lidar = torch.randn(B, 200, 4) * 10
    lidar_valid = torch.ones(B, 200, dtype=torch.bool)
    lidar_valid[:, 150:] = False
    kinv = torch.eye(3).unsqueeze(0).expand(B, 3, 3)
    T = torch.eye(4).unsqueeze(0).expand(B, 4, 4)
    px = torch.rand(B, N_vis, 2)
    depths = torch.rand(B, N_vis, D) + 1
    pc = torch.rand(B, N_lidar, 2)
    pd = torch.rand(B, N_lidar, 2) + 0.1
    ph = torch.rand(B, N_lidar)
    q_pix = torch.rand(B, N_vis, 2)
    k_pix = torch.rand(B, N_lidar, 2)

    ids = torch.randint(0, vocab, (B, 10))
    mask = torch.ones_like(ids)

    out = model(
        composite_image=composite,
        input_ids=ids,
        attention_mask=mask,
        lidar_points=lidar,
        lidar_valid=lidar_valid,
        camera_intrinsics=kinv,
        camera_extrinsics=T,
        normalised_pixel_coords=px,
        depth_samples=depths,
        pillar_coords=pc,
        pillar_dims=pd,
        pillar_heights=ph,
        q_pix=q_pix,
        k_pix=k_pix,
    )
    assert out.logits.shape == (B, 10 + N_vis, vocab), out.logits.shape
    print("  FullModel fused OK", out.logits.shape)

    # Fallback path (no lidar) must delegate cleanly.
    out2 = model(composite_image=composite, input_ids=ids, attention_mask=mask)
    print("  FullModel fallback OK")


def test_fusion_dtype_follows_base_model():
    """The additive fusion modules (cem/lidar_encoder/fusion) must inherit the
    base model's dtype. Regression for the H100 `loss=nan` caused by fresh
    fp32 fusion params meeting bf16 inputs (`mat1 and mat2 must have the same
    dtype, got BFloat16 and Float` / silent NaN on GPU). device_map only casts
    checkpoint-loaded params, not newly-initialized submodules."""
    torch.manual_seed(0)
    d_vis = 64
    base = _FakeBase(d_vis).to(torch.bfloat16)  # emulate torch_dtype=bf16 load
    model = AerialVLAModel(base, d_vis=d_vis, lidar_backbone="pointpillars",
                           lidar_grid_res=(16, 16), cem_num_depth_samples=8)
    fm = model
    for name, p in fm.named_parameters():
        assert p.dtype == torch.bfloat16, f"{name} dtype={p.dtype}"
    assert next(fm.lidar_encoder.parameters()).dtype == torch.bfloat16
    assert next(fm.cem.parameters()).dtype == torch.bfloat16
    assert next(fm.fusion.parameters()).dtype == torch.bfloat16
    print("  fusion dtype follows base model (bf16) OK")


def _make_cycle_model():
    """Replicate the real integration: parent owns fusion_module, which stores base_model.

    This mirrors `openvla-7b/modeling_prismatic.py`: `self.fusion_module =
    AerialVLAModel(base_model=self, ...)`. The `base_model` back-reference must
    stay OUT of the nn.Module child registry, otherwise `.state_dict()` /
    `.parameters()` recurse infinitely.
    """
    d_vis = 64
    base = _FakeBase(d_vis)
    model = AerialVLAModel(base, d_vis=d_vis, lidar_backbone="pointpillars",
                           lidar_grid_res=(16, 16), cem_num_depth_samples=8)
    base.fusion_module = model
    return base


def test_state_dict_no_infinite_recursion():
    base = _make_cycle_model()
    sd = base.state_dict()
    assert len(sd) > 0
    assert list(base.parameters())  # parameters() must terminate too
    print("  state_dict/parameters OK (no recursion),", len(sd), "tensors")


def test_fusion_module_not_in_children_of_itself():
    base = _make_cycle_model()
    fm = base.fusion_module
    # base_model must NOT be a registered child of fusion_module.
    assert "base_model" not in dict(fm.named_children())
    # But fusion_module IS a child of base (forward path still works).
    assert "fusion_module" in dict(base.named_children())


def test_reset_fusion_parameters_recovers_nan():
    """After HF's `low_cpu_mem_usage`+`device_map` loader discards the fusion
    module's init (params come back as uninitialized memory -> NaN / garbage,
    e.g. BN weight ~8e35, LayerNorm weight NaN), `reset_fusion_parameters()`
    must deterministically restore valid values (BN weight=1, LN weight=1, no
    NaN). Regression for the H100 `loss=nan` smoke that actually NaNs in the
    LiDAR encoder."""
    torch.manual_seed(0)
    d_vis = 64
    base = _FakeBase(d_vis)
    model = AerialVLAModel(base, d_vis=d_vis, lidar_backbone="pointpillars",
                           lidar_grid_res=(16, 16), cem_num_depth_samples=8)

    # Corrupt everything with NaN to emulate the uninitialized-materialization.
    for mod in (model.cem, model.lidar_encoder, model.fusion):
        for p in mod.parameters():
            if p.is_floating_point():
                p.data.fill_(float("nan"))
        for b in mod.buffers():
            if b.is_floating_point():
                b.data.fill_(float("nan"))

    assert any(
        torch.isnan(p).any().item()
        for mod in (model.cem, model.lidar_encoder, model.fusion)
        for p in mod.parameters() if p.is_floating_point()
    )

    model.reset_fusion_parameters()

    for mod in (model.cem, model.lidar_encoder, model.fusion):
        for p in mod.parameters():
            if p.is_floating_point():
                assert not torch.isnan(p).any().item(), p
                assert not torch.isinf(p).any().item(), p
        for b in mod.buffers():
            if b.is_floating_point():
                assert not torch.isnan(b).any().item(), b

    conv = model.lidar_encoder.backbone.conv_stack
    assert torch.allclose(conv[1].weight, torch.ones_like(conv[1].weight))
    assert torch.allclose(model.fusion.norm.weight, torch.ones_like(model.fusion.norm.weight))
    print("  reset_fusion_parameters recovers NaN params OK")


def test_reset_fusion_parameters_bf16():
    """Reset must preserve the base model dtype (bf16) — the cast in __init__
    plus a follow-up reset must not produce fp32 params."""
    torch.manual_seed(0)
    d_vis = 64
    base = _FakeBase(d_vis).to(torch.bfloat16)
    model = AerialVLAModel(base, d_vis=d_vis, lidar_backbone="pointpillars",
                           lidar_grid_res=(16, 16), cem_num_depth_samples=8)
    model.reset_fusion_parameters()
    for mod in (model.cem, model.lidar_encoder, model.fusion):
        for p in mod.parameters():
            assert p.dtype == torch.bfloat16, p.dtype
    print("  reset_fusion_parameters preserves bf16 OK")


if __name__ == "__main__":
    print("cem ..."); test_cem_shapes()
    print("lidar ..."); test_lidar_encoder()
    print("cross_attn ..."); test_cross_attention()
    print("full ..."); test_full_model()
    print("reset ..."); test_reset_fusion_parameters_recovers_nan()
    print("reset bf16 ..."); test_reset_fusion_parameters_bf16()
    print("\nALL SMOKE TESTS PASSED")
