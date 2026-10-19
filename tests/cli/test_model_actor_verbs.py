"""t9: ``actors enrol-models|references|disable --force`` and ``workflows repoint-actor``.

End to end against ``create_app`` with the t4/t5 routes (gateways and grants injected), plus a
fake old server that answers the plain ``{"detail": "Not Found"}`` of a missing route.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from culture_rules.cli import _api, main  # noqa: E402
from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.client.http import ApiClient  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.server.conftest import ALICE  # noqa: E402
from tests.server.test_model_actors_api import (  # noqa: E402
    SPARK,
    default_gateways,
    make_client,
    seeded,
    set_gateways,
)

TOO_OLD = "server too old: needs culture-rules >= 0.22.0"


class Tap:
    """A transport onto an in-process app (as alice) that records every request."""

    def __init__(self, tc):
        self.tc = tc
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, json.loads(body) if body else None))
        r = self.tc.request(method, url, headers={**headers, **ALICE}, content=body)
        return r.status_code, r.content

    def posts(self, suffix):
        return [b for m, u, b in self.calls if m == "POST" and u.split("?")[0].endswith(suffix)]


def wire_to(monkeypatch, transport):
    monkeypatch.setattr(
        _api,
        "make_client",
        lambda api_url=None: ApiClient("http://127.0.0.1:8765", transport=transport),
    )
    return transport


def run(capsys, *argv):
    rc = main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def jok(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == 0, err
    return json.loads(out)


def jfail(capsys, code, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == code, (out, err)
    return json.loads(err)


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def gw(store, monkeypatch):
    set_gateways(store, {"spark": SPARK})
    client, _ = make_client(store, default_gateways())
    return wire_to(monkeypatch, Tap(client)), client


ENROL = ["actors", "enrol-models", "--gateway", "spark", "--match", "cortex-spark"]


class OldServer:
    """An API from before 0.22.0: the new routes answer the generic FastAPI 404."""

    def __init__(self, real):
        self.real = real
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        path = url.split("?")[0]
        if path.endswith(("/enrol-models", "/references", "/repoint-actor")):
            return 404, b'{"detail":"Not Found"}'
        return self.real(method, url, headers, body)


# --------------------------------------------------------------------------- registry


def test_the_four_verbs_are_registered_with_roles():
    for noun, name, mutating in [
        ("actors", "enrol-models", True),
        ("actors", "references", False),
        ("actors", "disable", True),
        ("workflows", "repoint-actor", True),
    ]:
        v = REGISTRY.get(noun, name)
        assert v is not None and v.mutating is mutating
    assert REGISTRY.get("actors", "disable").tool_name == "actors_disable"
    names = {p.name for p in REGISTRY.get("actors", "disable").params}
    assert names == {"id", "force"}


# --------------------------------------------------------------------------- enrol-models


def test_enrol_models_dry_run_asks_the_server_for_a_plan(gw, store, capsys):
    tap, _ = gw
    out = jok(capsys, *ENROL)
    assert out["dry_run"] is True and out["applied"] is False
    assert [c["id"] for c in out["result"]["changes"]] == ["cortex-spark"]
    assert tap.posts("/enrol-models") == [
        {
            "gateways": ["spark"],
            "match": ["cortex-spark"],
            "pools": [],
            "api_key": "grant:LOBES_GATEWAY_API_KEY",
            "dry_run": True,
        }
    ]
    assert store.find("actors") == []
    rc, text, _ = run(capsys, *ENROL)
    assert rc == 0 and "cortex-spark" in text and "create" in text and "--apply" in text


def test_enrol_models_apply_commits_and_sends_pools_and_key(gw, store, capsys):
    tap, _ = gw
    out = jok(
        capsys,
        *ENROL,
        "--pool",
        "cortex-spark=lane",
        "--api-key",
        "grant:OTHER",
        "--apply",
    )
    assert out["applied"] is True and out["dry_run"] is False
    body = tap.posts("/enrol-models")[0]
    assert body["dry_run"] is False
    assert body["pools"] == ["cortex-spark=lane"]
    assert body["api_key"] == "grant:OTHER"
    assert [a["id"] for a in out["result"]["applied"]] == ["cortex-spark"]
    assert store.get("actors", "cortex-spark") is not None


def test_enrol_models_flags_are_repeatable_and_ordered(gw, capsys):
    tap, _ = gw
    rc, _, _ = run(
        capsys,
        "actors",
        "enrol-models",
        "--gateway",
        "spark2",
        "--gateway",
        "spark",
        "--match",
        "a-*",
        "--match",
        "b-*",
        "--json",
    )
    body = tap.posts("/enrol-models")[0]
    assert body["gateways"] == ["spark2", "spark"]
    assert body["match"] == ["a-*", "b-*"]
    assert rc in (0, 1)  # spark2 is not a configured gateway here: the server's answer, not ours


@pytest.mark.parametrize("pool", ["nope", "=x", "x=", ""])
def test_enrol_models_malformed_pool_is_a_user_error_before_any_request(gw, capsys, pool):
    tap, _ = gw
    err = jfail(capsys, 1, *ENROL, "--pool", pool)
    assert "--pool" in err["message"]
    assert tap.calls == []


def test_enrol_models_refuses_a_literal_key_without_echoing_it(gw, capsys):
    tap, _ = gw
    err = jfail(capsys, 1, *ENROL, "--api-key", "sk-supersecretvalue")
    assert "grant:" in err["message"]
    assert "sk-supersecretvalue" not in json.dumps(err)
    assert tap.calls == []


def test_enrol_models_requires_gateway_and_match(gw, capsys):
    for argv in (["--match", "x"], ["--gateway", "spark"]):
        with pytest.raises(SystemExit) as exc:
            main(["actors", "enrol-models", *argv, "--json"])
        assert exc.value.code == 1
        assert "required" in capsys.readouterr().err
    assert gw[0].calls == []


def test_enrol_models_server_refusal_is_exit_1(gw, capsys):
    err = jfail(capsys, 1, "actors", "enrol-models", "--gateway", "ghost", "--match", "x")
    assert "unknown_gateway" in err["message"]


def test_enrol_models_on_an_old_server_is_exit_2_with_the_version(gw, monkeypatch, capsys):
    tap, _ = gw
    wire_to(monkeypatch, OldServer(tap))
    for extra in ([], ["--apply"]):
        err = jfail(capsys, 2, *ENROL, *extra)
        assert err["message"] == TOO_OLD
        assert "http://127.0.0.1:8765" in err["remediation"]


# --------------------------------------------------------------------------- references


def test_references_lists_what_uses_an_actor(store, monkeypatch, capsys):
    client = seeded(store)
    wire_to(monkeypatch, Tap(client))
    out = jok(capsys, "actors", "references", "old")
    assert out["actor_id"] == "old"
    assert {(i["kind"], i["id"]) for i in out["items"]} == {("workflow", "news")}
    rc, text, _ = run(capsys, "actors", "references", "old")
    assert rc == 0 and "workflow news" in text and "summarise.body[summary]" in text
    rc, text, _ = run(capsys, "actors", "references", "new")
    assert rc == 0 and "no references" in text


def test_references_unknown_actor_stays_a_user_error(store, monkeypatch, capsys):
    client, _ = make_client(store)
    wire_to(monkeypatch, Tap(client))
    err = jfail(capsys, 1, "actors", "references", "nope")
    assert "not_found" in err["message"]


def test_references_on_an_old_server_is_exit_2(store, monkeypatch, capsys):
    client, _ = make_client(store)
    wire_to(monkeypatch, OldServer(Tap(client)))
    assert jfail(capsys, 2, "actors", "references", "x")["message"] == TOO_OLD


# --------------------------------------------------------------------------- disable --force


def test_disable_refusal_lists_the_referrers_and_force_goes_through(store, monkeypatch, capsys):
    client = seeded(store)
    tap = wire_to(monkeypatch, Tap(client))
    out = jok(capsys, "actors", "disable", "old")
    assert out["dry_run"] is True
    assert {i["id"] for i in out["in_use"]} == {"news"}
    assert store.get("actors", "old")["enabled"] is True
    err = jfail(capsys, 1, "actors", "disable", "old", "--apply")
    assert "workflow news" in err["message"] and "actor_in_use" in err["message"]
    assert "--force" in err["remediation"]
    assert store.get("actors", "old")["enabled"] is True
    out = jok(capsys, "actors", "disable", "old", "--force", "--apply")
    assert out["applied"] is True
    assert store.get("actors", "old")["enabled"] is False
    assert any(u.startswith("/actors/old/disable?force=true") for m, u, _ in tap.calls)


def test_disable_without_referrers_keeps_its_positional_id(store, monkeypatch, capsys):
    client = seeded(store)
    wire_to(monkeypatch, Tap(client))
    out = jok(capsys, "actors", "disable", "new", "--apply")
    assert out["applied"] is True
    assert store.get("actors", "new")["enabled"] is False


class FailsReferences:
    """The references read answers ``status`` with the server's own error envelope."""

    def __init__(self, real, status):
        self.real, self.status = real, status

    def __call__(self, method, url, headers, body):
        if url.split("?")[0].endswith("/references"):
            err = {"error": {"code": "internal", "message": "boom", "errors": []}}
            return self.status, json.dumps(err).encode()
        return self.real(method, url, headers, body)


