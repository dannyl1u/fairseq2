# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from typing import final

from torch import Tensor
from torch.nn import Module
from torch.nn.parallel import DistributedDataParallel as DDP
from typing_extensions import override

from fairseq2.context import RuntimeContext
from fairseq2.gang import GangError, Gangs
from fairseq2.logging import log
from fairseq2.models import ModelHandler
from fairseq2.nn.data_parallel import (
    DistributedSetupError,
    Fsdp1Module,
    Fsdp2Module,
    FsdpGranularity,
    FsdpWrapper,
    fsdp1_load_local_state_dict,
    fsdp1_local_state_dict,
    fsdp1_summon_full_parameters,
    fsdp2_load_local_state_dict,
    fsdp2_local_state_dict,
    fsdp2_no_sync,
    fsdp2_summon_full_parameters,
    to_ddp,
    to_fsdp1,
    to_fsdp2,
)
from fairseq2.nn.utils.grad import clip_grad_norm
from fairseq2.nn.utils.module import broadcast_module, to_device
from fairseq2.recipes import Model, RecipeError
from fairseq2.recipes.config import TrainerSection
from fairseq2.typing import ContextManager

# isort: split

from fairseq2.recipes.common._error import FsdpNotSupportedError


def setup_data_parallel_model(
    context: RuntimeContext,
    trainer_section: TrainerSection,
    model: Model,
    gangs: Gangs,
    has_checkpoint: bool,
    static_graph: bool = True,
) -> Model:
    data_parallelism = trainer_section.data_parallelism

    if data_parallelism == "fsdp":
        if gangs.rdp.size > 1 and gangs.sdp.size == 1:
            log.warning("Hybrid sharded data parallelism not enabled. Falling back to DDP.")  # fmt: skip

            data_parallelism = "ddp"

    try:
        if data_parallelism == "ddp":
            return _wrap_ddp(model, gangs, static_graph)

        if data_parallelism == "fsdp":
            return _wrap_fsdp(
                trainer_section, model, gangs, has_checkpoint, static_graph
            )
    except DistributedSetupError as ex:
        raise RecipeError(
            "The data parallelism cannot be setup. See the nested exception for details."
        ) from ex

    raise ValueError(
        "`recipe_config.trainer.data_parallelism` must be 'ddp' or 'fsdp'."
    )


def _wrap_ddp(model: Model, gangs: Gangs, static_graph: bool) -> Model:
    if gangs.dp.size == 1:
        to_device(model.module, gangs.root.device)

        return model

    log.info("Wrapping the model with DDP and broadcasting to all processes.")

    # We do not set DDP's `static_graph` parameter. Unfortunately, support for
    # that feature is finicky in DDP. `find_unused_parameters` is still useful
    # though and can have measurable impact on performance.
    ddp_module = to_ddp(model.module, gangs, find_unused_parameters=not static_graph)

    log.info("Model wrapped with DDP and broadcasted.")

    return _DdpModel(ddp_module, model)


@final
class _DdpModel(Model):
    _ddp: DDP
    _wrapped_model: Model

    def __init__(self, ddp: DDP, wrapped_model: Model) -> None:
        self._ddp = ddp
        self._wrapped_model = wrapped_model

    @override
    def state_dict(self) -> dict[str, object]:
        return self._ddp.module.state_dict()  # type: ignore[no-any-return]

    @override
    def load_state_dict(self, state_dict: Mapping[str, object]) -> None:
        self._ddp.module.load_state_dict(state_dict)

    @override
    def no_sync(self) -> ContextManager:
        return self._ddp.no_sync()

    @override
    def clip_grad_norm(self, max_norm: float | None) -> Tensor:
        return clip_grad_norm(self._ddp, max_norm)

    @override
    def summon_full_parameters(self) -> ContextManager:
        return nullcontext()

    @property
    @override
    def module(self) -> Module:
        return self._ddp

    @property
    @override
    def base_module(self) -> Module:
        return self._ddp.module  # type: ignore[no-any-return]

    @property
    @override
    def name(self) -> str:
        return self._wrapped_model.name

    @property
    @override
    def config(self) -> object:
        return self._wrapped_model.config

    @property
    @override
    def handler(self) -> ModelHandler:
        return self._wrapped_model.handler

    @property
    @override
    def newly_initialized(self) -> bool:
        return self._wrapped_model.newly_initialized


