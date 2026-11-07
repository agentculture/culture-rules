"""The variable guard on MongoDB (marker ``mongo``; skips without docker): a variable delete
and a rule enable are serialized, so no enabled rule ends up referencing a deleted variable.

Both write the variable's guard document (``variable_guards``) in their transactions; of two
that overlap, the store aborts one with a write conflict and its retry re-reads what the
other committed (Codex review P1 on ``variables delete``).
"""

from __future__ import annotations

import threading

import pytest

from culture_rules.engine.audit import AUDIT_COLLECTION as AUDIT
from culture_rules.engine.audit import AuditLog
from culture_rules.server import service
from culture_rules.server.service import Conflict, Definitions, Invalid, Variables
from culture_rules.store import mongo as mongo_adapter
from culture_rules.store.port import VARIABLE_GUARDS_COLLECTION, VARIABLES_COLLECTION
from tests.server.conftest import rule_body

pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
_mongo_tests = pytest.importorskip("tests.store.test_mongo", reason="Mongo binding missing")

pytestmark = pytest.mark.mongo

NAME = "levels"
WAIT_S = 30


@pytest.fixture
def mongo_store():
    _mongo_tests.get_rig()  # skips when docker / the image is unavailable
    store = _mongo_tests.TestMongoStore().make_store()
    store.ensure_collections("rules", VARIABLES_COLLECTION, VARIABLE_GUARDS_COLLECTION, AUDIT)
    store.put_variable(NAME, ["a"], updated_by="seed")
    store.put("rules", _referrer(enabled=False))
    yield store
    _mongo_tests._release_stores()


def _referrer(*, enabled: bool) -> dict:
    condition = {"op": "in", "value": {"field": "a"}, "items": {"var": NAME}}
    return {"id": "uses", **rule_body("uses", enabled=enabled, condition=condition)}


def _verbs(store):
    audit = AuditLog()
    return Definitions(store, audit), Variables(store, audit)


def test_a_rule_enabled_after_the_pre_check_refuses_the_delete(mongo_store, monkeypatch):
    """The reviewer's interleaving: the delete's out-of-transaction check passes, a rule
    referencing the variable is enabled, then the delete's transaction runs."""
    defs, variables = _verbs(mongo_store)
    real = mongo_store.delete_variable

    def enable_first(*args, **kw):
        defs.set_enabled("rules", "uses", True, "editor")
        return real(*args, **kw)

    monkeypatch.setattr(mongo_store, "delete_variable", enable_first)
    with pytest.raises(Conflict) as exc:
        variables.delete(NAME, dry_run=False, identity="admin")
    assert exc.value.code == "variable_in_use"
    assert mongo_store.get_variable(NAME)["version"] == 1
    assert mongo_store.get("rules", "uses")["enabled"] is True


def test_an_enable_after_the_delete_commits_is_refused(mongo_store):
    defs, variables = _verbs(mongo_store)
    variables.delete(NAME, dry_run=False, identity="admin")
    with pytest.raises(Invalid) as exc:
        defs.set_enabled("rules", "uses", True, "editor")
    assert exc.value.errors[0]["code"] == "variable_undefined"
    assert mongo_store.get("rules", "uses")["enabled"] is False


def test_overlapping_delete_and_enable_exactly_one_commits(mongo_store, monkeypatch):
    """Both transactions open and read before either writes the guard (a barrier just before
    each guard write); then both write it. Whichever the store lets through commits; the
    other's retry waits until the winner is done and is refused on what it committed."""
    defs, variables = _verbs(mongo_store)
    barrier = threading.Barrier(2, timeout=WAIT_S)
    done = {"delete": threading.Event(), "enable": threading.Event()}
    attempts = {"delete": 0, "enable": 0}

    def gated(real, me, other):
        def write(ops, name, *, by, at):
            attempts[me] += 1
            if attempts[me] == 1:
                barrier.wait()  # both transactions hold a snapshot taken before either write
            else:
                done[other].wait(WAIT_S)  # a retry: let the winner finish first
            real(ops, name, by=by, at=at)

        return write

    monkeypatch.setattr(
        mongo_adapter,
        "write_variable_guard",
        gated(mongo_adapter.write_variable_guard, "delete", "enable"),
    )
    monkeypatch.setattr(
        service, "write_variable_guard", gated(service.write_variable_guard, "enable", "delete")
    )
    outcomes: dict[str, str] = {}

    def run(me, fn):
        try:
            fn()
            outcomes[me] = "committed"
        except (Conflict, Invalid) as exc:
            outcomes[me] = exc.code if isinstance(exc, Conflict) else exc.errors[0]["code"]
        finally:
            done[me].set()

    threads = [
        threading.Thread(
            target=run,
            args=("delete", lambda: variables.delete(NAME, dry_run=False, identity="admin")),
        ),
        threading.Thread(
            target=run, args=("enable", lambda: defs.set_enabled("rules", "uses", True, "ed"))
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT_S * 2)
    assert sorted(outcomes) == ["delete", "enable"]
    assert list(outcomes.values()).count("committed") == 1
    deleted = mongo_store.get_variable(NAME) is None
    enabled = mongo_store.get("rules", "uses")["enabled"] is True
    assert deleted != enabled  # never both: no enabled rule references a deleted variable
    if deleted:
        assert outcomes["enable"] == "variable_undefined"
    else:
        assert outcomes["delete"] == "variable_in_use"
