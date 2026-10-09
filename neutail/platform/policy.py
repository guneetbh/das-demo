"""Policy & guardrail plane — default-deny, three checks.

1. Tool ACL: the calling principal must be on the contract's allow-list.
   The principal comes from authentication or from the runtime, never from a
   request body, so the allow-list actually means something.
2. Data-domain matrix (deck slide 7): what each agent may read/write, and by
   which access method. Contracts are checked against it at registration;
   every data-layer call is checked against the running contract's grant.
3. Rate limits per actor at the system boundary, tighter for side effects.
"""

import threading
import time
from collections import defaultdict, deque

from neutail import config


class PolicyDenied(PermissionError):
    def __init__(self, caller: str, tool: str, reason: str):
        self.caller = caller
        self.tool = tool
        self.reason = reason
        super().__init__(f"{caller} cannot call {tool}: {reason}")


class RateLimited(PolicyDenied):
    pass


# agent -> domain -> (mode, method). mode: R | W | RW. method: FS feature serving,
# RAG vector retrieval, API direct lookup, EVT event stream (deck slide 7 legend).
DOMAIN_MATRIX: dict[str, dict[str, tuple[str, str]]] = {
    "persona_agent":   {"crm": ("R", "FS"), "loyalty": ("R", "FS"), "behavioural": ("R", "FS"),
                        "transactional": ("R", "FS")},
    "muse_agent":      {"catalogue": ("R", "RAG"), "inventory": ("R", "FS"), "behavioural": ("RW", "EVT")},
    "tailor_agent":    {"catalogue": ("R", "RAG"), "returns": ("R", "FS"), "transactional": ("R", "FS"),
                        "fit": ("R", "FS"), "behavioural": ("W", "EVT")},
    "care_agent":      {"crm": ("R", "API"), "loyalty": ("R", "API"), "catalogue": ("R", "API"),
                        "inventory": ("R", "API"), "returns": ("R", "API"), "behavioural": ("R", "API"),
                        "transactional": ("R", "API")},
    "concierge_agent": {"crm": ("R", "FS"), "loyalty": ("R", "FS"), "catalogue": ("R", "API"),
                        "inventory": ("RW", "API"), "transactional": ("RW", "API"),
                        "escalations": ("RW", "API")},
    "sentry_agent":    {"crm": ("R", "API"), "transactional": ("R", "FS"), "escalations": ("RW", "API")},
    "tally_agent":     {"loyalty": ("RW", "API"), "transactional": ("R", "API"), "behavioural": ("R", "EVT"),
                        "returns": ("R", "API")},
    "onebasket_agent": {"crm": ("R", "API")},
    "scout_agent":     {"catalogue": ("R", "FS"), "behavioural": ("R", "FS")},
    "pace_agent":      {"catalogue": ("R", "API"), "inventory": ("R", "FS")},
    "grade_agent":     {"catalogue": ("R", "API"), "transactional": ("R", "API"), "returns": ("RW", "EVT")},
    "reporting":       {d: ("R", "API") for d in ("crm", "loyalty", "catalogue", "inventory", "returns",
                                                    "behavioural", "transactional", "escalations", "fit")},
}


def assert_domains_permitted(owner: str, domains: dict[str, str], tool: str) -> None:
    granted = DOMAIN_MATRIX.get(owner)
    if granted is None:
        raise ValueError(f"tool '{tool}': owner '{owner}' has no row in the domain matrix")
    for domain, mode in domains.items():
        if domain not in granted:
            raise ValueError(f"tool '{tool}': {owner} is not granted domain '{domain}'")
        for letter in mode:
            if letter not in granted[domain][0]:
                raise ValueError(f"tool '{tool}': {owner} has '{granted[domain][0]}' on '{domain}', not '{mode}'")


def check_tool_access(caller: str, contract) -> None:
    if caller not in contract.allowed_callers:
        raise PolicyDenied(caller, contract.name, f"not in allowed_callers {sorted(contract.allowed_callers)}")


# --------------------------------------------------------------------------- rate limiting
_windows: dict[tuple[str, bool], deque] = defaultdict(deque)
_lock = threading.Lock()


def check_rate(actor: str, tool: str, side_effect: bool) -> None:
    limit = config.SIDE_EFFECT_RATE_LIMIT_PER_MINUTE if side_effect else config.RATE_LIMIT_PER_MINUTE
    now = time.monotonic()
    key = (actor, side_effect)
    with _lock:
        window = _windows[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= limit:
            raise RateLimited(actor, tool, f"rate limit of {limit}/min exceeded")
        window.append(now)


def reset_rate_limits() -> None:
    with _lock:
        _windows.clear()
