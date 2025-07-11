# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from typing import Final

from fairseq2.models.utils.checkpoint import convert_checkpoint

# isort: split

from fairseq2.models.qwen._config import QwenConfig

_QWEN_HG_KEY_MAP: Final = {
    # fmt: off
    r"^model\.layers\.([0-9]+)\.self_attn\.q_proj\.":        r"decoder.layers.\1.self_attn.q_proj.",
    r"^model\.layers\.([0-9]+)\.self_attn\.k_proj\.":        r"decoder.layers.\1.self_attn.k_proj.",
    r"^model\.layers\.([0-9]+)\.self_attn\.v_proj\.":        r"decoder.layers.\1.self_attn.v_proj.",
    r"^model\.layers\.([0-9]+)\.self_attn\.q_norm\.":        r"decoder.layers.\1.self_attn.q_norm.",
    r"^model\.layers\.([0-9]+)\.self_attn\.k_norm\.":        r"decoder.layers.\1.self_attn.k_norm.",
    r"^model\.layers\.([0-9]+)\.self_attn\.o_proj\.":        r"decoder.layers.\1.self_attn.output_proj.",
    r"^model\.layers\.([0-9]+)\.post_attention_layernorm\.": r"decoder.layers.\1.ffn_layer_norm.",
    r"^model\.layers\.([0-9]+)\.mlp\.gate_proj\.":           r"decoder.layers.\1.ffn.gate_proj.",
    r"^model\.layers\.([0-9]+)\.mlp\.down_proj\.":           r"decoder.layers.\1.ffn.output_proj.",
    r"^model\.layers\.([0-9]+)\.mlp\.up_proj\.":             r"decoder.layers.\1.ffn.inner_proj.",
    r"^model\.layers\.([0-9]+)\.input_layernorm\.":          r"decoder.layers.\1.self_attn_layer_norm.",
    r"^model\.norm\.":                                       r"decoder.layer_norm.",
    r"^model\.embed_tokens\.":                               r"decoder_frontend.embed.",
    r"^lm_head\.":                                           r"final_proj.",
    # fmt: on
}


def convert_qwen_checkpoint(
    checkpoint: dict[str, object], config: QwenConfig
) -> dict[str, object]:
    if "model.embed_tokens.weight" in checkpoint:  # Hugging Face
        checkpoint = convert_checkpoint(checkpoint, _QWEN_HG_KEY_MAP)

    if config.tied_embeddings:
        checkpoint["final_proj.weight"] = checkpoint["decoder_frontend.embed.weight"]

    return checkpoint
