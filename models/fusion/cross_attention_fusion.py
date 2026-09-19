"""
SoftLiDARVisualCrossAttention -- multi-head cross-attention fusion between
visual and LiDAR tokens with optional SMCA-style 2D distance masks.

Key contract from the spec:
    Q = Z_vis + Gamma_im
    K / V = Z_lidar + Gamma_pc

Multi-head soft cross-attention performs a *soft association* between every
visual token and every LiDAR token (no hard point-to-pixel projection).  A
"2D distance mask" (SMCA-style, ``dist_mask``) optionally downweights attention
by pixel distance between the projected LiDAR token and the visual token, so
pixels near the LiDAR's camera projection attend more strongly.

Residual connection + LayerNorm wrap the cross-attention output.
"""

import math

import torch
import torch.nn as nn
import torch.nn.functional as F


class SoftLiDARVisualCrossAttention(nn.Module):
    """Multi-head cross-attention (soft association) LiDAR -> visual fusion."""

    def __init__(
        self,
        d_model: int,
        n_heads: int = 8,
        dropout: float = 0.1,
        use_smca_mask: bool = False,
        smca_sigma: float = 10.0,
        capture_attn: bool = False,
    ) -> None:
        """Args:
            d_model: token width (D_vis); must equal CEM output + projector input.
            n_heads: number of attention heads.
            dropout: attention/output dropout.
            use_smca_mask: toggle the 2D distance mask (SMCA-style).
            smca_sigma: Gaussian sigma controlling spatial locality of the mask.
            capture_attn: if True, record attention statistics (mass on lidar
                keys, per-head focus) into self.attn_stats each forward for
                lidar-usage analysis (off by default; no semantic change).
        """
        super().__init__()
        assert d_model % n_heads == 0, "d_model must be divisible by n_heads"

        self.d_model = d_model
        self.n_heads = n_heads
        self.head_dim = d_model // n_heads
        self.use_smca_mask = use_smca_mask
        self.smca_sigma = smca_sigma
        self.scale = math.sqrt(self.head_dim)
        self.capture_attn = capture_attn
        self.attn_stats = None

        self.q_proj = nn.Linear(d_model, d_model)
        self.k_proj = nn.Linear(d_model, d_model)
        self.v_proj = nn.Linear(d_model, d_model)
        self.out_proj = nn.Linear(d_model, d_model)
        self.attn_drop = nn.Dropout(dropout)
        self.resid_drop = nn.Dropout(dropout)

        # Residual + layernorm.
        self.norm = nn.LayerNorm(d_model)

        self._reset_parameters()

    def _reset_parameters(self) -> None:
        for proj in (self.q_proj, self.k_proj, self.v_proj, self.out_proj):
            nn.init.xavier_uniform_(proj.weight)
            if proj.bias is not None:
                nn.init.zeros_(proj.bias)

    def _smca_2d_mask(
        self,
        attn_logits: torch.Tensor,
        q_pix: torch.Tensor,
        k_pix: torch.Tensor,
    ) -> torch.Tensor:
        """Gaussian-smoothed 2D pixel-distance mask (SMCA-style).

        Args:
            attn_logits: [B, n_heads, N_q, N_k] raw logits.
            q_pix: [B, N_q, 2] (u,v) pixel coords of each query (visual) token.
            k_pix: [B, N_k, 2] (u,v) pixel coords of each key (LiDAR) token.

        Retuns:
            mask_logits [B, n_heads, N_q, N_k] (added to attn_logits).
        """
        # [B, N_q, N_k] euclidean pixel distance
        d = torch.cdist(q_pix, k_pix)  # [B, N_q, N_k]
        d = d.unsqueeze(1)             # [B, 1, N_q, N_k]
        m = -0.5 * (d / self.smca_sigma) ** 2
        return m.expand(-1, self.n_heads, -1, -1)

    def forward(
        self,
        vis_tokens: torch.Tensor,
        lidar_tokens: torch.Tensor,
        pe_vis: torch.Tensor,
        pe_lidar: torch.Tensor,
        q_pix: torch.Tensor = None,
        k_pix: torch.Tensor = None,
        key_padding_mask: torch.Tensor = None,
    ) -> torch.Tensor:
        """Args:
            vis_tokens:   [B, N_vis, d_model] Z_vis (visual tokens).
            lidar_tokens: [B, N_lidar, d_model] Z_lidar (LiDAR tokens).
            pe_vis:       [B, N_vis, d_model] Gamma_im (image PE).
            pe_lidar:     [B, N_lidar, d_model] Gamma_pc (point-cloud PE).
            q_pix:        [B, N_vis, 2] optional pixel coords for SMCA mask.
            k_pix:        [B, N_lidar, 2] optional pixel coords for SMCA mask.
            key_padding_mask: [B, N_lidar] bool (True = ignore / pad) for keys.

        Returns:
            fused [B, N_vis, d_model] (attention output + residual + LayerNorm).
        """
        B, N_vis, _ = vis_tokens.shape
        _, N_k, _ = lidar_tokens.shape

        # Spec: Q = Z_vis + Gamma_im ; K = V = Z_lidar + Gamma_pc.
        Q = vis_tokens + pe_vis
        KV = lidar_tokens + pe_lidar

        q = self.q_proj(Q)
        k = self.k_proj(KV)
        v = self.v_proj(KV)

        # Split heads: [B, n_heads, N, head_dim]
        q = q.view(B, N_vis, self.n_heads, self.head_dim).transpose(1, 2)
        k = k.view(B, N_k, self.n_heads, self.head_dim).transpose(1, 2)
        v = v.view(B, N_k, self.n_heads, self.head_dim).transpose(1, 2)

        attn_logits = torch.matmul(q, k.transpose(-2, -1)) / self.scale  # [B, h, Nq, Nk]

        # Optional SMCA 2D distance mask.
        if self.use_smca_mask and q_pix is not None and k_pix is not None:
            attn_logits = attn_logits + self._smca_2d_mask(attn_logits, q_pix, k_pix)

        # Key padding mask.
        if key_padding_mask is not None:
            # [B, 1, 1, N_k] -> set to -inf
            pad = key_padding_mask.unsqueeze(1).unsqueeze(2).to(attn_logits.dtype)
            attn_logits = attn_logits.masked_fill(pad.bool(), float("-inf"))

        attn = torch.softmax(attn_logits, dim=-1)
        attn = self.attn_drop(attn)

        # Optional instrumentation: record lidar-usage statistics (no-op unless
        # capture_attn=True). mean_key = mean attention mass on lidar keys
        # (baseline 1/N_k when unused); focus = fraction of max-attended key.
        if self.capture_attn:
            with torch.no_grad():
                attn_nodrop = torch.softmax(attn_logits, dim=-1)
                self.attn_stats = {
                    # per-head mean attention mass on lidar keys (mean over
                    # batch + query tokens): baseline 1/N_k when lidar unused.
                    "per_head_mean_mass": attn_nodrop.mean(dim=(0, 2)).tolist(),
                    "mean_lidar_mass": attn_nodrop.mean(dim=(0, 2)).mean().item(),
                    "max_key_focus": attn_nodrop.max(dim=-1).values.mean().item(),
                    "mean_entropy": -(attn_nodrop * torch.log(attn_nodrop.clamp_min(1e-9)))
                    .sum(dim=-1).mean().item(),
                }

        out = torch.matmul(attn, v)                        # [B, h, Nq, head_dim]
        out = out.transpose(1, 2).contiguous().view(B, N_vis, self.d_model)
        out = self.out_proj(out)
        out = self.resid_drop(out)

        # Residual + LayerNorm.
        return self.norm(out + vis_tokens)

    @torch.no_grad()
    def lidar_ablation(self, vis_tokens, lidar_tokens, pe_vis, pe_lidar,
                       q_pix=None, k_pix=None, key_padding_mask=None):
        """Quantify lidar influence: fused output with and without lidar content.

        Runs the fusion twice (once with `lidar_tokens`/`pe_lidar` zeroed) and
        reports the per-token RMS / cosine difference, i.e. how much of the
        fused visual representation actually depends on LiDAR. Returns a dict:
            rms_delta, rel_rms_delta, cos_sim, frac_changed
        """
        fused_full = self(vis_tokens, lidar_tokens, pe_vis, pe_lidar,
                          q_pix=q_pix, k_pix=k_pix,
                          key_padding_mask=key_padding_mask)
        zero_l = torch.zeros_like(lidar_tokens)
        zero_p = torch.zeros_like(pe_lidar)
        fused_zero = self(vis_tokens, zero_l, pe_vis, zero_p,
                          q_pix=q_pix, k_pix=k_pix,
                          key_padding_mask=key_padding_mask)
        diff = (fused_full - fused_zero).norm(dim=-1)  # [B, N_vis]
        base = fused_full.norm(dim=-1).clamp_min(1e-6)
        return {
            "rms_delta": diff.mean().item(),
            "rel_rms_delta": (diff / base).mean().item(),
            "cos_sim": F.cosine_similarity(
                fused_full.flatten(0, 1), fused_zero.flatten(0, 1), dim=-1
            ).mean().item(),
            "frac_changed": (diff > 1e-4).float().mean().item(),
        }
