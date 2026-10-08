"""Tool contract registry.

A contract says who owns a tool, who may call it, what its inputs look like,
which data domains it touches (and how), and whether it has side effects.
Registration fails if a contract claims a domain its owning agent is not
granted in the slide-7 matrix (policy.DOMAIN_MATRIX), so the matrix can't be
widened by accident in an agent module.
"""

from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel, ValidationError

from neutail.platform import policy


@dataclass(frozen=True)
class ToolContract:
    name: str
    owner: str
    allowed_callers: frozenset[str]
    handler: Callable[..., dict]
    input_model: type[BaseModel] | None = None
    domains: dict[str, str] = field(default_factory=dict)
    side_effect: bool = False
    customer_scoped: bool = True
    description: str = ""

    def validate(self, args: dict) -> dict:
        if self.input_model is None:
            if args:
                raise ValueError(f"{self.name} takes no arguments, got {sorted(args)}")
            return {}
        try:
            return self.input_model(**args).model_dump()
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in exc.errors()
            )
            raise ValueError(f"invalid input for {self.name}: {problems}") from None


_REGISTRY: dict[str, ToolContract] = {}


def register(
    name: str,
    *,
    owner: str,
    allowed_callers: list[str],
    handler: Callable[..., dict],
    input_model: type[BaseModel] | None = None,
    domains: dict[str, str] | None = None,
    side_effect: bool = False,
    customer_scoped: bool = True,
    description: str = "",
) -> ToolContract:
    if name in _REGISTRY:
        raise ValueError(f"tool '{name}' is already registered")
    domains = domains or {}
    policy.assert_domains_permitted(owner, domains, tool=name)
    contract = ToolContract(
        name=name, owner=owner, allowed_callers=frozenset(allowed_callers), handler=handler,
        input_model=input_model, domains=domains, side_effect=side_effect,
        customer_scoped=customer_scoped, description=description or (handler.__doc__ or "").strip(),
    )
    _REGISTRY[name] = contract
    return contract


def get(name: str) -> ToolContract:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise LookupError(f"no such tool: '{name}'") from None


def all_contracts() -> list[ToolContract]:
    return [_REGISTRY[n] for n in sorted(_REGISTRY)]


def all_tools() -> list[str]:
    return sorted(_REGISTRY)
