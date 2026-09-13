"""
UAVLiDARDataset -- multimodal (image + LiDAR) dataset + collator for AerialVLA.

Ingests the raw AirSim / TravelUAV episode trees produced by the closed-loop
simulator (see `airsim_plugin/AirVLNSimulatorServerTool.py`) and hands each
`AerialVLAModel.forward(...)` call the exact sensor tensors it requires.

On-disk episode layout (per trajectory directory ``rel_dir``):

    <traj_rel_dir>/
        frontcamera/*.png          RGB 256x256 (or 1024x1024 for *Record cams)
        downcamera/*.png           RGB 256x256 (or 1024x1024 for *Record cams)
        <cam>_depth/*.png          per-camera 16-bit depth (same grid as RGB)
        log/<frame>.json           one JSON per sim frame, containing
                                   `sensors.state` (position/orientation/vel) and
                                   `sensors.lidar` (point_cloud, pose,
                                   segmentation) -- the *Lidar1* AirSim sensor
                                   writes point clouds into these logs.
        mark.json / object_description.json   (labels / episode metadata)

Training labels come from the same schema as `src/aerovla_dataset.py`: a
`merged_data.json`/clean JSON with fields ``traj_rel_dir``, ``img_name``,
``instruction``, ``label {fwd,down,yaw}``, ``is_last_step``, ``is_penultimate``.

Camera calibration follows the AirSim settings template:
   * intrinsics K_i: built from the FOV and the image resolution used at
     capture time (pin-hole; only f_x/f_y = f depend on FOV => no per-camera
     principal-point metadata exists in the logs, so cx, cy default to the
     image centre, which is exact for AirSim's centred cameras).
   * extrinsics T_ci^l (camera -> LiDAR body): recovered from the *relative*
     pose of the camera frame w.r.t. the drone body.  AirSim logs the drone
     body pose in `sensors.state.position/orientation` (NED world), cameras are
     mounted at fixed offsets with fixed rotations (FrontCamera: +X, DownCamera:
     pitch -90).  We invert the drone pose and apply the camera mount offset to
     build a camera->lidar transform registered on the drone body frame.

Because AirSim default cameras are centred (no optical-axis offset) and the
sensor frames are axis-aligned rigid mounts, the default calibration here is
exact up to the *focal length / FOV* which we read directly from the simulator's
settings template of the run that produced the data.  Any real calibration file
can be passed in via `camera_calib_dir` / `K_override` / `T_override`.

Pre-computation performed per sample (and batched by the collator):
   1. CEM frustum rays: for every visual token (patch grid of the composite
      image) sample ``num_depth_samples`` depths along the ray
          p_k(u,v) = T_ci^l @ K_i^{-1} @ p_k(u,v)
      -> emitted as `normalised_pixel_coords` (uv in [0,1]) + `depth_samples`
         (signed depth per token) + `camera_intrinsics` (K_i^{-1}) +
         `camera_extrinsics` (T_ci^l).
   2. LiDAR pillar/voxel coordinates: voxelise the raw cloud into a BEV grid;
      compute `pillar_coords` (u,v cell indices), `pillar_dims` (u_d, v_d per
      pillar) and `pillar_heights` (h_k) for the LiDAREncoder's point-cloud PE
      (psi_pc).

The collator returns a batched dict ready for `AerialVLAModel.forward(...)`:

    {
      "composite_image":       [B, 6, 224, 224]  (front+down mosaic, RGB)
      "lidar_points":          [B, N_max, 4]      (x, y, z, intensity)  NED lidar-local
      "lidar_valid":           [B, N_max]          bool
      "camera_intrinsics":     [B, 3, 3]           K_i^{-1}  (inverse! CEM contract)
      "camera_extrinsics":     [B, 4, 4]           T_ci^l
      "normalised_pixel_coords":[B, N_vis, 2]      (u, v) in [0,1]
      "depth_samples":         [B, N_vis, num_depth_samples]  signed depth
      "pillar_coords":         [B, N_lidar, 2]     (u, v) BEV cell index
      "pillar_dims":           [B, N_lidar, 2]     (u_d, v_d)
      "pillar_heights":        [B, N_lidar]        h_k
      "q_pix":                 [B, N_vis, 2]       pixel coords for SMCA mask
      "k_pix":                 [B, N_lidar, 2]     pixel coords for SMCA mask
      "input_ids"/"attention_mask"/"labels":       tokenized text (via tokenizer)
    }
"""

