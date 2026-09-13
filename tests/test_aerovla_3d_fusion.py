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


if __name__ == "__main__":
    print("cem ..."); test_cem_shapes()
    print("lidar ..."); test_lidar_encoder()
    print("cross_attn ..."); test_cross_attention()
    print("full ..."); test_full_model()
    print("\nALL SMOKE TESTS PASSED")
