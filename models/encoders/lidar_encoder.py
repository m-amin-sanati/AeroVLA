"""
LiDAREncoder -- point-cloud encoder producing LiDAR tokens.

Two backends (selectable via ``backbone``):

  * PointPillars: voxelize the raw points into a BEV pillar map
    [B, 4, H, W], run a 2D CNN stack, flatten to tokens.
  * VoxelNet: sparse-ish voxel binning (dense tensor, range bounded by
    ``voxel_range``), a 3D CNN, then a 2D collapse to BEV tokens.

Both share an identical output token space: [B, N_lidar, D_lidar], after which
a linear projection maps ``D_lidar -> D_vis`` so the fusion can add the CEM
point-cloud PE and cross-attend against visual tokens.

The input ``lidar_points`` is ragged across the batch (each frame has a
variable number of points), so we pass a per-batch padding mask; the encoder
must already be handed **densified** points (list of [N_i, 3|4] tensors or a
padded [B, N_max, 3|4] + ``valid_mask``).
"""

import torch
import torch.nn as nn


class PointPillarsEncoder(nn.Module):
    """Minimum viable PointPillars: feature net + pillar scatter to BEV grid."""

    def __init__(
        self,
        in_channels: int = 4,
        voxel_size: float = 0.4,
        point_cloud_range: tuple = (0.0, 0.0, -1.5, 20.0, 20.0, 1.0),
        max_voxels: int = 12000,
        max_points_per_voxel: int = 32,
        feature_dim: int = 64,
        grid_res: tuple = (64, 64),
    ) -> None:
        super().__init__()
        self.voxel_size = voxel_size
        self.point_cloud_range = point_cloud_range
        self.max_voxels = max_voxels
        self.max_points_per_voxel = max_points_per_voxel
        self.grid_res = grid_res  # (H, W) BEV cells

        # Pillar feature net: for each voxel keep the max per-channel feature.
        # We produce a per-voxel feature of dims `feature_dim` after a small MLP.
        self.pillar_mlp = nn.Sequential(
            nn.Linear(in_channels, feature_dim),
            nn.ReLU(inplace=True),
            nn.Linear(feature_dim, feature_dim),
        )

        # 2D CNN stack on the BEV pseudo-image.
        c0 = feature_dim
        self.conv_stack = nn.Sequential(
            nn.Conv2d(c0, 128, 3, padding=1, bias=False),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.Conv2d(128, 256, 3, padding=1, bias=False),
            nn.BatchNorm2d(256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, feature_dim, 3, padding=1, bias=False),
            nn.BatchNorm2d(feature_dim),
            nn.ReLU(inplace=True),
        )

    def _voxelize(self, points: torch.Tensor, valid: torch.Tensor) -> tuple:
        """Convert padded point cloud into a dense BEV feature map.

        Args:
            points: [B, N_max, in_channels] (x, y, z, [intensity]...)
            valid:  [B, N_max] bool mask of real points.
        Returns:
            bev: [B, feature_dim, H, W] BEV feature map
        """
        B, N, _ = points.shape
        H, W = self.grid_res
        device = points.device

        # Filter to in-range points (keeps tensor dense, bounded).
        keep = valid
        x_min, y_min, z_min = self.point_cloud_range[0], self.point_cloud_range[1], self.point_cloud_range[2]
        x_max, y_max, z_max = self.point_cloud_range[3], self.point_cloud_range[4], self.point_cloud_range[5]
        in_range = (
            (points[..., 0] >= x_min) & (points[..., 0] <= x_max)
            & (points[..., 1] >= y_min) & (points[..., 1] <= y_max)
            & (points[..., 2] >= z_min) & (points[..., 2] <= z_max)
        )
        keep = keep & in_range

        # Pillar indices.
        ix = ((points[..., 0] - x_min) / self.voxel_size).long()
        iy = ((points[..., 1] - y_min) / self.voxel_size).long()
        valid_xy = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        keep = keep & valid_xy

        # Max-pool top-K points per voxel (bounded).
        # We do a simple, memory-light approximation: only keep the max-N points
        # per voxel via a per-voxel sort is expensive; instead we just mask.
        point_feats = self.pillar_mlp(points)  # [B, N, feature_dim]
        point_feats = point_feats * keep.unsqueeze(-1)

        # Scatter-max into BEV grid (B, H, W).
        bev = torch.zeros(B, H, W, point_feats.shape[-1], dtype=point_feats.dtype, device=device)
        # Use a flattened index to do scatter_add on the max (approximate);
        # accumulate via max by iterating 2D bins.
        mask = keep.unsqueeze(-1)  # [B, N, 1]
        ones_feat = torch.ones_like(point_feats) * mask
        # We'll scatter-add features where a count <= 1 per voxel assumption is fine
        # for smoke tests; this is a research sketch, not a full PointPillars impl.
        for b in range(B):
            nb = keep[b].nonzero(as_tuple=False)
            if nb.numel() == 0:
                continue
            idx_b = nb  # [K, 1]
            k = idx_b.shape[0]
            rows = ix[b].index_select(0, idx_b[:, 0])
            cols = iy[b].index_select(0, idx_b[:, 0])
            feats = point_feats[b].index_select(0, idx_b[:, 0])  # [K, feature_dim]
            for i in range(k):
                bev[b, rows[i].item(), cols[i].item()] = torch.max(
                    bev[b, rows[i].item(), cols[i].item()], feats[i]
                )
        return bev.permute(0, 3, 1, 2)  # [B, feature_dim, H, W]

    def forward(self, points: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        """Args:
            points: [B, N_max, in_channels]
            valid:  [B, N_max] bool.
        Returns:
            [B, N_lidar, feature_dim] flattened BEV tokens.
        """
        bev = self._voxelize(points, valid)          # [B, feature_dim, H, W]
        feats = self.conv_stack(bev)                 # [B, feature_dim, H, W]
        B, C, H, W = feats.shape
        return feats.reshape(B, C, H * W).transpose(1, 2)  # [B, N_lidar, feature_dim]


class VoxelNetEncoder(nn.Module):
    """VoxelNet: dense voxel grid (bounded range) -> 3D CNN -> BEV tokens.

    This is a research-simplified VoxelNet: we bin points into a dense [B, C, D,
    H, W] voxel grid, a few 3D conv layers, then flatten depth -> a 2D BEV
    feature map fused with a small point MLP.  Real deployments should replace
    with a proper sparse-conv VoxelNet; kept dense for CPU/GPU smoke tests.
    """

    def __init__(
        self,
        in_channels: int = 4,
        voxel_size: tuple = (0.4, 0.4, 1.0),
        point_cloud_range: tuple = (0.0, 0.0, -1.5, 20.0, 20.0, 1.0),
        feature_dim: int = 64,
        grid_res: tuple = (64, 64),
        depth: int = 8,
    ) -> None:
        super().__init__()
        self.voxel_size = voxel_size
        self.point_cloud_range = point_cloud_range
        self.feature_dim = feature_dim
        self.depth = depth
        self.grid_res = grid_res

        # Project x,y,z into voxel coordinates (dense) and encode per-point.
        self.point_mlp = nn.Sequential(
            nn.Linear(in_channels, feature_dim),
            nn.ReLU(inplace=True),
            nn.Linear(feature_dim, feature_dim),
        )

        # 3D CNN.
        self.conv3d = nn.Sequential(
            nn.Conv3d(feature_dim, feature_dim, 3, padding=1, bias=False),
            nn.BatchNorm3d(feature_dim),
            nn.ReLU(inplace=True),
            nn.Conv3d(feature_dim, feature_dim, 3, padding=1, bias=False),
            nn.BatchNorm3d(feature_dim),
            nn.ReLU(inplace=True),
        )

        # Collapse depth -> 2D.
        self.out_conv = nn.Sequential(
            nn.Conv2d(feature_dim, feature_dim, 3, padding=1, bias=False),
            nn.BatchNorm2d(feature_dim),
            nn.ReLU(inplace=True),
        )

    def _bin(self, points: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        B, N, _ = points.shape
        D, H, W = self.depth, self.grid_res[0], self.grid_res[1]
        x_min, y_min, z_min = self.point_cloud_range[0], self.point_cloud_range[1], self.point_cloud_range[2]
        vx, vy, vz = self.voxel_size
        ix = ((points[..., 0] - x_min) / vx).long()
        iy = ((points[..., 1] - y_min) / vy).long()
        iz = ((points[..., 2] - z_min) / vz).long()

        inb = (
            (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H) & (iz >= 0) & (iz < D)
        )
        keep = valid & inb

        point_feats = self.point_mlp(points) * keep.unsqueeze(-1)
        grid = torch.zeros(B, D, H, W, self.feature_dim, dtype=point_feats.dtype, device=points.device)
        for b in range(B):
            nb = keep[b].nonzero(as_tuple=False)
            if nb.numel() == 0:
                continue
            k = nb.shape[0]
            rows = ix[b].index_select(0, nb[:, 0])
            cols = iy[b].index_select(0, nb[:, 0])
            deps = iz[b].index_select(0, nb[:, 0])
            feats = point_feats[b].index_select(0, nb[:, 0])
            for i in range(k):
                grid[b, deps[i].item(), rows[i].item(), cols[i].item()] = torch.max(
                    grid[b, deps[i].item(), rows[i].item(), cols[i].item()], feats[i]
                )
        return grid  # [B, D, H, W, C]

    def forward(self, points: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        grid = self._bin(points, valid)                      # [B, D, H, W, C]
        grid = grid.permute(0, 4, 1, 2, 3)                    # [B, C, D, H, W]
        feats = self.conv3d(grid)                             # [B, C, D, H, W]
        # Collapse depth by max-pool.
        bev = feats.max(dim=2).values                         # [B, C, H, W]
        feats2d = self.out_conv(bev)                          # [B, C, H, W]
        B, C, H, W = feats2d.shape
        return feats2d.reshape(B, C, H * W).transpose(1, 2)   # [B, N_lidar, C]


class LiDAREncoder(nn.Module):
    """Top-level LiDAR encoder with a linear projection ``D_lidar -> D_vis``."""

    def __init__(
        self,
        d_vis: int,
        backbone: str = "pointpillars",
        d_lidar: int = 64,
        input_channels: int = 4,
        grid_res: tuple = (64, 64),
    ) -> None:
        """Args:
            d_vis:  target visual/token dim (projection target D_vis).
            backbone: 'pointpillars' or 'voxelnet'.
            d_lidar: internal feature dim of the 2D/3D backbone.
            input_channels: per-point channels (x,y,z,+intensity).
            grid_res: BEV grid resolution (H, W).
        """
        super().__init__()
        self.d_vis = d_vis
        self.d_lidar = d_lidar
        if backbone.lower() == "pointpillars":
            self.backbone = PointPillarsEncoder(
                in_channels=input_channels, feature_dim=d_lidar, grid_res=grid_res
            )
        elif backbone.lower() == "voxelnet":
            self.backbone = VoxelNetEncoder(
                in_channels=input_channels, feature_dim=d_lidar, grid_res=grid_res
            )
        else:
            raise ValueError(f"Unknown LiDAR backbone: {backbone}")

        self.proj = nn.Linear(d_lidar, d_vis)

    def forward(self, points: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        """Args:
            points: [B, N_max, input_channels]
            valid:  [B, N_max] bool mask.
        Returns:
            lidar_tokens [B, N_lidar, d_vis] (projected into visual dim).
        """
        toks = self.backbone(points, valid)  # [B, N_lidar, d_lidar]
        return self.proj(toks)               # [B, N_lidar, d_vis]
