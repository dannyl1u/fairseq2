# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from torch.nn import Module

from fairseq2.data_type import DataType
from fairseq2.gang import Gangs
from fairseq2.typing import ContextManager
from fairseq2.utils.version import torch_greater_or_equal

# isort: split

from fairseq2.nn.data_parallel._common import FSDPApplier, FSDPGranularity
from fairseq2.nn.data_parallel._fsdp1 import FSDP1 as FSDP1
from fairseq2.nn.data_parallel._fsdp1 import (
    fsdp1_load_local_state_dict as fsdp1_load_local_state_dict,
)
from fairseq2.nn.data_parallel._fsdp1 import (
    fsdp1_local_state_dict as fsdp1_local_state_dict,
)
from fairseq2.nn.data_parallel._fsdp1 import (
    fsdp1_summon_full_parameters as fsdp1_summon_full_parameters,
)
from fairseq2.nn.data_parallel._fsdp1 import to_fsdp1 as to_fsdp1

if not TYPE_CHECKING and torch_greater_or_equal(2, 6):
    from fairseq2.nn.data_parallel._fsdp2 import FSDP2 as FSDP2
    from fairseq2.nn.data_parallel._fsdp2 import (
        fsdp2_load_local_state_dict as fsdp2_load_local_state_dict,
    )
    from fairseq2.nn.data_parallel._fsdp2 import (
        fsdp2_local_state_dict as fsdp2_local_state_dict,
    )
    from fairseq2.nn.data_parallel._fsdp2 import fsdp2_no_sync as fsdp2_no_sync
    from fairseq2.nn.data_parallel._fsdp2 import (
        fsdp2_summon_full_parameters as fsdp2_summon_full_parameters,
    )
    from fairseq2.nn.data_parallel._fsdp2 import to_fsdp2 as to_fsdp2
else:
    from fairseq2.nn.data_parallel._fsdp2_compat import FSDP2 as FSDP2
    from fairseq2.nn.data_parallel._fsdp2_compat import (
        fsdp2_load_local_state_dict as fsdp2_load_local_state_dict,
    )
    from fairseq2.nn.data_parallel._fsdp2_compat import (
        fsdp2_local_state_dict as fsdp2_local_state_dict,
    )
    from fairseq2.nn.data_parallel._fsdp2_compat import fsdp2_no_sync as fsdp2_no_sync
    from fairseq2.nn.data_parallel._fsdp2_compat import (
        fsdp2_summon_full_parameters as fsdp2_summon_full_parameters,
    )
    from fairseq2.nn.data_parallel._fsdp2_compat import to_fsdp2 as to_fsdp2


def to_fsdp(
    module: Module,
    gangs: Gangs,
    applier: FSDPApplier,
    *,
    version: Literal["v1", "v2"] = "v1",
    granularity: FSDPGranularity = "layer",
    mixed_precision_dtype: DataType | None = None,
    fp32_reduce: bool = False,
    broadcast_state: bool = False,
    skip_init: bool = False,
    reshard_after_forward: bool = True,
    cpu_offload: bool = False,
) -> Module:
    """Wraps ``module`` with FSDP1 or FSDP2 depending on ``version``.

    :param module: The module to wrap.
    :param gangs: The gangs over which to shard ``module``.
    :param applier: The callable to apply FSDP to ``module``.
    :param version: The version of FSDP to use.
    :param mixed_precision_dtype: If not ``None``, parameters, buffers, and
        gradients will use this data type during forward and backward passes.
        Outside forward and backward passes, the model will be kept in full
        precision.
    :param fp32_reduce: If ``True``, the gradients will be reduced in full
        precision. Only relevant if ``mixed_precision_dtype`` is not ``None``.
    :param broadcast_state: If ``True``, each FSDP module will broadcast its
        parameters and buffers from rank 0 to ensure that they are replicated
        across all processes.
    :param reshard_after_forward: If ``True``, unshards the parameters before
        the forward pass and only reshards them after the backward pass.
    :param cpu_offload: If ``True``, offloads parameters to CPU when unsharded.
    """
    if version == "v1":
        return to_fsdp1(
            module,
            gangs,
            applier,
            granularity=granularity,
            mixed_precision_dtype=mixed_precision_dtype,
            fp32_reduce=fp32_reduce,
            broadcast_state=broadcast_state,
            reshard_after_forward=reshard_after_forward,
            cpu_offload=cpu_offload,
        )

    if version == "v2":
        return to_fsdp2(
            module,
            gangs,
            applier,
            granularity=granularity,
            mixed_precision_dtype=mixed_precision_dtype,
            fp32_reduce=fp32_reduce,
            broadcast_state=broadcast_state,
            reshard_after_forward=reshard_after_forward,
            cpu_offload=cpu_offload,
        )

    raise ValueError("`version` must be 'v1' or 'v2'.")


def fsdp_local_state_dict(module: Module) -> dict[str, object]:
    if isinstance(module, FSDP1):
        return fsdp1_local_state_dict(module)

    if isinstance(module, FSDP2):
        return fsdp2_local_state_dict(module)

    raise _type_error(module)


def fsdp_load_local_state_dict(
    module: Module, state_dict: Mapping[str, object]
) -> None:
    if isinstance(module, FSDP1):
        fsdp1_load_local_state_dict(module, state_dict)

    if isinstance(module, FSDP2):
        fsdp2_load_local_state_dict(module, state_dict)

    raise _type_error(module)


def fsdp_no_sync(module: Module, *, unsafe: bool = False) -> ContextManager:
    if isinstance(module, FSDP1):
        return module.no_sync()

    if isinstance(module, FSDP2):
        return fsdp2_no_sync(module)

    raise _type_error(module)


def fsdp_summon_full_parameters(module: Module) -> ContextManager:
    """
    Unshards the parameters of ``module``.

    In addition to unsharding the parameters, this function allows calling the
    module locally. This is useful for online evaluation.
    """
    if isinstance(module, FSDP1):
        return fsdp1_summon_full_parameters(module)

    if isinstance(module, FSDP2):
        return fsdp2_summon_full_parameters(module)

    raise _type_error(module)


def _type_error(module: Module) -> Exception:
    return TypeError(
        f"`module` must be of type `FSDP1` or `FSDP2`, but is of type `{type(module)}` instead."
    )