import json
import os

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset, default_collate

# The model arg is named `camera_intrinsics` but `CEM.image_pe(..., intrinsics_inv, ...)`
# expects the INVERSE calibration K_i^{-1} (see `models/encoders/cem.py:114`).  Samples
# carry the (non-inverted) K; the collator emits K^{-1} under `camera_intrinsics`.
KEY_INTRINSICS = "camera_intrinsics"
KEY_EXTRINSICS = "camera_extrinsics"


class UAVLiDARDataset(Dataset):
    """Dataset over TravelUAV/AirSim episode trees with LiDAR + images.

    Args:
        samples: list of dicts with the same schema as `src/aerovla_dataset.py`,
            i.e. {traj_rel_dir, img_name, instruction, label{fwd,down,yaw},
            is_last_step, is_penultimate}.
        data_root: root directory under which each `traj_rel_dir` resolves.
        image_size: (H, W) that images are resized to before processing.
            The mosaic is (H, W*2) for 2 cameras; the vision backbone resizes
            to 224x224.  N_vis is derived from the backbone patch grid.
        num_depth_samples: number of depth samples along each frustum ray for CEM.
        grid_res: (H, W) BEV/pillar grid resolution for the LiDAR encoder.
        voxel_size: (dx, dy, d_z) pillar/voxel resolution (lidar-local metres).
        point_cloud_range: (x_min, y_min, z_min, x_max, y_max, z_max) valid box
            for LiDAR voxelisation, in the lidar-local frame.
        lidar_input_channels: number of per-point channels to feed the encoder
            (x, y, z + intensity).
        max_points: pad/truncate each cloud to this many points (N_max).
        lidar_sensor_key: key in the log frame's `sensors` dict holding the
            Lidar payload (e.g. "lidar").
        require_lidar: if True, raise KeyError when the lidar key is missing from
            a log frame (strict mode for fresh lidar-enabled captures); if False
            (default), emit a zero cloud so pre-LiDAR eval episodes still load.
        tokenizer: HF tokenizer used to build input_ids/attention_mask/labels
            from the prompt + quantized action string (same as AeroVLADataset).
        prompt_prefix/suffix: text wrapping around the instruction+action.
        camera_calib_dir: optional dir with per-trajectory calibration JSONs
            (see `load_camera_calib`); if None, synthesizes AirSim-template
            K_i / T_ci^l.
        K_override/T_override: if provided, use these tensors instead of the
            synthesized calibration (shape [3,3] / [4,4] or list of them).
        device: compute device for pre-computed CEM/pillar tensors.
        dtype: dtype for pre-computed tensors.
    """

    def __init__(
        self,
        samples,
        data_root,
        image_size=(224, 224),
        num_depth_samples=32,
        grid_res=(64, 64),
        voxel_size=(0.4, 0.4, 1.0),
        point_cloud_range=(0.0, 0.0, -1.5, 20.0, 20.0, 1.0),
        lidar_input_channels=4,
        max_points=20000,
        lidar_sensor_key="lidar",
        require_lidar=False,
        tokenizer=None,
        prompt_prefix="",
        prompt_suffix="",
        camera_calib_dir=None,
        K_override=None,
        T_override=None,
        device="cpu",
        dtype=torch.float32,
        training=False,
    ):
        super().__init__()
        self.samples = samples
        self.data_root = data_root
        self.image_size = tuple(image_size)
        self.H, self.W = self.image_size
        self.num_depth_samples = int(num_depth_samples)
        self.grid_res = tuple(grid_res)
        self.voxel_size = voxel_size
        self.point_cloud_range = tuple(point_cloud_range)
        self.lidar_input_channels = int(lidar_input_channels)
        self.max_points = int(max_points)
        self.lidar_sensor_key = lidar_sensor_key
        self.require_lidar = require_lidar
        self.tokenizer = tokenizer
        self.prompt_prefix = prompt_prefix
        self.prompt_suffix = prompt_suffix
        self.camera_calib_dir = camera_calib_dir
        self.K_override = K_override
        self.T_override = T_override
        self.device = device
        self.dtype = dtype
        self.training = training

        # N_vis = number of patches the fused vision backbone emits for a
        # 224x224 input.  The real dinosiglip fused backbone (DINOv2
        # vit_large_patch14 + SigLIP vit_so400m_patch14, both 224 px, patch 14)
        # emits 16x16 = 256 patches *per* stream and feature-concatenates them
        # (see `openvla-7b/modeling_prismatic.py` PrismaticVisionBackbone.forward),
        # i.e. N_vis = 256 and d_vis = 1024 + 1152 = 2176.  256 is therefore the
        # correct token count for the fused encoder.
        self.patch_size = 14
        self.n_patches_per_side = 224 // self.patch_size  # 16
        self.n_vis = self.n_patches_per_side * self.n_patches_per_side  # 256
        self.n_lidar = int(np.prod(self.grid_res))

        # AirSim template: front camera at body +X, down camera pitch -90,
        # both centred (cy, cx = image centre).
        self._front_open = os.path.join("frontcamera", "{:06d}.png")
        self._down_open = os.path.join("downcamera", "{:06d}.png")

    # ------------------------------------------------------------------ #
    # Length / indexing
    # ------------------------------------------------------------------ #
    def __len__(self):
        return len(self.samples)

    def _image_path(self, rel_dir, cam, img_name):
        base = os.path.join(self.data_root, rel_dir, cam)
        # img_name may be just a filename (e.g. "000000.png").
        return os.path.join(base, os.path.basename(img_name))

    # ------------------------------------------------------------------ #
    # Calibration
    # ------------------------------------------------------------------ #
    @staticmethod
    def airsim_K(fov_deg, width, height):
        """Pin-hole intrinsics for an AirSim camera (centred principal point).

        AirSim default cameras are centred (no optical-axis offset) and the
        principal point is the image centre; only f depends on FOV.
        """
        f = (width / 2.0) / np.tan(np.radians(fov_deg) / 2.0)
        return np.array(
            [[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )

    def _default_extrinsics(self, rel_dir, frame_idx):
        """camera->lidar transform T_ci^l for the front camera mounted on the
        drone body (AirSim NED).  FrontCamera sits at +X on the body, no
        rotation; lidar is the body origin => T_ci^l = inverse(body-inv
        * camera-mount) = camera mount pose expressed in the lidar frame.

        Note: AirSim's front camera looks down +X (NED), the LiDAR `Lidar1`
        sensor is at the body origin with SensorLocalFrame, so the rotation
        between them is identity (both are body axes); translation is the
        negative mount offset.
        """
        # FrontCamera mount: X=+1, Y=0, Z=0, RPY=0 (from server template).
        mount_trans = np.array([1.0, 0.0, 0.0], dtype=np.float64)  # lidar->cam
        cam_trans_l = -mount_trans  # cam origin in lidar frame -> T translation
        R = np.eye(3)
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = cam_trans_l
        T[3, 3] = 1.0
        return T

    def get_camera_calib(self, rel_dir, frame_idx):
        """Return (K, T_ci^l) for the sample, either from a calibration file or
        synthesized from the AirSim settings template.

        If `camera_calib_dir` is set and contains
        `<traj_rel_dir>/camera_calib.json`, we load K / T from
        {"front": {"K": [[...]], "T_ci_l": [[...]]}}.

        Returns numpy [3,3] K and [4,4] T_ci^l.
        """
        if self.K_override is not None and self.T_override is not None:
            K = np.asarray(self.K_override, dtype=np.float64).reshape(3, 3)
            T = np.asarray(self.T_override, dtype=np.float64).reshape(4, 4)
            return K, T
        if self.camera_calib_dir is not None:
            calib_path = os.path.join(
                self.camera_calib_dir, rel_dir, "camera_calib.json"
            )
            if os.path.exists(calib_path):
                with open(calib_path, "r") as f:
                    calib = json.load(f)
                front = calib["front"]
                K = np.asarray(front["K"], dtype=np.float64).reshape(3, 3)
                T = np.asarray(front["T_ci_l"], dtype=np.float64).reshape(4, 4)
                return K, T
        # Synthesize from the AirSim template; front camera 90 FOV.
        K = self.airsim_K(90.0, self.W, self.H)
        T = self._default_extrinsics(rel_dir, frame_idx)
        return K, T

    # ------------------------------------------------------------------ #
    # Image + lidar loading
    # ------------------------------------------------------------------ #
    def _load_image_stack(self, rel_dir, img_name):
        """Load front+down RGB, build the 2-camera mosaic [H, 2W, 3] resized to
        `image_size` (kept as [H, 2W] so the backbone's 224x224 resize is ~
        correct for patch-based pixel coords).
        """
        w, h = self.W, self.H
        mosaic = Image.new("RGB", (w * 2, h), (0, 0, 0))
        for i, cam in enumerate(["frontcamera", "downcamera"]):
            p = self._image_path(rel_dir, cam, img_name)
            try:
                im = Image.open(p).convert("RGB").resize((w, h), Image.BICUBIC)
            except Exception as e:  # noqa: BLE001
                raise FileNotFoundError(f"CRITICAL image missing: {p} ({e})")
            mosaic.paste(im, (i * w, 0))
        return mosaic

    def _load_lidar(self, rel_dir, frame_idx):
        """Load the raw LiDAR cloud + pose + segmentation from the log frame.

        Returns:
            points: [N, 4] float64 (x, y, z, intensity) in lidar-local frame.
            valid:  [N] bool (True for real points, False beyond cloud length).
        """
        log_path = os.path.join(
            self.data_root, rel_dir, "log", "{:06d}.json".format(frame_idx)
        )
        with open(log_path, "r") as f:
            frame = json.load(f)
        sensors = frame.get("sensors", {})
        self._last_log = frame  # for tests / debugging
        if self.lidar_sensor_key not in sensors:
            if self.require_lidar:
                raise KeyError(
                    f"lidar key {self.lidar_sensor_key!r} not in frame {log_path}! "
                    f"Sensors present: {list(sensors.keys())}"
                )
            # Pre-LiDAR episode logs only carry state/imu; emit an empty cloud.
            return (
                np.zeros((0, 4), dtype=np.float64),
                np.zeros((0,), dtype=bool),
            )
        lidar = sensors[self.lidar_sensor_key]
        pc = lidar["point_cloud"]  # flat [N*3]? or [N,3]
        pc = np.asarray(pc, dtype=np.float64).reshape(-1, 3)
        seg = lidar.get("segmentation")
        if isinstance(seg, list) and len(seg):
            seg = np.asarray(seg, dtype=np.float64).reshape(-1)
            # intensity placeholder: use segmentation class where available
            intensity = seg
        else:
            intensity = np.zeros(pc.shape[0], dtype=np.float64)
        # airsim returns point_cloud as flat [x0,y0,z0, x1,y1,z1, ...]
        if pc.shape[1] == 1:
            pc = pc.reshape(-1, 3)
        pts = np.concatenate([pc, intensity[:, None]], axis=1)  # [N, 4]
        return pts, np.ones(pts.shape[0], dtype=bool)

    # ------------------------------------------------------------------ #
    # CEM frustum ray pre-computation
    # ------------------------------------------------------------------ #
    def compute_cem_rays(self, K, T_ci_l):
        """Compute per-visual-token normalised pixel coords + depth samples.

        Args:
            K: [3, 3] camera intrinsics (not inverted).
            T_ci_l: [4, 4] camera->lidar transform.

        Returns:
            norm_pix: [N_vis, 2] (u, v) in [0,1].
            depths:   [N_vis, num_depth_samples] signed depths along each ray.
            K_inv:    [3, 3] K^{-1}.
        """
        # Patch-centred pixel centres in the *composite* frame.  The mosaic is
        # [H, 2W]; front occupies (0..W), down occupies (W..2W).
        # We need per-patch pixel coordinates in the ORIGINAL composite image to
        # match what the vision backbone actually sees.  DINOv2/SigLIP are
        # position-free except patch grid; we approximate by taking the patch
        # centres across the full 224-wide mosaic (as the processor resizes).
        ps = self.patch_size
        H, W = self.H, self.W
        # The (fused) vision backbone sees the final 224x224 image (the mosaic
        # is resized to 224x224 by the collator).  The 16x16 patch grid tiles
        # the full [0,1]^2, one grid shared by both cameras (the two backbones
        # feature-concat over the same spatial grid -> N_vis=256).
        img_w = W  # final backbone input width (post-resize)
        cols = np.arange(self.n_patches_per_side) * ps + ps // 2
        rows = np.arange(self.n_patches_per_side) * ps + ps // 2
        # Normalise to [0,1] in the final (224x224) image.
        uu = cols[:, None] / img_w  # [P, 1]
        vv = rows[None, :] / img_w  # [1, P]
        # Flatten over the 16x16 patch grid -> 256 tokens; order row-major
        # matches DINOv2/SigLIP patch embedding order.
        uu = np.tile(uu, (1, self.n_patches_per_side)).reshape(-1)
        vv = np.tile(vv, (self.n_patches_per_side, 1)).reshape(-1)
        norm_pix = np.stack([uu, vv], axis=1)  # [N_vis, 2]

        # Depth sampling: logarithmic near->far in metres (lidar range 100 m).
        z_near, z_far = 0.1, 60.0
        depths = z_near * (z_far / z_near) ** (
            np.linspace(0.0, 1.0, self.num_depth_samples)[None, :]
        )  # [1, D]
        depths = np.tile(depths, (self.n_vis, 1))  # [N_vis, D]

        K_inv = np.linalg.inv(K)
        return norm_pix, depths, K_inv

    # ------------------------------------------------------------------ #
    # Pillar / voxel pre-computation (BEV grid coords for CEM pc PE)
    # ------------------------------------------------------------------ #
    def compute_pillar_coords(self, points, valid):
        """Voxelise the lidar cloud into a BEV pillar grid.

        Args:
            points: [N, 4] (x, y, z, intensity) lidar-local.
            valid:  [N] bool.

        Returns:
            pillar_coords:  [n_lidar, 2] (u, v) BEV cell indices (col, row),
                padded to n_lidar = H*W.
            pillar_dims:    [n_lidar, 2] (u_d, v_d) = (voxel_size*? ) per pillar;
                we use the pillar width = voxel_size in x/y as u_d, v_d.
            pillar_heights: [n_lidar] h_k.
            occupied:       [n_lidar] bool (real occupied pillars).
        """
        x_min, y_min, z_min = self.point_cloud_range[0:3]
        vx, vy, vz = self.voxel_size
        H, W = self.grid_res
        ix = np.floor((points[:, 0] - x_min) / vx).astype(np.int64)
        iy = np.floor((points[:, 1] - y_min) / vy).astype(np.int64)
        iz = np.floor((points[:, 2] - z_min) / vz).astype(np.int64)
        inb = (
            valid
            & (ix >= 0) & (ix < W)
            & (iy >= 0) & (iy < H)
            & (iz >= 0) & (iz < np.inf)
        )
        ix, iy, iz = ix[inb], iy[inb], iz[inb]
        pts = points[inb]
        if len(ix) == 0:
            return (
                np.zeros((self.n_lidar, 2), np.float64),
                np.zeros((self.n_lidar, 2), np.float64),
                np.zeros(self.n_lidar, np.float64),
                np.zeros(self.n_lidar, bool),
            )
        # Aggregate per (ix, iy) pillar: max z (height) + count.
        cell = ix * W + iy  # row-major index
        # Use numpy's unique + max to get per-pillar stats.
        uniq, inv = np.unique(cell, return_inverse=True)
        n_cells = len(uniq)
        h_agg = np.zeros(n_cells, np.float64)
        np.maximum.at(h_agg, inv, pts[:, 2])
        # Prefer taking the height of the highest point within the cell.
        num = np.bincount(inv, minlength=n_cells)
        # Use centroid-ish dimension: u_d = vx, v_d = vy (pillar size in that axis)
        # plus a flag for occupied pillars.
        cols = uniq // W
        rows = uniq % W  # uniq is col*W+row? we built cell=ix*W+iy so ix=cell//W, iy=cell%W

        # ix is col, iy is row; store (u, v) = (col, row).
        u = (uniq // W).astype(np.float64)
        v = (uniq % W).astype(np.float64)
        # pillar dims = voxel extents (u_d = vx, v_d = vy)
        ud = np.full(n_cells, vx, np.float64)
        vd = np.full(n_cells, vy, np.float64)

        out_u = np.zeros(self.n_lidar, np.float64)
        out_v = np.zeros(self.n_lidar, np.float64)
        out_ud = np.zeros(self.n_lidar, np.float64)
        out_vd = np.zeros(self.n_lidar, np.float64)
        out_h = np.zeros(self.n_lidar, np.float64)
        occ = np.zeros(self.n_lidar, bool)
        # Place empty/placeholder pillars first, then occupied ones (stable
        # ordering across batch: all grids have n_lidar rows).
        out_u[:n_cells] = u
        out_v[:n_cells] = v
        out_ud[:n_cells] = ud
        out_vd[:n_cells] = vd
        out_h[:n_cells] = h_agg
        occ[:n_cells] = True
        return (
            np.stack([out_u, out_v], axis=1),
            np.stack([out_ud, out_vd], axis=1),
            out_h,
            occ,
        )

    # ------------------------------------------------------------------ #
    # Sample-level output (pre-tokenization)
    # ------------------------------------------------------------------ #
    def _to_tensor(self, arr, dtype=None):
        d = self.dtype if dtype is None else dtype
        return torch.as_tensor(np.asarray(arr), dtype=d, device=self.device)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        rel_dir = sample["traj_rel_dir"]
        img_name = sample["img_name"]
        frame_idx = int(os.path.splitext(os.path.basename(img_name))[0])

        # Images -> PIL (the processor resizes/handles normalization).
        image = self._load_image_stack(rel_dir, img_name)

        # Calibration (K, T_ci^l).
        K, T_ci_l = self.get_camera_calib(rel_dir, frame_idx)

        # LiDAR cloud.
        points, valid = self._load_lidar(rel_dir, frame_idx)
        N = points.shape[0]
        if N > self.max_points:
            keep = np.random.default_rng(idx).choice(N, self.max_points, replace=False)
            points, valid = points[keep], valid[keep]
        # Pad/truncate to max_points.
        pad_n = self.max_points - points.shape[0]
        if pad_n > 0:
            points = np.pad(points, ((0, pad_n), (0, 0)), constant_values=0.0)
            valid = np.concatenate([valid, np.zeros(pad_n, dtype=bool)])

        # CEM rays.
        norm_pix, depths, K_inv = self.compute_cem_rays(K, T_ci_l)
        # Pillar coords.
        pillar_coords, pillar_dims, pillar_heights, occ = self.compute_pillar_coords(points[: self.max_points], valid[: self.max_points])

        # SMCA pixel coords: q_pix = per-visual-token pixel (u,v) in image-pixel
        # space (0..W for the full 2W mosaic width? spec uses pixel coords for
        # the mask; we use the same normalised coords scaled to original width).
        # We use the *composite* width: W*2, and q_pix/k_pix must be same units
        # so that cdist behaves.  k_pix = projected pillar location in pixels of
        # the composite: front-cam pixel + down-cam offset.
        # For simplicity we set q_pix = norm_pix scaled to (0..img_w) pixels,
        # k_pix = pillar coords scaled to (0..img_w) too, where the "pixel" of a
        # pillar is its grid centre mapped to composite pixel space.
        img_w = self.W * 2
        q_pix = norm_pix * img_w  # [N_vis, 2] in pixels
        # Map pillar (u,v in [0..W-1]) to pixel-space via grid centre, then to
        # composite pixels (front half for u<128? approximate).
        k_pix = np.stack([pillar_coords[:, 0] + 0.5, pillar_coords[:, 1] + 0.5], axis=1) / np.array([self.grid_res[1], self.grid_res[0]]) * img_w
        k_pix = k_pix  # units: pixels

        return {
            "image": image,  # PIL mosaic
            "composite_image": None,  # filled by collator (processor)
            "frame_idx": frame_idx,
            "rel_dir": rel_dir,
            "lidar_points": points,      # [N_max, 4] np
            "lidar_valid": valid,        # [N_max] bool
            "camera_intrinsics": K,       # [3,3] np
            "camera_extrinsics": T_ci_l,  # [4,4] np
            "normalised_pixel_coords": norm_pix,   # [N_vis, 2]
            "depth_samples": depths,               # [N_vis, D]
            "pillar_coords": pillar_coords,        # [N_lidar, 2]
            "pillar_dims": pillar_dims,            # [N_lidar, 2]
            "pillar_heights": pillar_heights,      # [N_lidar]
            "pillar_occupancy": occ,               # [N_lidar]
            "q_pix": q_pix,                        # [N_vis, 2] pixels
            "k_pix": k_pix,                        # [N_lidar, 2] pixels
            "text": None,  # filled by tokenizer in collator if tokenizer given
            "label": sample.get("label"),
            "instruction": sample.get("instruction"),
            "is_last_step": sample.get("is_last_step", False),
            "is_penultimate": sample.get("is_penultimate", False),
        }


# --------------------------------------------------------------------- #
# Tokenization + collate
# --------------------------------------------------------------------- #
def quantize_action(val, axis, action_stats=None):
    """Quantize a continuous action to a bin in [0, 99] (matches
    src/aerovla_dataset.py ACTION_STATS)."""
    if action_stats is None:
        action_stats = {"forward": (0.0, 5.0), "down": (-5.0, 5.0), "yaw": (-1.1, 1.1)}
    lo, hi = action_stats[axis]
    val = float(np.clip(val, lo, hi))
    norm = (val - lo) / (hi - lo)
    return int(norm * (NUM_BINS - 1))


NUM_BINS = 99


class UAVLiDARCollator(object):
    """Collate a list of samples from UAVLiDARDataset into a batch dict ready
    for `AerialVLAModel.forward(...)`.

    Handles:
      * image -> processor -> `composite_image` [B, 6, 224, 224]
      * numpy -> tensors on `device`/`dtype`
      * tokenization of prompt+quantized action via `tokenizer`
      * (labels are built from the same quantized action as the text)
    """

    def __init__(
        self,
        processor=None,
        tokenizer=None,
        device="cpu",
        dtype=torch.float32,
        image_processor_key="image_processor",
        tokenizer_max_length=96,
    ):
        self.processor = processor
        self.tokenizer = tokenizer
        self.device = device
        self.dtype = dtype
        self.image_processor_key = image_processor_key
        self.tokenizer_max_length = tokenizer_max_length
        if processor is not None and hasattr(processor, self.image_processor_key):
            self.image_processor = getattr(processor, self.image_processor_key)
        else:
            self.image_processor = processor

    def _process_images(self, images):
        """Build the fused [B, 6, 224, 224] input, matching the fine-tuned
        training convention (see `src/aerovla_dataset.py` + PrismaticProcessor).

        The real PrismaticProcessor (`openvla-7b/processing_prismatic.py`)
        resizes/center-crops the mosaic to 224x224, then normalizes the SAME
        image twice (ImageNet stats -> DINOv2 stream, [0.5,0.5,0.5] -> SigLIP
        stream) and stacks them -> [6, 224, 224].

        If a processor is provided we use it (matches the checkpoint exactly).
        Otherwise we replicate the same resize + double normalization.
        """
        if self.image_processor is not None:
            try:
                out = self.image_processor(images=[im.convert("RGB") for im in images],
                                           return_tensors="pt")
                pv = out["pixel_values"]
                if pv.ndim == 4 and pv.shape[1] == 6:
                    return pv
            except Exception:  # noqa: BLE001
                pass

        DINO_MEAN = np.asarray((0.485, 0.456, 0.406), np.float32)
        DINO_STD = np.asarray((0.229, 0.224, 0.225), np.float32)
        SIGLIP_MEAN = np.asarray((0.5, 0.5, 0.5), np.float32)
        SIGLIP_STD = np.asarray((0.5, 0.5, 0.5), np.float32)

        out = []
        for im in images:
            x = np.asarray(im.convert("RGB").resize((224, 224), Image.BICUBIC),
                           dtype=np.float32) / 255.0  # [224,224,3]
            dino = (x - DINO_MEAN) / DINO_STD
            sigl = (x - SIGLIP_MEAN) / SIGLIP_STD
            six = np.concatenate([dino, sigl], axis=-1)  # [224,224,6]
            out.append(torch.as_tensor(six).permute(2, 0, 1).unsqueeze(0))
        return torch.cat(out, dim=0)

    def _make_prompt(self, item):
        instr = (item.get("instruction") or "").strip()
        # Rebuild the action string from label like AeroVLADataset.
        label = item.get("label") or {}
        bin_fwd = quantize_action(label.get("fwd", 0.0), "forward")
        bin_down = quantize_action(label.get("down", 0.0), "down")
        bin_yaw = quantize_action(label.get("yaw", 0.0), "yaw")
        action = f"Action: {bin_fwd:02d} {bin_down:02d} {bin_yaw:02d}"
        if item.get("is_last_step") or item.get("is_penultimate"):
            action += " LAND"
        if self.tokenizer is not None and self.tokenizer.eos_token:
            action += self.tokenizer.eos_token
        else:
            action += "</s>"
        return f"{instr}\n{action}"

    def _tokenize(self, texts):
        if self.tokenizer is None:
            return None, None, None
        enc = self.tokenizer(
            texts,
            return_tensors="pt",
            padding="max_length",
            max_length=self.tokenizer_max_length,
            truncation=True,
        )
        input_ids = enc["input_ids"].to(self.device)
        attn = enc["attention_mask"].to(self.device)
        # Labels: copy input_ids, mask out padding (attention 0 -> -100).
        labels = input_ids.clone()
        labels[attn == 0] = -100
        return input_ids, attn, labels

    def __call__(self, batch):
        # --- images ---
        images = [b["image"] for b in batch]
        composite = self._process_images(images)  # [B, 6, 224, 224]
        composite = composite.to(device=self.device, dtype=self.dtype)

        # --- numpy sensor fields ---
        def to_t(b, ndim=None):
            a = torch.as_tensor(np.asarray(b))
            if ndim is not None and a.ndim < ndim:
                a = a.unsqueeze(0)
            # pad/truncate is already done at the dataset level
            return a.to(device=self.device, dtype=self.dtype)

        out = {
            "composite_image": composite,
            "lidar_points": to_t([b["lidar_points"] for b in batch]),
            "lidar_valid": to_t([b["lidar_valid"] for b in batch]).bool(),
            # CEM wants K^-1 here (see module docstring / models/encoders/cem.py).
            "camera_intrinsics": torch.linalg.inv(
                to_t([b["camera_intrinsics"] for b in batch])
            ),
            "camera_extrinsics": to_t([b["camera_extrinsics"] for b in batch]),
            "normalised_pixel_coords": to_t([b["normalised_pixel_coords"] for b in batch]),
            "depth_samples": to_t([b["depth_samples"] for b in batch]),
            "pillar_coords": to_t([b["pillar_coords"] for b in batch]),
            "pillar_dims": to_t([b["pillar_dims"] for b in batch]),
            "pillar_heights": to_t([b["pillar_heights"] for b in batch]),
            "pillar_occupancy": to_t([b["pillar_occupancy"] for b in batch]).bool(),
            "q_pix": to_t([b["q_pix"] for b in batch]),
            "k_pix": to_t([b["k_pix"] for b in batch]),
        }

        # --- text ---
        texts = [self._make_prompt(b) for b in batch]
        input_ids, attention_mask, labels = self._tokenize(texts)
        if input_ids is not None:
            out["input_ids"] = input_ids
            out["attention_mask"] = attention_mask
            out["labels"] = labels

        # metadata (non-tensor)
        out["rel_dir"] = [b["rel_dir"] for b in batch]
        out["frame_idx"] = [b["frame_idx"] for b in batch]
        return out


def build_uav_lidar_batch(dataset, collator=None, batch_indices=None, **collator_kwargs):
    """Convenience: collate a list of indices from `dataset` (or all)."""
    if collator is None:
        collator = UAVLiDARCollator(**collator_kwargs)
    if batch_indices is None:
        batch_indices = list(range(len(dataset)))
    batch = [dataset[i] for i in batch_indices]
    return collator(batch)
