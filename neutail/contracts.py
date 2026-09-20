"""Tool contract registry (§05 — Access & policy as code).

Each agent module registers its own tool(s) here on import, by calling
`register`. A contract is name, the caller allowed to invoke it, and the
Python callable that does the work — the same shape as the JSON example
in the architecture doc's §05.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolContract:
    name: str
    allowed_callers: frozenset[str]
    handler: Callable[..., dict]


_REGISTRY: dict[str, ToolContract] = {}


def register(name: str, allowed_callers: list[str], handler: Callable[..., dict]) -> None:
    if name in _REGISTRY:
        raise ValueError(f"tool '{name}' is already registered")
    _REGISTRY[name] = ToolContract(name, frozenset(allowed_callers), handler)


def get(name: str) -> ToolContract:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise LookupError(f"no such tool: '{name}'") from None


def all_tools() -> list[str]:
    return sorted(_REGISTRY)
