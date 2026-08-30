"""Deterministic, authorization-first context compilation helpers for World Engine 4."""

from .authorization import authorize_candidate, resolve_principal
from .models import ContextCandidate, Principal
from .scoring import fixed_point_score

__all__ = [
    "ContextCandidate",
    "Principal",
    "authorize_candidate",
    "resolve_principal",
    "fixed_point_score",
]
