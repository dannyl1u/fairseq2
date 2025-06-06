# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import torch.nn as nn

from fairseq2.nn import (
    Embedding,
    LayerNorm,
    Linear,
    PositionEncoder,
    Projection,
    SinusoidalPositionEncoder,
    StandardEmbedding,
    StandardLayerNorm,
    TiedProjection,
    init_scaled_embedding,
)

# isort: split

from fairseq2.models.transformer._attention_bias import (
    CausalAttentionBias,
    IdentityBias,
)
from fairseq2.models.transformer._config import TransformerConfig
from fairseq2.models.transformer._decoder import (
    StandardTransformerDecoder,
    TransformerDecoder,
)
from fairseq2.models.transformer._decoder_layer import (
    StandardTransformerDecoderLayer,
    TransformerDecoderLayer,
)
from fairseq2.models.transformer._encoder import (
    StandardTransformerEncoder,
    TransformerEncoder,
)
from fairseq2.models.transformer._encoder_layer import (
    StandardTransformerEncoderLayer,
    TransformerEncoderLayer,
)
from fairseq2.models.transformer._ffn import (
    FeedForwardNetwork,
    StandardFeedForwardNetwork,
)
from fairseq2.models.transformer._frontend import (
    TransformerEmbeddingFrontend,
    TransformerFrontend,
)
from fairseq2.models.transformer._model import TransformerModel
from fairseq2.models.transformer._multihead_attention import (
    MultiheadAttention,
    StandardMultiheadAttention,
)
from fairseq2.models.transformer._norm_order import TransformerNormOrder
from fairseq2.models.transformer._sdpa._default import create_default_sdpa


def create_transformer_model(config: TransformerConfig) -> TransformerModel:
    return TransformerFactory(config).create_model()


class TransformerFactory:
    _config: TransformerConfig

    def __init__(self, config: TransformerConfig) -> None:
        self._config = config

    def create_model(self) -> TransformerModel:
        config = self._config

        embed = self.create_embedding()

        frontend = self.create_frontend(embed)

        encoder = self.create_encoder()

        decoder = self.create_decoder()

        final_proj = self.create_final_projection(embed)

        return TransformerModel(
            config.model_dim,
            frontend,
            encoder,
            frontend,
            decoder,
            final_proj,
            config.pad_idx,
            config.max_seq_len,
            config.max_seq_len,
        )

    def create_embedding(self) -> Embedding:
        config = self._config

        return StandardEmbedding(
            config.vocab_size,
            config.model_dim,
            config.pad_idx,
            init_fn=init_scaled_embedding,
        )

    def create_frontend(self, embed: Embedding) -> TransformerFrontend:
        config = self._config

        pos_encoder = self.create_position_encoder()

        return TransformerEmbeddingFrontend(
            config.model_dim, embed, pos_encoder, dropout_p=config.dropout_p
        )

    def create_position_encoder(self) -> PositionEncoder:
        config = self._config

        return SinusoidalPositionEncoder(
            config.model_dim, config.max_seq_len, _legacy_pad_idx=1
        )

    def create_encoder(self) -> TransformerEncoder:
        config = self._config

        layers = []

        for _ in range(config.num_encoder_layers):
            layer = self.create_encoder_layer()

            layers.append(layer)

        if config.norm_order == TransformerNormOrder.PRE:
            layer_norm = self.create_layer_norm()
        else:
            layer_norm = None

        return StandardTransformerEncoder(layers, layer_norm)

    def create_encoder_layer(self) -> TransformerEncoderLayer:
        config = self._config

        self_attn = self.create_encoder_self_attention()

        self_attn_layer_norm = self.create_layer_norm()

        ffn = self.create_ffn()

        ffn_layer_norm = self.create_layer_norm()

        return StandardTransformerEncoderLayer(
            self_attn,
            self_attn_layer_norm,
            ffn,
            ffn_layer_norm,
            norm_order=config.norm_order,
            dropout_p=config.dropout_p,
        )

    def create_encoder_self_attention(self) -> MultiheadAttention:
        config = self._config

        attn_bias = IdentityBias()

        sdpa = create_default_sdpa(attn_bias)

        num_heads = config.num_encoder_attn_heads

        return StandardMultiheadAttention(config.model_dim, num_heads, sdpa)

    def create_ffn(self) -> FeedForwardNetwork:
        config = self._config

        return StandardFeedForwardNetwork(
            config.model_dim, config.ffn_inner_dim, bias=True
        )

    def create_decoder(self) -> TransformerDecoder:
        config = self._config

        layers = []

        for _ in range(config.num_decoder_layers):
            layer = self.create_decoder_layer()

            layers.append(layer)

        if config.norm_order == TransformerNormOrder.PRE:
            layer_norm = self.create_layer_norm()
        else:
            layer_norm = None

        return StandardTransformerDecoder(layers, layer_norm)

    def create_decoder_layer(self) -> TransformerDecoderLayer:
        config = self._config

        self_attn = self.create_decoder_self_attention()

        self_attn_layer_norm = self.create_layer_norm()

        encoder_decoder_attn = self.create_encoder_decoder_attention()

        encoder_decoder_attn_layer_norm = self.create_layer_norm()

        ffn = self.create_ffn()

        ffn_layer_norm = self.create_layer_norm()

        return StandardTransformerDecoderLayer(
            self_attn,
            self_attn_layer_norm,
            encoder_decoder_attn,
            encoder_decoder_attn_layer_norm,
            ffn,
            ffn_layer_norm,
            norm_order=config.norm_order,
            dropout_p=config.dropout_p,
        )

    def create_decoder_self_attention(self) -> MultiheadAttention:
        config = self._config

        attn_bias = CausalAttentionBias()

        sdpa = create_default_sdpa(attn_bias)

        num_heads = config.num_decoder_attn_heads

        return StandardMultiheadAttention(config.model_dim, num_heads, sdpa)

    def create_encoder_decoder_attention(self) -> MultiheadAttention:
        config = self._config

        attn_bias = IdentityBias()

        sdpa = create_default_sdpa(attn_bias)

        num_heads = config.num_decoder_attn_heads

        return StandardMultiheadAttention(config.model_dim, num_heads, sdpa)

    def create_layer_norm(self) -> LayerNorm:
        config = self._config

        return StandardLayerNorm(config.model_dim, bias=True)

    def create_final_projection(self, embed: Embedding) -> Projection:
        config = self._config

        if isinstance(embed, StandardEmbedding):
            return TiedProjection(embed.weight, bias=None)

        return Linear(
            config.model_dim,
            config.vocab_size,
            bias=False,
            init_fn=init_transformer_final_projection,
        )


def init_transformer_final_projection(proj: Linear) -> None:
    nn.init.normal_(proj.weight, std=proj.input_dim**-0.5)

    if proj.bias is not None:
        nn.init.zeros_(proj.bias)
