"""Data-access layer — one module per domain in the deck's slide-7 matrix.

Agents never write SQL against tables they don't own; they call functions
here, each tagged with the domain and mode it needs. The runtime opens a data
scope for the running tool contract, and `access()` refuses any call outside
that contract's declared grant — so the matrix is enforced, not just drawn.

Code outside the agent mesh (seed, reporting, tests) opts in explicitly with
`system_scope()`.
"""

import functools
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator


class DataAccessDenied(PermissionError):
    pass


_SYSTEM = object()
_scope: ContextVar[object | None] = ContextVar("neutail_data_scope", default=None)


@contextmanager
def scope(principal: str, domains: dict[str, str]) -> Iterator[None]:
    token = _scope.set((principal, domains))
    try:
        yield
    finally:
        _scope.reset(token)


@contextmanager
def system_scope() -> Iterator[None]:
    token = _scope.set(_SYSTEM)
    try:
        yield
    finally:
        _scope.reset(token)


def require(domain: str, mode: str) -> None:
    current = _scope.get()
    if current is _SYSTEM:
        return
    if current is None:
        raise DataAccessDenied(
            f"{mode} on '{domain}' outside any data scope — call through runtime.invoke() or data.system_scope()"
        )
    principal, domains = current
    granted = domains.get(domain, "")
    if mode not in granted:
        raise DataAccessDenied(f"{principal} has '{granted or '-'}' on '{domain}' for this tool, needs '{mode}'")


def access(domain: str, mode: str) -> Callable:
    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            require(domain, mode)
            return fn(*args, **kwargs)

        wrapper.domain = domain
        wrapper.mode = mode
        return wrapper

    return decorate
