"""Evaluation package for AI Freight Platform (Phase 1.9)."""

from packages.evals.dataset import EvalEmailCase, generate_eval_dataset
from packages.evals.evaluator import EvaluationReport, InboxAgentEvaluator

__all__ = [
    "EvalEmailCase",
    "generate_eval_dataset",
    "InboxAgentEvaluator",
    "EvaluationReport",
]
