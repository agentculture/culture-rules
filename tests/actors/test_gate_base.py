"""Round 3 (#2): the gate checks base_sha (which picks the gate policy) against the PR's
base as the App reads it at gate time."""

from __future__ import annotations

from datetime import timedelta

from culture_rules.actors.gate import GatePort
from culture_rules.engine.actorport import InvocationContext
from tests.actors.test_gate import (  # noqa: F401 - fixtures
    PASSING,
    LocalRunner,
    Repo,
    clock,
    gate_yaml,
    store,
)
from tests.engine.run_helpers import T0, FakeActor

DEADLINE = T0 + timedelta(hours=1)


def run_gate(store, repo, tmp_path, clock, lookup, *, run=True):  # noqa: F811
    if run:
        store.put("runs", {"id": "run-1", "inputs": {"repo": "o/r", "number": 7}})
    port = GatePort(
        store, run_as=LocalRunner(), bundle_dir=tmp_path / "b", clock=clock, pr_lookup=lookup
    )
    ctx = InvocationContext("run-1", "fix[0]/gate", "code", "spark2", config={"builtin": "gate"})
    return port.invoke(repo.inputs(), "k", DEADLINE, context=ctx)


def test_the_prs_own_base_passes_and_is_read_as_the_app(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    lookup = FakeActor(default=lambda inp, ctx: {"head_sha": repo.start, "base_sha": repo.base})
    res = run_gate(store, repo, tmp_path, clock, lookup)
    assert res.outcome == "completed" and res.output["verdict"] == "pass"
    ((_, given, ctx, _),) = lookup.calls
    assert given == {"repo": "o/r", "number": 7} and ctx.actor == "github-app"


def test_another_base_is_base_mismatch(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    lookup = FakeActor(default=lambda inp, ctx: {"head_sha": repo.start, "base_sha": "f" * 40})
    res = run_gate(store, repo, tmp_path, clock, lookup)
    assert res.outcome == "failed" and res.error.startswith("base_mismatch")


def test_a_base_that_cannot_be_checked_fails_closed(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    failing = FakeActor().on("fix[0]/gate", ("fail", "http_502", True))
    res = run_gate(store, repo, tmp_path, clock, failing)
    assert res.outcome == "failed" and res.error.startswith("base_unverified")
    res = run_gate(store, repo, tmp_path, clock, FakeActor())  # completes with no base
    assert res.outcome == "failed" and res.error.startswith("base_unverified")
    store.delete("runs", "run-1")
    res = run_gate(store, repo, tmp_path, clock, FakeActor(), run=False)  # no run to read
    assert res.outcome == "failed" and res.error.startswith("base_unverified")


def test_the_production_gate_has_the_app_lookup():
    from culture_rules.node.runner import default_ports
    from culture_rules.store.memory import MemoryStore

    code = default_ports(MemoryStore(), "spark2")["code"]
    gate = code._builtins["gate"]
    assert gate._pr_lookup is not None