def test_disable_dry_run_on_an_old_server_lists_no_referrers(store, monkeypatch, capsys):
    client = seeded(store)
    wire_to(monkeypatch, OldServer(Tap(client)))
    out = jok(capsys, "actors", "disable", "old")
    assert out["dry_run"] is True and out["in_use"] == []


def test_disable_dry_run_does_not_read_a_failed_lookup_as_not_in_use(store, monkeypatch, capsys):
    client = seeded(store)
    wire_to(monkeypatch, FailsReferences(Tap(client), 500))
    err = jfail(capsys, 1, "actors", "disable", "old")
    assert "boom" in err["message"]


# --------------------------------------------------------------------------- repoint-actor

REPOINT = ["workflows", "repoint-actor", "news", "--from", "old", "--to", "new"]


def test_repoint_dry_run_reads_the_revision_and_asks_for_a_plan(store, monkeypatch, capsys):
    client = seeded(store)
    tap = wire_to(monkeypatch, Tap(client))
    out = jok(capsys, *REPOINT)
    assert out["dry_run"] is True and out["applied"] is False
    assert out["result"]["changed"] == ["summarise.body[summary]"]
    assert tap.posts("/repoint-actor") == [
        {"from": "old", "to": "new", "expected_revision": 1, "force": False, "dry_run": True}
    ]
    assert store.get("workflows", "news")["revision"] == 1
    rc, text, _ = run(capsys, *REPOINT)
    assert rc == 0 and "summarise.body[summary]" in text and "--apply" in text


