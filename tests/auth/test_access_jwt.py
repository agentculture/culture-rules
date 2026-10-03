"""Criterion 1: Cf-Access-Jwt-Assertion is verified (RS256 over the team JWKS, aud, exp)."""

from __future__ import annotations

import json

import pytest

from tests.auth.jwks import AUD, NOW, TEAM, Keypair, b64, claims, jwks, keypair, now, token

from culture_rules.auth.access import (  # noqa: E402  isort: skip
    AccessConfigError,
    AccessListenerConfig,
    AccessVerifier,
    VerificationError,
    verify_rs256,
)


def verifier(fetches: list | None = None, keyset=None, **kw) -> AccessVerifier:
    def fetch():
        if fetches is not None:
            fetches.append(1)
        return keyset if keyset is not None else jwks()

    return AccessVerifier(TEAM, AUD, fetch_jwks=fetch, clock=now, **kw)


def reason(v: AccessVerifier, tok: str) -> str:
    with pytest.raises(VerificationError) as ei:
        v.verify(tok)
    return ei.value.reason


def test_valid_interactive_token_yields_sso_identity():
    ident = verifier().verify(token())
    assert ident.kind == "sso"
    assert ident.email == "alice@example.com"
    assert ident.subject == "user-sub-1"
    assert ident.identity == "alice@example.com"


def test_valid_access_service_token_yields_service_identity():
    body = claims(email=None, common_name="ci.abcdef.access", sub="")
    ident = verifier().verify(token(body))
    assert ident.kind == "service"
    assert ident.identity == "ci.abcdef.access"


def test_audience_may_be_a_single_string():
    assert verifier().verify(token(claims(aud=AUD))).kind == "sso"


def test_forged_signature_is_rejected():
    forger = Keypair(kid="kid-1")  # same kid, different key: a forgery
    assert reason(verifier(), token(pair=forger)) == "bad_signature"


def test_tampered_payload_is_rejected():
    head, _, sig = token().split(".")
    evil = b64(json.dumps(claims(email="root@example.com")).encode())
    assert reason(verifier(), f"{head}.{evil}.{sig}") == "bad_signature"


def test_expired_token_is_rejected():
    assert reason(verifier(), token(claims(exp=NOW - 1))) == "expired"
    assert reason(verifier(), token(claims(exp=NOW))) == "expired"


def test_not_yet_valid_token_is_rejected():
    assert reason(verifier(), token(claims(nbf=NOW + 60))) == "not_yet_valid"


def test_wrong_audience_is_rejected():
    assert reason(verifier(), token(claims(aud=["someone-else"]))) == "bad_audience"


def test_wrong_issuer_is_rejected():
    assert reason(verifier(), token(claims(iss="https://evil.cloudflareaccess.com"))) == (
        "bad_issuer"
    )


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "none", "kid": "kid-1"},
        {"alg": "HS256", "kid": "kid-1"},
        {"alg": "RS256"},  # no kid
    ],
)
def test_algorithm_confusion_and_missing_kid_are_rejected(header):
    assert reason(verifier(), token(header=header)) == "malformed"


@pytest.mark.parametrize("bad", ["", "a.b", "a.b.c.d", "!!.!!.!!", "x.y.z"])
def test_malformed_tokens_are_rejected(bad):
    assert reason(verifier(), bad) == "malformed"


def test_missing_exp_is_malformed():
    assert reason(verifier(), token(claims(exp=None))) == "malformed"


def test_unknown_kid_refetches_once_per_window_then_refuses():
    fetches: list = []
    v = verifier(fetches)
    v.verify(token())
    assert len(fetches) == 1
    other = keypair("kid-2")
    assert reason(v, token(pair=other)) == "unknown_kid"
    assert reason(v, token(pair=other)) == "unknown_kid"
    assert len(fetches) == 2  # one forced refetch inside the window, not one per request


def test_key_rotation_is_picked_up_on_kid_miss():
    rotated = {"value": jwks()}
    v = AccessVerifier(TEAM, AUD, fetch_jwks=lambda: rotated["value"], clock=now)
    v.verify(token())
    rotated["value"] = jwks(keypair("kid-2"))
    assert v.verify(token(pair=keypair("kid-2"))).kind == "sso"


def test_jwks_fetch_failure_refuses_without_raising_other_errors():
    def boom():
        raise OSError("network down")

    v = AccessVerifier(TEAM, AUD, fetch_jwks=boom, clock=now)
    assert reason(v, token()) == "unknown_kid"


def test_jwks_entries_that_are_not_rs256_signing_keys_are_ignored():
    jwk = keypair().jwk()
    keyset = {"keys": [{**jwk, "use": "enc"}, {**jwk, "kty": "EC"}, {**jwk, "alg": "RS512"}]}
    assert reason(verifier(keyset=keyset), token()) == "unknown_kid"


