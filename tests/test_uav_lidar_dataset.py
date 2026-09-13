"""Smoke test for the UAVLiDARDataset + collator pipeline.

Builds a synthetic episode tree matching the real on-disk layout
(eval_results/checkpoints/seen_valset/<Map>/<episode_id>/) and verifies:

  * loading a frame WITH a `lidar` key produces the full sensor tensors,
  * loading a frame WITHOUT lidar (old eval logs) works via the
    require_lidar=False fallback (zero cloud) and raises in strict mode,
  * the collator emits exactly the tensors `AerialVLAModel.forward(...)`
    expects, with correct shapes, and `camera_intrinsics` is K^-1.
"""

import json
import os
import sys
import tempfile

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datasets import UAVLiDARDataset, UAVLiDARCollator, quantize_action


def make_episode_tree(root, episode_id="aaa", with_lidar=True, n_frames=3):
    """Create an episode directory with frontcamera/downcamera + log frames."""
    ep = os.path.join(root, episode_id)
    for cam in ("frontcamera", "downcamera"):
        os.makedirs(os.path.join(ep, cam), exist_ok=True)
    os.makedirs(os.path.join(ep, "log"), exist_ok=True)

    # 256x256 RGB images (matches disk) - save at 256, dataset resizes.
    rng = np.random.default_rng(0)
    for i in range(n_frames):
        for cam in ("frontcamera", "downcamera"):
            arr = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
            Image.fromarray(arr).save(os.path.join(ep, cam, f"{i:06d}.png"))

        # A sparse synthetic cloud (flat AirSim point_cloud format).
        cloud = np.array([[1.0, 1.0, -1.0], [2.0, 3.0, -0.5], [0.5, 4.0, -1.5]],
                         dtype=np.float64)
        sensors = {
            "state": {
                "position": [0.0, 0.0, -10.0],
                "orientation": [0.0, 0.0, 0.0, 1.0],
                "linear_velocity": [0.0, 0.0, 0.0],
                "angular_velocity": [0.0, 0.0, 0.0],
            },
            "imu": {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
        }
        if with_lidar:
            sensors["lidar"] = {
                "point_cloud": cloud.reshape(-1).tolist(),
                "time_stamp": i,
                "pose": {"position": [0, 0, 0],
                         "orientation": [1, 0, 0, 0]},
                "segmentation": [1, 2, 1],
            }
        with open(os.path.join(ep, "log", f"{i:06d}.json"), "w") as f:
            json.dump({"frame": i, "sensors": sensors}, f)
    return ep


def make_samples(data_root, n=2):
    """Synthetic sample index matching data/aerovla_train_dataset.json schema."""
    samples = []
    for i in range(n):
        ep = "aaa" if i == 0 else "bbb"
        samples.append({
            "traj_rel_dir": ep,
            "img_name": "000000.png",
            "instruction": "Fly to the target while avoiding trees.",
            "label": {"fwd": 2.0, "down": -1.0, "yaw": 0.3},
            "is_last_step": False,
            "is_penultimate": False,
        })
    return samples


def test_dataset_lidar_and_collator_shapes():
    with tempfile.TemporaryDirectory() as tmp:
        make_episode_tree(tmp, "aaa", with_lidar=True)
        make_episode_tree(tmp, "bbb", with_lidar=False)

        samples = make_samples(tmp)
        ds = UAVLiDARDataset(samples=samples, data_root=tmp, max_points=20000)
        assert len(ds) == 2

        item = ds[0]
        assert item["lidar_points"].shape == (20000, 4)
        assert item["lidar_valid"].sum() == 3
        assert item["camera_intrinsics"].shape == (3, 3)
        assert item["camera_extrinsics"].shape == (4, 4)
        assert item["normalised_pixel_coords"].shape == (ds.n_vis, 2)
        assert item["depth_samples"].shape == (ds.n_vis, 32)

        # lidar-less sample must load via fallback (empty cloud).
        item_old = ds[1]
        assert item_old["lidar_valid"].sum() == 0

        coll = UAVLiDARCollator(device="cpu", dtype=torch.float32)
        batch = coll([ds[0], ds[1]])
        assert batch["composite_image"].shape == (2, 6, 224, 224)
        assert batch["lidar_points"].shape == (2, 20000, 4)
        assert batch["lidar_valid"].shape == (2, 20000)
        assert batch["camera_intrinsics"].shape == (2, 3, 3)
        assert batch["camera_extrinsics"].shape == (2, 4, 4)
        assert batch["normalised_pixel_coords"].shape == (2, ds.n_vis, 2)
        assert batch["depth_samples"].shape == (2, ds.n_vis, 32)
        assert batch["pillar_coords"].shape == (2, ds.n_lidar, 2)
        assert batch["pillar_dims"].shape == (2, ds.n_lidar, 2)
        assert batch["pillar_heights"].shape == (2, ds.n_lidar)
        assert batch["pillar_occupancy"].shape == (2, ds.n_lidar)
        assert batch["q_pix"].shape == (2, ds.n_vis, 2)
        assert batch["k_pix"].shape == (2, ds.n_lidar, 2)
        print("  collator batch keys:", sorted(batch.keys()))
        print("  composite:", batch["composite_image"].shape,
              "intrinsics:\n", batch["camera_intrinsics"][0])


def test_camera_intrinsics_is_inverse():
    with tempfile.TemporaryDirectory() as tmp:
        make_episode_tree(tmp, "aaa", with_lidar=True)
        ds = UAVLiDARDataset(make_samples(tmp), data_root=tmp, max_points=10000)
        coll = UAVLiDARCollator()
        batch = coll([ds[0]])
        K = ds[0]["camera_intrinsics"]
        Kinv_emitted = batch["camera_intrinsics"][0]
        assert torch.allclose(torch.linalg.inv(torch.as_tensor(K, dtype=torch.float32)),
                              Kinv_emitted, atol=1e-5), "camera_intrinsics must be K^-1"


def test_collator_intrinsics_bf16_inverse():
    """linalg.inv has no bf16 kernel; the collator must invert in fp32 then cast.

    Regression for the H100 training crash
    `RuntimeError: linalg.inv: Low precision dtypes not supported. Got BFloat16`
    (datasets/uav_lidar_dataset.py, `UAVLiDARCollator.__call__`).
    """
    import numpy as np
    with tempfile.TemporaryDirectory() as tmp:
        make_episode_tree(tmp, "aaa", with_lidar=True)
        ds = UAVLiDARDataset(make_samples(tmp), data_root=tmp, max_points=10000)
        coll = UAVLiDARCollator(device="cpu", dtype=torch.bfloat16)
        batch = coll([ds[0]])
        assert batch["camera_intrinsics"].dtype == torch.bfloat16
        # K * K^-1 ~= I in fp32 (bf16 rounding allows ~1e-2 error on the round trip).
        I = torch.eye(3)
        K = torch.as_tensor(np.asarray(ds[0]["camera_intrinsics"]).astype(np.float32))
        Kinv = batch["camera_intrinsics"][0].float()
        assert torch.allclose(K @ Kinv, I, atol=5e-2), "K @ K^-1 must be identity after bf16 cast"


def test_require_lidar_strict():
    with tempfile.TemporaryDirectory() as tmp:
        make_episode_tree(tmp, "aaa", with_lidar=False)
        ds = UAVLiDARDataset(make_samples(tmp), data_root=tmp,
                             require_lidar=True, max_points=1000)
        try:
            ds[0]
            raise AssertionError("expected KeyError when require_lidar=True + missing lidar")
        except KeyError:
            pass


def test_quantize_action_bounds():
    assert 0 <= quantize_action(-100.0, "forward") <= 98
    assert 0 <= quantize_action(100.0, "yaw") <= 98
    assert quantize_action(0.0, "forward") == 0
    assert quantize_action(5.0, "forward") == 98


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
