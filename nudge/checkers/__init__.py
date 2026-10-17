"""Checker registry.

A checker is a function ``(watch, ctx) -> CheckResult``. Register new ones with
``@checker("name")``; load your own modules via the NUDGE_PLUGINS env var.
"""

from __future__ import annotations

import importlib
from typing import Callable

from nudge.checkers.base import CheckContext, CheckResult, Response

CheckerFn = Callable[..., CheckResult]
REGISTRY: dict[str, CheckerFn] = {}


def checker(name: str) -> Callable[[CheckerFn], CheckerFn]:
    def register(fn: CheckerFn) -> CheckerFn:
        REGISTRY[name] = fn
        return fn

    return register


def get_checker(name: str) -> CheckerFn:
    try:
        return REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown check type {name!r}. Available: {', '.join(sorted(REGISTRY))}") from None


def load_plugins(modules: list[str]) -> None:
    for module in modules:
        importlib.import_module(module)


# Built-in checkers register themselves on import.
from nudge.checkers import builtin  # noqa: E402,F401

__all__ = ["CheckContext", "CheckResult", "Response", "checker", "get_checker", "load_plugins", "REGISTRY"]