def test_repoint_apply_writes_and_honours_expected_revision(store, monkeypatch, capsys):
    client = seeded(store)
    tap = wire_to(monkeypatch, Tap(client))
    out = jok(capsys, *REPOINT, "--expected-revision", "1", "--apply")
    assert out["applied"] is True and out["result"]["revision"] == 2
    assert tap.posts("/repoint-actor")[0]["dry_run"] is False
    assert not any(m == "GET" and u == "/workflows/news" for m, u, _ in tap.calls)
    assert store.get("workflows", "news")["revision"] == 2


def test_repoint_stale_revision_is_exit_1(store, monkeypatch, capsys):
    client = seeded(store)
    wire_to(monkeypatch, Tap(client))
    err = jfail(capsys, 1, *REPOINT, "--expected-revision", "0", "--apply")
    assert "revision_conflict" in err["message"]
    assert store.get("workflows", "news")["revision"] == 1


def test_repoint_force_is_sent(store, monkeypatch, capsys):
    client = seeded(store)
    tap = wire_to(monkeypatch, Tap(client))
    jok(capsys, *REPOINT, "--force")
    assert tap.posts("/repoint-actor")[0]["force"] is True


def test_repoint_unknown_workflow_is_a_user_error_not_too_old(store, monkeypatch, capsys):
    client, _ = make_client(store)
    wire_to(monkeypatch, Tap(client))
    err = jfail(capsys, 1, "workflows", "repoint-actor", "ghost", "--from", "a", "--to", "b")
    assert "not_found" in err["message"]


def test_repoint_on_an_old_server_is_exit_2(store, monkeypatch, capsys):
    client = seeded(store)
    wire_to(monkeypatch, OldServer(Tap(client)))
    for extra in ([], ["--apply"]):
        assert jfail(capsys, 2, *REPOINT, *extra)["message"] == TOO_OLD


# --------------------------------------------------------------------------- docs


def test_explain_and_learn_cover_the_new_verbs(capsys):
    from culture_rules.explain import resolve

    for path in (
        ("actors", "enrol-models"),
        ("actors", "references"),
        ("actors", "disable"),
        ("workflows", "repoint-actor"),
    ):
        assert "culture-rules " + " ".join(path) in resolve(path)
    assert "0.22.0" in resolve(("actors", "enrol-models"))
    assert "--force" in resolve(("actors", "disable"))
    rc, out, _ = run(capsys, "learn")
    assert rc == 0
    for name in ("enrol-models", "references", "repoint-actor"):
        assert name in out
