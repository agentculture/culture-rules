"""Test helpers: an RSA keypair, a fake team JWKS and signed Access JWTs (no network)."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

TEAM = "agentculture.cloudflareaccess.com"
AUD = "aud-tag-0123456789abcdef"
KID = "kid-1"
NOW = 1_800_000_000


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int(value: int) -> str:
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


class Keypair:
    def __init__(self, kid: str = KID) -> None:
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self) -> dict[str, str]:
        pub = self.key.public_key().public_numbers()
        return {"kid": self.kid, "kty": "RSA", "alg": "RS256", "use": "sig",
                "n": _int(pub.n), "e": _int(pub.e)}  # fmt: skip

    def sign(self, data: bytes) -> bytes:
        return self.key.sign(data, padding.PKCS1v15(), hashes.SHA256())


_KEYS: dict[str, Keypair] = {}


def keypair(kid: str = KID) -> Keypair:
    """One keypair per kid per process (2048-bit generation is not free)."""
    if kid not in _KEYS:
        _KEYS[kid] = Keypair(kid)
    return _KEYS[kid]


def jwks(*pairs: Keypair) -> dict[str, Any]:
    return {"keys": [p.jwk() for p in (pairs or (keypair(),))]}


def claims(**changes: Any) -> dict[str, Any]:
    base = {
        "aud": [AUD],
        "iss": f"https://{TEAM}",
        "sub": "user-sub-1",
        "email": "alice@example.com",
        "iat": NOW - 10,
        "nbf": NOW - 10,
        "exp": NOW + 600,
        "type": "app",
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


def token(
    body: dict[str, Any] | None = None,
    *,
    pair: Keypair | None = None,
    header: dict[str, Any] | None = None,
) -> str:
    pair = pair or keypair()
    head = header if header is not None else {"alg": "RS256", "kid": pair.kid, "typ": "JWT"}
    signing_input = f"{b64(json.dumps(head).encode())}.{b64(json.dumps(body or claims()).encode())}"
    return f"{signing_input}.{b64(pair.sign(signing_input.encode()))}"


def now() -> float:
    return float(NOW)


def wall_clock_token(**changes: Any) -> str:
    """A token valid against the real clock (for HTTP tests that use the default clock)."""
    t = int(time.time())
    return token(claims(iat=t - 10, nbf=t - 10, exp=t + 600, **changes))
