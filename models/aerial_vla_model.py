"""
AerialVLAModel -- 3D LiDAR-visual cross-attention fusion wrapper around the
existing Prismatic/OpenVLA model (DINOv2 + SigLIP fused vision backbone,
fine-tuned linear projector, LLaMA-2).

Given a composite image, LiDAR points and per-camera calibration, this module:

  * runs the SigLIP/DINOv2 backbone to get visual tokens,
  * runs the LiDAR encoder to get LiDAR tokens (projected to D_vis),
  * computes CEM PEs (Gamma_im for visual, Gamma_pc for LiDAR),
  * fuses via SoftLiDARVisualCrossAttention (Q = Z_vis + Gamma_im,
    K/V = Z_lidar + Gamma_pc),
  * feeds the fused tokens through the *existing* fine-tuned projector into the
    LLaMA-2 language model with a prompt (following Prismatic's
    <BOS>|<image patches>|<rest of prompt> layout).

The entire ensemble is optional: when `enable_fusion=True` and the needed
sensor inputs are provided, the fused branch is used; otherwise the model
falls back to the standard (no-LiDAR) path, preserving the existing eval
pipeline exactly.
"""

import math

import torch
import torch.nn as nn

from models.encoders.cem import CoordinatesEncodingModule
from models.encoders.lidar_encoder import LiDAREncoder
from models.fusion.cross_attention_fusion import SoftLiDARVisualCrossAttention

IGNORE_INDEX = -100