def _wrap_fsdp(
    trainer_section: TrainerSection,
    model: Model,
    gangs: Gangs,
    has_checkpoint: bool,
    static_graph: bool,
) -> Model:
    if not model.handler.supports_fsdp:
        raise FsdpNotSupportedError(model.name)

    if gangs.dp.size == 1:
        to_device(model.module, gangs.root.device)

        return model

    if trainer_section.mixed_precision == "static":
        mp_dtype = trainer_section.dtype
    else:
        mp_dtype = None

    def apply_fsdp(
        module: Module, granularity: FsdpGranularity, wrapper: FsdpWrapper
    ) -> None:
        model.handler.apply_fsdp(module, granularity, wrapper)

    if trainer_section.fsdp.version == "v1":
        if has_checkpoint:
            log.info("Wrapping the model with FSDP1.")  # fmt: skip
        else:
            log.info("Wrapping the model with FSDP1 and broadcasting to all processes.")  # fmt: skip

        fsdp1_module = to_fsdp1(
            model.module,
            gangs,
            apply_fsdp,
            mixed_precision_dtype=mp_dtype,
            fp32_reduce=trainer_section.fsdp.fp32_reduce,
            broadcast_state=not has_checkpoint,
            skip_init=has_checkpoint,
            reshard_after_forward=trainer_section.fsdp.reshard_after_forward,
        )

        if has_checkpoint:
            log.info("Model wrapped with FSDP1.")
        else:
            log.info("Model wrapped with FSDP1 and broadcasted.")

        return _Fsdp1Model(fsdp1_module, model)
    else:
        if has_checkpoint:
            log.info("Wrapping the model with FSDP2.")  # fmt: skip
        else:
            log.info("Wrapping the model with FSDP2 and broadcasting to all processes.")  # fmt: skip

        fsdp2_module = to_fsdp2(
            model.module,
            gangs,
            apply_fsdp,
            mixed_precision_dtype=mp_dtype,
            fp32_reduce=trainer_section.fsdp.fp32_reduce,
            broadcast_state=not has_checkpoint,
            skip_init=has_checkpoint,
            reshard_after_forward=trainer_section.fsdp.reshard_after_forward,
        )

        if has_checkpoint:
            log.info("Model wrapped with FSDP2.")
        else:
            log.info("Model wrapped with FSDP2 and broadcasted.")

        return _Fsdp2Model(fsdp2_module, model)


@final
class _Fsdp1Model(Model):
    _fsdp: Fsdp1Module
    _wrapped_model: Model

    def __init__(self, fsdp: Fsdp1Module, wrapped_model: Model) -> None:
        self._fsdp = fsdp
        self._wrapped_model = wrapped_model

    @override
    def state_dict(self) -> dict[str, object]:
        return fsdp1_local_state_dict(self._fsdp)

    @override
    def load_state_dict(self, state_dict: Mapping[str, object]) -> None:
        fsdp1_load_local_state_dict(self._fsdp, state_dict)

    @override
    def no_sync(self) -> ContextManager:
        return self._fsdp.no_sync()

    @override
    def clip_grad_norm(self, max_norm: float | None) -> Tensor:
        return clip_grad_norm(self._fsdp, max_norm)

    @override
    def summon_full_parameters(self) -> ContextManager:
        return fsdp1_summon_full_parameters(self._fsdp)

    @property
    @override
    def module(self) -> Module:
        return self._fsdp

    @property
    @override
    def base_module(self) -> Module:
        return self._fsdp.module

    @property
    @override
    def name(self) -> str:
        return self._wrapped_model.name

    @property
    @override
    def config(self) -> object:
        return self._wrapped_model.config

    @property
    @override
    def handler(self) -> ModelHandler:
        return self._wrapped_model.handler

    @property
    @override
    def newly_initialized(self) -> bool:
        return self._wrapped_model.newly_initialized


@final
class _Fsdp2Model(Model):
    _fsdp: Fsdp2Module
    _wrapped_model: Model

    def __init__(self, fsdp: Fsdp2Module, wrapped_model: Model) -> None:
        self._fsdp = fsdp
        self._wrapped_model = wrapped_model

    @override
    def state_dict(self) -> dict[str, object]:
        return fsdp2_local_state_dict(self._fsdp)

    @override
    def load_state_dict(self, state_dict: Mapping[str, object]) -> None:
        fsdp2_load_local_state_dict(self._fsdp, state_dict)

    @override
    def no_sync(self) -> ContextManager:
        return fsdp2_no_sync(self._fsdp)

    @override
    def clip_grad_norm(self, max_norm: float | None) -> Tensor:
        return clip_grad_norm(self._fsdp, max_norm)

    @override
    def summon_full_parameters(self) -> ContextManager:
        return fsdp2_summon_full_parameters(self._fsdp)

    @property
    @override
    def module(self) -> Module:
        return self._fsdp

    @property
    @override
    def base_module(self) -> Module:
        return self._fsdp

    @property
    @override
    def name(self) -> str:
        return self._wrapped_model.name

    @property
    @override
    def config(self) -> object:
        return self._wrapped_model.config

    @property
    @override
    def handler(self) -> ModelHandler:
        return self._wrapped_model.handler

    @property
    @override
    def newly_initialized(self) -> bool:
        return self._wrapped_model.newly_initialized


def broadcast_model(model: Model, gangs: Gangs) -> None:
    if gangs.dp.size == 1:
        return

    log.info("Broadcasting '{}' model to all processes.", model.name)

    try:
        broadcast_module(model.module, gangs.dp)
    except GangError as ex:
        raise RecipeError(
            f"The '{model.name}' model cannot be broadcasted from rank 0 to the rest of the data parallel gang. See the nested exception for details."
        ) from ex

    log.info("Model broadcasted.")
