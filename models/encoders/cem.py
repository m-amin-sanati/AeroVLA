"""
CoordinatesEncodingModule (CEM) -- 3D ground-truth-aware positional encodings.

Two independent PE branches:

  * Image PE  (``psi_im``): samples ``num_depth_samples`` points along each
    camera frustum ray for every visual token, projects each 3D point into the
    camera frame via

        p_k^im = T_ci^l . K_i^-1 . p_k(u, v)

    and embeds the ray through an MLP (hidden dim ``d_im``) followed by a linear
    head to ``d_hidden``.  One feature vector per visual token.

  * Point-cloud PE (``psi_pc``): for LiDAR/BEV-token grids, builds a homogeneous
    pillar coordinate ``p_k^pc = (u * u_d, v * v_d, h_k, 1)`` and embeds it with
    ``psi_pc`` (MLP + linear head, output ``d_hidden``).

Every PE is meant to be *added* element-wise to the corresponding token feature
(Z_vis + Gamma_im, Z_lidar + Gamma_pc).  All params are zero-initialized at the
output head so the module is a no-op at init (identity init).
"""

import torch
import torch.nn as nn


def _zero_init_linear(in_dim: int, out_dim: int, bias: bool = True) -> nn.Linear:
    """Linear whose output is exactly zero -> guaranteed no-op at initialisation."""
    lin = nn.Linear(in_dim, out_dim, bias=bias)
    nn.init.zeros_(lin.weight)
    if lin.bias is not None:
        nn.init.zeros_(lin.bias)
    return lin


