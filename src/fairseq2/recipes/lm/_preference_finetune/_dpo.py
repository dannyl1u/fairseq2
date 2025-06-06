# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, final

import torch
import torch.distributed
from torch import Tensor
from typing_extensions import override

from fairseq2.context import RuntimeContext
from fairseq2.datasets import SequenceBatch
from fairseq2.datasets.preference import PreferenceBatch
from fairseq2.gang import Gangs
from fairseq2.logging import log
from fairseq2.metrics import Mean, MetricBag
from fairseq2.models.clm import CausalLM
from fairseq2.nn.utils.module import freeze_parameters
from fairseq2.recipes import Model, TrainUnit
from fairseq2.recipes.common import setup_reference_model
from fairseq2.recipes.config import ReferenceModelSection
from fairseq2.recipes.metrics import update_nll_loss, update_seq_batch_metrics
from fairseq2.utils.structured import structure
from fairseq2.utils.validation import validate

# isort: split

from fairseq2.recipes.lm._preference_finetune._common import (
    _gather_lprobs_avg,
    update_logps_metrics,
    update_sequence_length_metrics,
)
from fairseq2.recipes.lm._preference_finetune._config import POFinetuneConfig
from fairseq2.recipes.lm._preference_finetune._handler import POFinetuneUnitHandler


@final
class DpoFinetuneUnit(TrainUnit[PreferenceBatch]):
    """Represents the language model DPO-finetuning unit. Paper: https://arxiv.org/abs/2305.18290."""

    _model: Model
    _reference_model: Model | None
    _beta: float
    _nll_scale: float
    _length_normalization: bool

    def __init__(
        self,
        model: Model,
        reference_model: Model | None,
        beta: float = 0.1,
        nll_scale: float = 1.0,
        length_normalization: bool = False,
    ) -> None:
        self._model = model
        self._reference_model = reference_model
        self._beta = beta
        self._nll_scale = nll_scale
        self._length_normalization = length_normalization

    @override
    def __call__(
        self, batch: PreferenceBatch, metric_bag: MetricBag
    ) -> tuple[Tensor, int]:
        chosen_batch = batch.chosen
        chosen_input_batch, chosen_target_batch = chosen_batch.as_auto_regressive()

        rejected_batch = batch.rejected
        rejected_input_batch, rejected_target_batch = (
            rejected_batch.as_auto_regressive()
        )
        if (
            chosen_target_batch.target_mask is None
            or rejected_target_batch.target_mask is None
        ):
            raise RuntimeError("target_mask attributes must exist for DPO loss")

        chosen_seqs, chosen_seqs_layout = chosen_input_batch.as_input()

        nll_loss, chosen_logits = self._model.module(
            chosen_seqs,
            chosen_seqs_layout,
            targets=chosen_target_batch.seqs,
            target_mask=chosen_target_batch.target_mask,
            return_logits=True,
        )

        rejected_seqs, rejected_seqs_layout = rejected_input_batch.as_input()

        rejected_logits = self._model.module(rejected_seqs, rejected_seqs_layout)

        chosen_logps, average_chosen_logps = _gather_lprobs_avg(
            chosen_logits, chosen_target_batch
        )
        rejected_logps, average_rejected_logps = _gather_lprobs_avg(
            rejected_logits, rejected_target_batch
        )

        if self._reference_model is not None:
            chosen_seqs, chosen_seqs_layout = chosen_batch.as_input()
            rejected_seqs, rejected_seqs_layout = rejected_batch.as_input()

            with torch.no_grad():
                # TODO: fix!
                ref_chosen_logits = self._reference_model.module(
                    chosen_seqs, chosen_seqs_layout
                )
                ref_rejected_logits = self._reference_model.module(
                    rejected_seqs, rejected_seqs_layout
                )

                ref_chosen_logps, ref_average_chosen_logps = _gather_lprobs_avg(
                    ref_chosen_logits, chosen_target_batch
                )
                ref_rejected_logps, ref_average_rejected_logps = _gather_lprobs_avg(
                    ref_rejected_logits, rejected_target_batch
                )
        elif (
            batch.reference_score_chosen is not None
            and batch.reference_score_rejected is not None
        ):
            # reference scores must exist in the batch if reference model is None
            ref_chosen_logps = batch.reference_score_chosen
            ref_average_chosen_logps = (
                ref_chosen_logps / chosen_target_batch.target_mask.sum(-1)
            )
            ref_rejected_logps = batch.reference_score_rejected
            ref_average_rejected_logps = (
                ref_rejected_logps / rejected_target_batch.target_mask.sum(-1)
            )
        else:
            raise RuntimeError(
                "Reference model is not initialized and data batch does not provide reference score, but at least one must exist."
            )

        if self._length_normalization:
            _, _, dpo_loss = self._compute_dpo_loss(
                average_chosen_logps,
                ref_average_chosen_logps,
                average_rejected_logps,
                ref_average_rejected_logps,
            )
        else:
            _, _, dpo_loss = self._compute_dpo_loss(
                chosen_logps, ref_chosen_logps, rejected_logps, ref_rejected_logps
            )

        update_dpo_loss(metric_bag, dpo_loss, batch)

        update_nll_loss(metric_bag, nll_loss, chosen_batch.num_target_elements)

        update_sequence_length_metrics(metric_bag, batch)

        update_logps_metrics(metric_bag, batch, chosen_logps, rejected_logps)

        update_seq_batch_metrics(metric_bag, chosen_batch)

        loss = (
            dpo_loss
            + self._nll_scale
            * nll_loss
            * chosen_target_batch.batch_size
            / chosen_target_batch.num_target_elements
        )  # normalization applied locally per-rank

        return loss, chosen_target_batch.batch_size

    def _gather_lprobs(
        self, logits: Tensor, target: SequenceBatch
    ) -> tuple[Tensor, Tensor]:
        assert target.target_mask is not None
        logprobs = torch.log_softmax(logits, dim=-1)
        per_token_logps = torch.gather(logprobs, -1, target.seqs.unsqueeze(-1)).squeeze(
            -1
        )
        total_logps = (per_token_logps * target.target_mask).sum(dim=-1)  # [Batch, 1]
        assert target.target_mask is not None
        average_logps = total_logps / target.target_mask.sum(-1)

        return total_logps, average_logps

    def _compute_dpo_loss(
        self,
        chosen_logps: Tensor,
        ref_chosen_logps: Tensor,
        rejected_logps: Tensor,
        ref_rejected_logps: Tensor,
    ) -> tuple[Tensor, Tensor, Tensor]:
        logp_ratio_chosen = self._beta * (chosen_logps - ref_chosen_logps)
        logp_ratio_rejected = self._beta * (rejected_logps - ref_rejected_logps)
        dpo_loss = -torch.nn.functional.logsigmoid(
            logp_ratio_chosen - logp_ratio_rejected
        )
        return logp_ratio_chosen, logp_ratio_rejected, dpo_loss.sum()

    @property
    @override
    def model(self) -> Model:
        return self._model


