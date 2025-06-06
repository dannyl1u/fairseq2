# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from copy import copy

from fairseq2.models.jepa import JepaEncoderFactory
from fairseq2.models.transformer import (
    IdentityBias,
    MultiheadAttention,
    StandardMultiheadAttention,
    TransformerEncoder,
    TransformerFrontend,
    create_default_sdpa,
)
from fairseq2.nn import IdentityProjection, Linear, Projection

# isort: split

from fairseq2.models.jepa.classifier._config import JepaClassifierConfig
from fairseq2.models.jepa.classifier._model import (
    AttentivePooler,
    CrossAttentionDecoderLayer,
    JepaClassifierModel,
)


def create_jepa_classifier_model(config: JepaClassifierConfig) -> JepaClassifierModel:
    return JepaClassifierFactory(config).create_model()


class JepaClassifierFactory:
    _config: JepaClassifierConfig

    def __init__(self, config: JepaClassifierConfig) -> None:
        self._config = config

    def create_model(self) -> JepaClassifierModel:
        config = self._config

        encoder_frontend = self.create_encoder_frontend()

        encoder = self.create_encoder()

        pooler = self.create_pooler()

        head = self.create_head()

        return JepaClassifierModel(
            config.encoder_config.model_dim, encoder_frontend, encoder, pooler, head
        )

    def create_encoder_frontend(self) -> TransformerFrontend:
        config = self._config

        factory = JepaEncoderFactory(config.encoder_config)

        return factory.create_encoder_frontend()

    def create_encoder(self) -> TransformerEncoder:
        config = self._config

        factory = JepaEncoderFactory(config.encoder_config)

        return factory.create_encoder()

    def create_pooler(self) -> AttentivePooler:
        config = self._config

        if config.pool_depth > 1:
            encoder = self.create_pooler_encoder()
        else:
            encoder = None

        decoder_layer = self.create_pooler_decoder_layer()

        return AttentivePooler(
            config.encoder_config.model_dim,
            decoder_layer,
            encoder,
            num_queries=config.num_queries,
            init_std=config.encoder_config.init_std,
        )

    def create_pooler_encoder(self) -> TransformerEncoder:
        config = self._config

        encoder_config = copy(config.encoder_config)

        encoder_config.num_encoder_layers = config.pool_depth

        encoder_factory = JepaEncoderFactory(encoder_config)

        return encoder_factory.create_encoder()

    def create_pooler_decoder_layer(self) -> CrossAttentionDecoderLayer:
        config = self._config

        encoder_factory = JepaEncoderFactory(config.encoder_config)

        cross_attn_layer_norm = encoder_factory.create_layer_norm()

        cross_attn = self.create_cross_attention()

        ffn_layer_norm = encoder_factory.create_layer_norm()

        ffn = encoder_factory.create_ffn(config.pool_depth)

        return CrossAttentionDecoderLayer(
            cross_attn_layer_norm, cross_attn, ffn_layer_norm, ffn
        )

    def create_cross_attention(self) -> MultiheadAttention:
        encoder_config = self._config.encoder_config

        attn_bias = IdentityBias()

        sdpa = create_default_sdpa(attn_bias, dropout_p=encoder_config.attn_dropout_p)

        output_proj = self.create_cross_attention_output_projection()

        return StandardMultiheadAttention(
            encoder_config.model_dim,
            encoder_config.num_encoder_attn_heads,
            sdpa,
            bias=encoder_config.qkv_bias,
            output_proj=output_proj,
        )

    def create_cross_attention_output_projection(self) -> Projection:
        config = self._config

        model_dim = config.encoder_config.model_dim

        if config.decoder_projection:
            return Linear(model_dim, model_dim, bias=True)

        return IdentityProjection(model_dim)

    def create_head(self) -> Projection:
        config = self._config

        return Linear(config.encoder_config.model_dim, config.num_classes, bias=True)
