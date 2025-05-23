# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import torch
from torch import Tensor
from torcheval.metrics import Mean

from fairseq2.datasets import SequenceBatch
from fairseq2.datasets.preference import PreferenceBatch
from fairseq2.gang import Gang
from fairseq2.models.sequence import SequenceModelOutput
from fairseq2.recipes import SequenceMetricBag


def _gather_lprobs(output: SequenceModelOutput, target: SequenceBatch) -> Tensor:
    assert target.target_mask is not None
    logprobs = torch.log_softmax(output.logits, dim=-1)
    chosen_logps = torch.gather(logprobs, -1, target.seqs.unsqueeze(-1)).squeeze(-1)
    chosen_logps = (chosen_logps * target.target_mask).sum(dim=-1)  # [Batch, 1]

    return chosen_logps


def _gather_lprobs_avg(
    output: SequenceModelOutput, target: SequenceBatch
) -> tuple[Tensor, Tensor]:
    assert target.target_mask is not None
    logprobs = torch.log_softmax(output.logits, dim=-1)
    per_token_logps = torch.gather(logprobs, -1, target.seqs.unsqueeze(-1)).squeeze(-1)
    total_logps = (per_token_logps * target.target_mask).sum(dim=-1)  # [Batch, 1]
    assert target.target_mask is not None
    average_logps = total_logps / target.target_mask.sum(-1)

    return total_logps, average_logps


class POFinetuneMetricBag(SequenceMetricBag):
    chosen_logps: Mean
    rejected_logps: Mean
    chosen_lengths: Mean
    rejected_lengths: Mean

    def __init__(self, gang: Gang) -> None:
        super().__init__(gang)

        device = gang.device

        self.chosen_logps = Mean(device=device)

        self.rejected_logps = Mean(device=device)

        self.chosen_lengths = Mean(device=device)

        self.rejected_lengths = Mean(device=device)

    @torch.inference_mode()
    def update_logps(
        self, batch: PreferenceBatch, chosen_logps: Tensor, rejected_logps: Tensor
    ) -> None:
        """Update the Chosen Sequence Log Probabilities and Rejected Sequence Log Probabilities metrics.

        :param batch:
            The batch processed by the model.
        :param chosen_logps:
            The log probabilities for each sequence in ``batch.chosen``.
        :param rejected_logps:
            The log probabilities for each sequence in ``batch.rejected``.
        """
        self.chosen_logps.update(
            chosen_logps.sum() / batch.chosen.batch_size, weight=batch.chosen.batch_size
        )
        self.rejected_logps.update(
            rejected_logps.sum() / batch.rejected.batch_size,
            weight=batch.rejected.batch_size,
        )

    @torch.inference_mode()
    def update_sequence_lengths(self, batch: PreferenceBatch) -> None:
        """Update the Chosen Sequence Length and Rejected Sequence Length metrics.

        :param batch:
            The batch processed by the model.
        """
        self.chosen_lengths.update(
            Tensor([batch.chosen.num_target_elements / batch.chosen.batch_size]),
            weight=batch.chosen.batch_size,
        )
        self.rejected_lengths.update(
            Tensor([batch.rejected.num_target_elements / batch.rejected.batch_size]),
            weight=batch.rejected.batch_size,
        )