class _PEMLP(nn.Module):
    """2-layer MLP (ELU) with a zero-init output head; output dim is fixed to ``d_out``."""

    def __init__(self, d_in: int, d_hidden: int, d_out: int) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d_in, d_hidden),
            nn.ELU(inplace=True),
            _zero_init_linear(d_hidden, d_out, bias=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class CoordinatesEncodingModule(nn.Module):
    """Ground-truth 3D-coordinates positional encoding for visual + LiDAR tokens."""

    def __init__(
        self,
        d_hidden: int,
        d_im: int = 128,
        d_pc: int = 64,
        num_depth_samples: int = 32,
        waypoint_dims: int = 3,
    ) -> None:
        """Args:
            d_hidden: hidden/token dim (must match the fusion + projector input dim).
            d_im:     MLP hidden width for the image ray PE (psi_im).
            d_pc:     MLP hidden width for the point-cloud PE (psi_pc).
            num_depth_samples: number of points sampled along each camera ray.
            waypoint_dims: dimensionality of the 3D world-space coordinates
                           (default 3 => x,y,z).
        """
        super().__init__()
        self.d_hidden = d_hidden
        self.num_depth_samples = num_depth_samples
        self.waypoint_dims = waypoint_dims

        # psi_im embeds the (already projected + de-homogenised) 3D ray point
        # p_k^im = T_ci^l . K^-1 . p_k(u,v)  =>  input dim = waypoint_dims.
        self.psi_im = _PEMLP(d_in=waypoint_dims, d_hidden=d_im, d_out=d_hidden)

        # psi_pc embeds the homogeneous pillar coordinate
        # p_k^pc = (u * u_d, v * v_d, h_k, 1) => input dim = waypoint_dims + 1.
        self.psi_pc = _PEMLP(d_in=waypoint_dims + 1, d_hidden=d_pc, d_out=d_hidden)

    def _ray_3d_points(self, depth_samples: torch.Tensor) -> torch.Tensor:
        """Project depth samples in *camera-normalised* pixels to 3D rays.

        Args:
            depth_samples: [B, N_vis, num_depth_samples, 3], where last dim is
                           (x_cam * d, y_cam * d, d); x_cam, y_cam already
                           normalised with the inverse intrinsics.
        Returns:
            homogeneous ray points [B, N_vis, num_depth_samples, waypoint_dims + 1]
            (x_cam, y_cam, d, 1).
        """
        ones = torch.ones(
            (*depth_samples.shape[:3], 1),
            dtype=depth_samples.dtype,
            device=depth_samples.device,
        )
        return torch.cat([depth_samples, ones], dim=-1)

    def image_pe(
        self,
        pixel_coords_norm: torch.Tensor,
        intrinsics_inv: torch.Tensor,
        depths: torch.Tensor,
        extrin_cam_to_lidar: torch.Tensor,
    ) -> torch.Tensor:
        """Compute per-visual-token image positional encoding (Gamma_im).

        Args:
            pixel_coords_norm: [B, N_vis, 2] normalised pixel coords (u,v) in
                               [0,1] (already normalised by image width/height).
            intrinsics_inv:    [B, 3, 3] (or [3, 3]) inverse camera intrinsic K^-1.
            depths:            [B, N_vis, num_depth_samples] signed depths along
                               each frustum ray.
            extrin_cam_to_lidar: [B, 4, 4] (or [4, 4]) camera->lidar transform
                               T_ci^l used for the projection p_k^im = T_ci^l K^-1 p_k(u,v).

        Returns:
            Gamma_im [B, N_vis, d_hidden].
        """
        B, N_vis, _ = pixel_coords_norm.shape
        device, dt = pixel_coords_norm.device, pixel_coords_norm.dtype
        D = depths.shape[2]  # derive from input so it always matches the collator

        # Build homogeneous pixel vector p_k(u,v) = (u, v, 1) per token, then
        # lift to a per-token *number-of-depth-samples* copy for the matmul.
        ones = torch.ones((B, N_vis, 1), dtype=dt, device=device)
        pix_hom = torch.cat([pixel_coords_norm, ones], dim=-1)  # [B, N_vis, 3]
        pix_hom = pix_hom.unsqueeze(2).repeat(1, 1, D, 1)  # [B, N_vis, D, 3]

        # [B, N_vis, D, 3] = K^-1 (B,3,3) @ pix_hom (B,N_vis,D,3)  (row vector * K^-1^T)
        K_inv = intrinsics_inv
        if K_inv.dim() == 2:
            K_inv = K_inv.unsqueeze(0).expand(B, 3, 3)
        p_im = torch.einsum("bij,bnk j->bnki", K_inv, pix_hom)  # [B,N,D,3] (x,y,1)

        # Multiply by per-token depth: (x*d, y*d, d)
        d = depths.unsqueeze(-1)  # [B, N, D, 1]
        ray_pts = p_im * d  # [B, N, D, 3]

        # Apply camera->lidar transform: p_k^im = T_ci^l . (K^-1 . p_k(u,v))
        T = extrin_cam_to_lidar
        if T.dim() == 2:
            T = T.unsqueeze(0).expand(B, 4, 4)
        ray_hom = self._ray_3d_points(ray_pts)  # [B, N, D, waypoint_dims+1]
        ray_3d = torch.einsum("bij,bn d j->bn d i", T, ray_hom)  # [B, N, D, 4]

        # psi_im takes only the 3D part (drop homogeneous coordinate)
        pe = self.psi_im(ray_3d[..., : self.waypoint_dims])  # [B, N, D, d_hidden]

        # Average over the depth samples => one PE per visual token
        return pe.mean(dim=2)  # [B, N_vis, d_hidden]

    def pc_pe(
        self,
        coords: torch.Tensor,
        pillar_dims: torch.Tensor,
        heights: torch.Tensor,
    ) -> torch.Tensor:
        """Compute per-LiDAR-token positional encoding (Gamma_pc).

        Args:
            coords: [B, N_lidar, 2] BEV grid coordinates (u, v) in pillar cells.
            pillar_dims: [B, N_lidar, 2] per-pillar dimensions (u_d, v_d) scaling
                         (bounding-box extents of each pillar).
            heights: [B, N_lidar] pillar height h_k.

        Returns:
            Gamma_pc [B, N_lidar, d_hidden].
        """
        # p_k^pc = (u * u_d, v * v_d, h_k, 1)
        x = coords[..., 0] * pillar_dims[..., 0]  # [B, N_lidar]
        y = coords[..., 1] * pillar_dims[..., 1]
        h = heights
        ones = torch.ones_like(x)
        hid_pc = torch.stack([x, y, h, ones], dim=-1)  # [B, N_lidar, 4]
        return self.psi_pc(hid_pc)  # [B, N_lidar, d_hidden]