@torch.inference_mode()
def update_dpo_loss(
    metric_bag: MetricBag, loss: Tensor, batch: PreferenceBatch
) -> None:
    metric_bag.get(Mean, "dpo_loss").update(
        loss / batch.chosen.batch_size, weight=batch.chosen.batch_size
    )


DPO_FINETUNE_UNIT: Final = "dpo"


@dataclass(kw_only=True)
class DpoFinetuneConfig:
    reference_model: ReferenceModelSection | None = field(
        default_factory=lambda: ReferenceModelSection(name="llama3_1_8b_instruct")
    )
    """
    The reference model. If ``None``, the recipe expects to get reference
    log-probabilities for chosen and rejected targets as float values in the
    data example (fields `reference_score_rejected` and  `reference_score_chosen`).
    """

    # Loss
    beta: float = 0.1
    """The coefficient of regularization towards the reference model."""

    nll_scale: float = 0.0
    """The coefficient of NLL loss added to the DPO loss."""

    length_normalization: bool = False
    """Use length normalized DPO, which uses the average log probability of a sequence as the implicit reward."""


@final
class DpoFinetuneUnitHandler(POFinetuneUnitHandler):
    _context: RuntimeContext

    def __init__(self, context: RuntimeContext) -> None:
        self._context = context

    @override
    def create(
        self, model: Model, gangs: Gangs, recipe_config: POFinetuneConfig
    ) -> TrainUnit[PreferenceBatch]:
        config = structure(recipe_config.criterion.config, DpoFinetuneConfig)

        validate(config)

        if config.reference_model is not None:
            log.info("Setting up DPO with reference model.")

            reference_model = setup_reference_model(
                CausalLM,
                self._context,
                config.reference_model,
                gangs,
                recipe_config.trainer.dtype,
                mp=False,
            )

            freeze_parameters(reference_model.module)

            log.info("DPO setup complete.")
        else:
            reference_model = None

        return DpoFinetuneUnit(
            model,
            reference_model,
            config.beta,
            config.nll_scale,
            config.length_normalization,
        )

    @property
    @override
    def name(self) -> str:
        return DPO_FINETUNE_UNIT

    @property
    @override
    def config_kls(self) -> type[object]:
        return DpoFinetuneConfig
