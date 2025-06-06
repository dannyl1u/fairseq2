# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from fairseq2.chatbots import ChatbotHandler
from fairseq2.chatbots.llama import LLaMAChatbotHandler
from fairseq2.chatbots.mistral import MistralChatbotHandler
from fairseq2.context import RuntimeContext


def _register_chatbots(context: RuntimeContext) -> None:
    registry = context.get_registry(ChatbotHandler)

    handler: ChatbotHandler

    # LLaMA
    handler = LLaMAChatbotHandler()

    registry.register(handler.family, handler)

    # Mistral
    handler = MistralChatbotHandler()

    registry.register(handler.family, handler)
