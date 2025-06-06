# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from pathlib import Path

from fairseq2.checkpoint import (
    CheckpointError,
    CheckpointManager,
    FileCheckpointManager,
)
from fairseq2.context import RuntimeContext
from fairseq2.gang import Gangs
from fairseq2.recipes import RecipeError
from fairseq2.utils.io import TorchTensorDumper, TorchTensorLoader
from fairseq2.utils.threading import get_default_thread_pool


def create_checkpoint_manager(
    context: RuntimeContext, gangs: Gangs, output_dir: Path
) -> CheckpointManager:
    checkpoint_dir = output_dir.joinpath("checkpoints")

    file_system = context.file_system

    tensor_loader = TorchTensorLoader(file_system)
    tensor_dumper = TorchTensorDumper(file_system)

    thread_pool = get_default_thread_pool()

    return FileCheckpointManager(
        checkpoint_dir, gangs, file_system, tensor_loader, tensor_dumper, thread_pool
    )


def check_has_checkpoint(checkpoint_manager: CheckpointManager) -> bool:
    try:
        return checkpoint_manager.has_checkpoint(exclude_model_only=True)
    except CheckpointError:
        raise RecipeError(
            "The last training checkpoint cannot be retrieved. See the nested exception for details."  # fmt: skip
        )