class AerialVLAModel(nn.Module):
    """LiDAR-visual fusion wrapper for the AerialVLA (Prismatic/OpenVLA) model."""

    def __init__(
        self,
        base_model: nn.Module,
        d_vis: int,
        n_lidar_heads: int = 8,
        fuse_dropout: float = 0.1,
        cem_d_im: int = 128,
        cem_d_pc: int = 64,
        cem_num_depth_samples: int = 32,
        lidar_backbone: str = "pointpillars",
        lidar_d_lidar: int = 64,
        lidar_input_channels: int = 4,
        lidar_grid_res: tuple = (64, 64),
        use_smca_mask: bool = False,
        smca_sigma: float = 10.0,
        enable_fusion: bool = True,
    ) -> None:
        """Args:
            base_model: the existing PrismaticForConditionalGeneration /
                        OpenVLAForActionPrediction instance whose `vision_backbone`
                        and `projector` we reuse.
            d_vis: visual token dim (must equal the fused vision backbone embed
                   dim + pre-projector; i.e. 11776 for dinosiglip-224).
            enable_fusion: master switch; False makes this wrapper a pure pass-
                           through to `base_model`.
        """
        super().__init__()
        # Store `base_model` OUTSIDE the nn.Module child registry. The parent
        # (Prismatic/OpenVLA) also owns *this* module (`fusion_module = self`
        # on the parent), so registering `base_model` as a submodule here would
        # create a reference cycle that makes torch `state_dict()`/`parameters()`
        # recurse infinitely (RecursionError during `from_pretrained`). All uses
        # are plain attribute reads (vision_backbone/projector/language_model),
        # so a non-registered attribute is fully sufficient.
        object.__setattr__(self, "base_model", base_model)
        self.enable_fusion = enable_fusion
        self.d_vis = d_vis

        if enable_fusion:
            self.cem = CoordinatesEncodingModule(
                d_hidden=d_vis,
                d_im=cem_d_im,
                d_pc=cem_d_pc,
                num_depth_samples=cem_num_depth_samples,
                waypoint_dims=3,
            )
            self.lidar_encoder = LiDAREncoder(
                d_vis=d_vis,
                backbone=lidar_backbone,
                d_lidar=lidar_d_lidar,
                input_channels=lidar_input_channels,
                grid_res=lidar_grid_res,
            )
            self.fusion = SoftLiDARVisualCrossAttention(
                d_model=d_vis,
                n_heads=n_lidar_heads,
                dropout=fuse_dropout,
                use_smca_mask=use_smca_mask,
                smca_sigma=smca_sigma,
            )

        # Newly-initialized additive modules default to fp32, while the rest of
        # the loaded model is e.g. bf16 (torch_dtype). device_map only casts
        # *checkpoint-loaded* params, not these fresh ones, so we must align the
        # fusion stack to the base model's dtype here -- otherwise forward gets
        # "mixed dtype" errors or NaNs on GPU (lidar_encoder fp32 weights vs
        # bf16 point input).
        if enable_fusion:
            base_dtype = next(base_model.parameters()).dtype
            for m in (self.cem, self.lidar_encoder, self.fusion):
                m.to(base_dtype)

    def reset_fusion_parameters(self, init_fn=nn.init.kaiming_uniform_) -> None:
        """Deterministically re-initialize the freshly-created fusion modules.

        When the model is (re)loaded with `low_cpu_mem_usage=True` and
        `device_map`, the HF loader moves the *entire* module to the meta
        device first (discarding the proper `__init__` values), then only
        materializes the parameters that appear in the checkpoint. The fusion
        stack (cem / lidar_encoder / fusion) is NOT in the checkpoint, so those
        params come back as *uninitialized raw memory* -- which shows up as NaN
        weights or garbage (e.g. BatchNorm weight ~8e35, LayerNorm weight=NaN)
        and NaNs the whole forward. Call this once after `from_pretrained` to
        restore a deterministic, valid initialization before any use.

        Deleting `init_fn` callables keep a clean signature for `torch.nn.Module`.
        """
        if not self.enable_fusion:
            return
        with torch.no_grad():
            for m in (self.cem, self.lidar_encoder, self.fusion):
                for module in m.modules():
                    # Convolutions / linear: mimic `nn.Conv2d`/`nn.Linear` reset.
                    if isinstance(module, (nn.Conv2d, nn.Linear)):
                        nn.init.kaiming_uniform_(module.weight, a=math.sqrt(5))
                        if module.bias is not None:
                            fan_in, _ = nn.init._calculate_fan_in_and_fan_out(module.weight)
                            bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
                            nn.init.uniform_(module.bias, -bound, bound)
                    # BatchNorm: weight=1, bias=0, running stats re-zeroed.
                    elif isinstance(module, nn.BatchNorm2d):
                        module.reset_parameters()
                    # LayerNorm: weight=1, bias=0.
                    elif isinstance(module, nn.LayerNorm):
                        if module.elementwise_affine:
                            module.weight.data.fill_(1.0)
                            module.bias.data.zero_()

    def encode_visual(
        self,
        composite_image: torch.Tensor,
        lidar_points: torch.Tensor,
        lidar_valid: torch.Tensor,
        camera_intrinsics: torch.Tensor,
        camera_extrinsics: torch.Tensor,
        normalised_pixel_coords: torch.Tensor,
        depth_samples: torch.Tensor,
        pillar_coords: torch.Tensor,
        pillar_dims: torch.Tensor,
        pillar_heights: torch.Tensor,
        q_pix: torch.Tensor = None,
        k_pix: torch.Tensor = None,
    ) -> torch.Tensor:
        """Encode visual + LiDAR and run the cross-attention fusion.

        Returns fused visual tokens [B, N_vis, d_vis] ready for the projector.
        """
        # Visual tokens from the (fused) DINOv2+SigLIP backbone.
        vis_tokens = self.base_model.vision_backbone(composite_image)  # [B, N_vis, d_vis]

        # LiDAR tokens -> D_vis.
        lidar_tokens = self.lidar_encoder(lidar_points, lidar_valid)   # [B, N_lidar, d_vis]

        # CEM PEs.
        pe_im = self.cem.image_pe(
            normalised_pixel_coords, camera_intrinsics, depth_samples, camera_extrinsics
        )
        pe_pc = self.cem.pc_pe(pillar_coords, pillar_dims, pillar_heights)

        # Fuse (Q = Z_vis + Gamma_im ; K/V = Z_lidar + Gamma_pc).
        fused = self.fusion(
            vis_tokens, lidar_tokens, pe_im, pe_pc,
            q_pix=q_pix, k_pix=k_pix,
        )
        return fused

    def forward(
        self,
        composite_image: torch.Tensor,
        input_ids: torch.LongTensor,
        attention_mask: torch.Tensor = None,
        labels: torch.LongTensor = None,
        lidar_points: torch.Tensor = None,
        lidar_valid: torch.Tensor = None,
        camera_intrinsics: torch.Tensor = None,
        camera_extrinsics: torch.Tensor = None,
        normalised_pixel_coords: torch.Tensor = None,
        depth_samples: torch.Tensor = None,
        pillar_coords: torch.Tensor = None,
        pillar_dims: torch.Tensor = None,
        pillar_heights: torch.Tensor = None,
        q_pix: torch.Tensor = None,
        k_pix: torch.Tensor = None,
        use_cache: bool = None,
        output_attentions: bool = None,
        output_hidden_states: bool = None,
        output_projector_features: bool = None,
        return_dict: bool = None,
    ) -> object:
        """Run the fused (or fallback) forward pass.

        When fusion is enabled and all sensor inputs are supplied, uses the
        LiDAR-visual cross-attention path; otherwise delegates to
        `base_model.forward` unchanged.
        """
        if not self.enable_fusion or any(
            v is None
            for v in (lidar_points, camera_intrinsics, camera_extrinsics,
                      normalised_pixel_coords, depth_samples,
                      pillar_coords, pillar_dims, pillar_heights)
        ):
            return self.base_model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                pixel_values=composite_image,
                labels=labels,
                use_cache=use_cache,
                output_attentions=output_attentions,
                output_hidden_states=output_hidden_states,
                output_projector_features=output_projector_features,
                return_dict=return_dict,
            )

        # Fused path: run fusion to get visual tokens, then project + inject.
        fused_tokens = self.encode_visual(
            composite_image, lidar_points, lidar_valid,
            camera_intrinsics, camera_extrinsics,
            normalised_pixel_coords, depth_samples,
            pillar_coords, pillar_dims, pillar_heights,
            q_pix=q_pix, k_pix=k_pix,
        )

        # Reuse the existing fine-tuned projector on the fused tokens.
        projected = self.base_model.projector(fused_tokens)
        patch_attn = None
        if attention_mask is not None:
            patch_attn = torch.full(
                (projected.shape[0], projected.shape[1]), True,
                dtype=attention_mask.dtype, device=attention_mask.device,
            )

        embeds = self.base_model.get_input_embeddings()(input_ids)
        multimodal_embeds = torch.cat(
            [embeds[:, :1, :], projected, embeds[:, 1:, :]], dim=1
        )
        multimodal_mask = None
        multimodal_labels = None
        if attention_mask is not None:
            multimodal_mask = torch.cat(
                [attention_mask[:, :1], patch_attn, attention_mask[:, 1:]], dim=1
            )
        if labels is not None:
            patch_labels = torch.full(
                (projected.shape[0], projected.shape[1]), IGNORE_INDEX,
                dtype=labels.dtype, device=labels.device,
            )
            multimodal_labels = torch.cat(
                [labels[:, :1], patch_labels, labels[:, 1:]], dim=1
            )

        language_model_output = self.base_model.language_model(
            input_ids=None,
            attention_mask=multimodal_mask,
            inputs_embeds=multimodal_embeds,
            labels=multimodal_labels,
            use_cache=use_cache,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
        )

        # Mirror the real Prismatic return type: wrap the LM output with
        # `projector_features` set to our fused (post-projector) token features.
        try:
            from openvla_7b.modeling_prismatic import PrismaticCausalLMOutputWithPast
        except Exception:
            PrismaticCausalLMOutputWithPast = None

        if PrismaticCausalLMOutputWithPast is not None and (
            return_dict if return_dict is not None else True
        ):
            return PrismaticCausalLMOutputWithPast(
                loss=language_model_output.loss,
                logits=language_model_output.logits,
                past_key_values=language_model_output.past_key_values,
                hidden_states=language_model_output.hidden_states,
                attentions=language_model_output.attentions,
                projector_features=projected,
            )

        if output_projector_features and language_model_output is not None:
            try:
                return *language_model_output, projected
            except TypeError:
                pass
        return language_model_output
