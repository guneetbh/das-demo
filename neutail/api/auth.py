"""Authentication → principal.

Shoppers get a signed session token from POST /auth/login (a demo "pick a
persona" login — there is no password; replace with your IdP). The token binds
the customer_id server-side, so no request body can choose whose data it
touches. Staff and external clients present an API key (X-API-Key) that maps
to a fixed principal and role.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException

from neutail import config
from neutail.platform import audit


@dataclass(frozen=True)
class Principal:
    name: str           # policy identity: storefront | human_reviewer | admin | external_mcp
    role: str           # shopper | reviewer | admin | external
    customer_id: str | None = None

    @property
    def actor(self) -> str:
        return f"shopper:{audit.hash_customer(self.customer_id)}" if self.customer_id else self.name


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(config.TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).digest())


def issue_token(customer_id: str) -> str:
    payload = _b64(json.dumps({"sub": customer_id, "exp": int(time.time()) + config.TOKEN_TTL_SECONDS}).encode())
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str:
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        raise HTTPException(401, "malformed token") from None
    if not hmac.compare_digest(signature, _sign(payload)):
        raise HTTPException(401, "invalid token")
    claims = json.loads(_unb64(payload))
    if claims["exp"] < time.time():
        raise HTTPException(401, "token expired — log in again")
    return claims["sub"]


def shopper(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "log in first: POST /auth/login, then send Authorization: Bearer <token>")
    return Principal("storefront", "shopper", verify_token(authorization.split(" ", 1)[1].strip()))


def staff(x_api_key: str | None = Header(default=None)) -> Principal:
    if not x_api_key or x_api_key not in config.STAFF_KEYS:
        raise HTTPException(401, "a valid X-API-Key is required")
    name, role = config.STAFF_KEYS[x_api_key]
    return Principal(name, role)


def require_role(principal: Principal, *roles: str) -> None:
    if principal.role not in roles:
        raise HTTPException(403, f"{principal.name} ({principal.role}) can't use this endpoint")