def test_team_domain_is_normalised():
    v = AccessVerifier(f"https://{TEAM}/", AUD, fetch_jwks=jwks, clock=now)
    assert v.team_domain == TEAM
    assert v.jwks_url == f"https://{TEAM}/cdn-cgi/access/certs"
    assert v.verify(token()).kind == "sso"


@pytest.mark.parametrize("scheme", ["https", "http", "HTTPS"])
def test_team_domain_scheme_is_dropped_and_jwks_is_always_https(scheme):
    v = AccessVerifier(f"{scheme}://{TEAM}", AUD, fetch_jwks=jwks, clock=now)
    assert v.team_domain == TEAM
    assert v.jwks_url == f"https://{TEAM}/cdn-cgi/access/certs"


def test_verify_rs256_is_pure_stdlib_pkcs1_v15():
    pair = keypair()
    pub = pair.key.public_key().public_numbers()
    sig = pair.sign(b"hello")
    assert verify_rs256(pub.n, pub.e, b"hello", sig)
    assert not verify_rs256(pub.n, pub.e, b"hellO", sig)
    assert not verify_rs256(pub.n, pub.e, b"hello", sig[:-1])
    assert not verify_rs256(pub.n, pub.e, b"hello", b"\x00" * len(sig))


# --- all-or-nothing configuration (nodes-culture-dev.md) ----------------------------------

ENV = {
    "CULTURE_RULES_ACCESS_LISTEN": "127.0.0.1:8766",
    "CULTURE_RULES_ACCESS_TEAM_DOMAIN": TEAM,
    "CULTURE_RULES_ACCESS_AUD": AUD,
}


def test_access_config_all_three_set():
    cfg = AccessListenerConfig.from_env(ENV)
    assert (cfg.host, cfg.port, cfg.team_domain, cfg.audience) == ("127.0.0.1", 8766, TEAM, AUD)


def test_access_config_all_unset_is_off():
    assert AccessListenerConfig.from_env({}) is None
    assert AccessListenerConfig.from_env({k: "" for k in ENV}) is None


@pytest.mark.parametrize("missing", sorted(ENV))
def test_access_config_partial_tuple_is_refused(missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(AccessConfigError) as ei:
        AccessListenerConfig.from_env(env)
    assert "together" in str(ei.value)


def test_access_config_rejects_bad_listen_address():
    with pytest.raises(AccessConfigError):
        AccessListenerConfig.from_env({**ENV, "CULTURE_RULES_ACCESS_LISTEN": "nope"})


def test_access_module_is_stdlib_only():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "sys.modules['cryptography'] = None\nsys.modules['jwt'] = None\n"
        "sys.modules['fastapi'] = None\n"
        "import culture_rules.auth.access, culture_rules.auth.principal\n"
        "import culture_rules.auth.tokens, culture_rules.auth.resolve, culture_rules.auth.policy\n"
        "print('ok')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok", done.stderr


def test_failing_jwks_fetch_is_negative_cached_for_forged_tokens():
    fetches: list = []

    def boom():
        fetches.append(1)
        raise OSError("network down")

    v = AccessVerifier(TEAM, AUD, fetch_jwks=boom, clock=now)
    for i in range(5):
        assert reason(v, token(pair=keypair(f"forged-{i}"))) == "unknown_kid"
    assert len(fetches) == 1


def test_concurrent_forged_tokens_share_one_failing_fetch():
    import threading

    fetches: list = []
    release = threading.Event()

    def slow_boom():
        fetches.append(1)
        release.wait(2.0)
        raise OSError("network down")

    v = AccessVerifier(TEAM, AUD, fetch_jwks=slow_boom, clock=now)
    reasons: list = []
    tokens = [token(pair=keypair(f"forged-{i}")) for i in range(5)]

    def attempt(tok):
        try:
            v.verify(tok)
        except VerificationError as exc:
            reasons.append(exc.reason)

    threads = [threading.Thread(target=attempt, args=(t,)) for t in tokens]
    for t in threads:
        t.start()
    release.set()
    for t in threads:
        t.join(5.0)
    assert reasons == ["unknown_kid"] * 5
    assert len(fetches) == 1


def test_the_jwks_fetch_runs_outside_the_lock():
    import threading

    started, release = threading.Event(), threading.Event()
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] > 1:  # the forced refetch for an unknown kid blocks
            started.set()
            release.wait(2.0)
        return jwks()

    v = AccessVerifier(TEAM, AUD, fetch_jwks=fetch, clock=now)
    v.verify(token())  # loads kid-1
    worker = threading.Thread(target=lambda: reason(v, token(pair=keypair("kid-9"))))
    worker.start()
    assert started.wait(2.0)
    done = threading.Event()
    checker = threading.Thread(target=lambda: (v.verify(token()), done.set()))
    checker.start()
    try:
        assert done.wait(1.0), "a cached-kid verify blocked behind an in-flight JWKS fetch"
    finally:
        release.set()
        worker.join(5.0)
        checker.join(5.0)
