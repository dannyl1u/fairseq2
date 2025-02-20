# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from typing import Final

from fairseq2.context import RuntimeContext
from fairseq2.data.text.tokenizers import (
    StandardTextTokenizerHandler,
    TextTokenizerHandler,
)
from fairseq2.data.text.tokenizers.sentencepiece import load_raw_sentencepiece_tokenizer

CHAR_TOKENIZER_FAMILY: Final = "char_tokenizer"


def register_char_tokenizer(context: RuntimeContext) -> None:
    asset_download_manager = context.asset_download_manager

    handler = StandardTextTokenizerHandler(
        CHAR_TOKENIZER_FAMILY, load_raw_sentencepiece_tokenizer, asset_download_manager
    )

    registry = context.get_registry(TextTokenizerHandler)

    registry.register(handler.family, handler)
