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


def run_gate(store, repo, tmp_path, clock, lookup, *, run=True, run_id="run-1"):  # noqa: F811
    if run and run_id == "run-1":
        store.put("runs", {"id": "run-1", "inputs": {"repo": "o/r", "number": 7}})
    port = GatePort(
        store, run_as=LocalRunner(), bundle_dir=tmp_path / "b", clock=clock, pr_lookup=lookup
    )
    ctx = InvocationContext(run_id, "fix[0]/gate", "code", "spark2", config={"builtin": "gate"})
    return port.invoke(repo.inputs(), "k", DEADLINE, context=ctx)


def dispatched_run(store, tip, *, data_tip=True):  # noqa: F811
    """A pr-fix run started by a genuine queue dispatch event carrying ``tip`` (d37)."""
    from culture_rules.events.emit import QUEUE_SOURCE, derive_envelope
    from culture_rules.events.ingest import event_document
    from culture_rules.node.actions.queue import DISPATCH_TYPE, dispatch_event_id
    from culture_rules.node.firing import run_id_for

    data = {"repository": "o/r", "number": 7, "base_sha": tip}
    if data_tip:
        data["base_tip_sha"] = tip
    envelope = derive_envelope(
        None,
        type=DISPATCH_TYPE,
        source=QUEUE_SOURCE,
        data=data,
        id=dispatch_event_id("pr-fixer", "1-abc"),
        hops=0,
    )
    store.insert("events", event_document(envelope, host="spark2"))
    run_id = run_id_for("pr-fixer-dispatch", envelope["id"])
    store.put(
        "runs",
        {
            "id": run_id,
            "rule_id": "pr-fixer-dispatch",
            "trigger": envelope,
            "inputs": {"repo": "o/r", "number": 7},
        },
    )
    return run_id


def test_the_prs_own_base_passes_and_is_read_as_the_app(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    lookup = FakeActor(default=lambda inp, ctx: {"head_sha": repo.start, "base_sha": repo.base})
    res = run_gate(store, repo, tmp_path, clock, lookup)
    assert res.outcome == "completed"
    assert res.output["verdict"] == "pass"
    ((_, given, ctx, _),) = lookup.calls
    assert given == {"repo": "o/r", "number": 7}
    assert ctx.actor == "github-app"


def test_another_base_is_base_mismatch(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    lookup = FakeActor(default=lambda inp, ctx: {"head_sha": repo.start, "base_sha": "f" * 40})
    res = run_gate(store, repo, tmp_path, clock, lookup)
    assert res.outcome == "failed"
    assert res.error.startswith("base_mismatch")


def lookup_placing(repo, placed):  # noqa: F811
    """The App: the PR's base.sha is an older commit; a ``base_sha`` it is asked to place
    answers ``placed`` (True, False or None)."""

    def answer(inp, ctx):
        out = {"head_sha": repo.start, "base_sha": "a" * 40}
        if "base_sha" in inp:
            out["base_on_branch"] = placed
        return out

    return FakeActor(default=answer)


def test_d37_the_dispatched_tip_on_the_branch_passes(store, tmp_path, clock):  # noqa: F811
    """The fixer's base is the branch's tip the queue dispatched, not GitHub's base.sha
    (the base as of the PR's last push): the App places it on the branch."""
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    run_id = dispatched_run(store, repo.base)
    lookup = lookup_placing(repo, True)
    res = run_gate(store, repo, tmp_path, clock, lookup, run_id=run_id)
    assert res.outcome == "completed"
    assert res.output["verdict"] == "pass"
    ((_, given, _, _),) = lookup.calls
    assert given["base_sha"] == repo.base


def test_d37_a_base_the_queue_did_not_dispatch_is_base_mismatch_even_on_the_branch(
    store, tmp_path, clock  # noqa: F811
):
    """Codex round 1 #2: a commit merely on the branch (e.g. one between base.sha and the
    tip, with a weaker gate) is not the dispatched tip: refused without asking GitHub."""
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    run_id = dispatched_run(store, "c" * 40)  # the queue dispatched another commit
    lookup = lookup_placing(repo, True)
    res = run_gate(store, repo, tmp_path, clock, lookup, run_id=run_id)
    assert res.outcome == "failed"
    assert res.error.startswith("base_mismatch")
    ((_, given, _, _),) = lookup.calls
    assert "base_sha" not in given


def test_d37_a_base_from_request_inputs_alone_is_not_the_dispatched_tip(
    store, tmp_path, clock  # noqa: F811
):
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    run_id = dispatched_run(store, repo.base, data_tip=False)  # no queue-read tip
    res = run_gate(store, repo, tmp_path, clock, lookup_placing(repo, True), run_id=run_id)
    assert res.outcome == "failed"
    assert res.error.startswith("base_mismatch")


def test_d37_a_tip_off_the_branch_now_is_base_mismatch(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    run_id = dispatched_run(store, repo.base)
    res = run_gate(store, repo, tmp_path, clock, lookup_placing(repo, False), run_id=run_id)
    assert res.outcome == "failed"
    assert res.error.startswith("base_mismatch")


def test_d37_a_dispatched_tip_github_cannot_place_is_unjudged_a_retry(
    store, tmp_path, clock  # noqa: F811
):
    """Codex round 1 #4: a transient compare failure is a retry, never the story's end."""
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    run_id = dispatched_run(store, repo.base)
    res = run_gate(store, repo, tmp_path, clock, lookup_placing(repo, None), run_id=run_id)
    assert res.outcome == "completed"
    assert res.output["verdict"] == "unjudged"
    assert res.output["rule"] == "base_unverified"
    assert res.output["commit_sha"] is None


def test_a_base_that_cannot_be_checked_fails_closed(store, tmp_path, clock):  # noqa: F811
    repo = Repo(tmp_path, gate_yaml([PASSING]))
    repo.commit("fix", {"src/app.py": "x = 3\n"})
    failing = FakeActor().on("fix[0]/gate", ("fail", "http_502", True))
    res = run_gate(store, repo, tmp_path, clock, failing)
    # d37: GitHub may answer later - an ``unjudged`` verdict (a retry): still nothing judged
    assert res.outcome == "completed"
    assert (res.output["verdict"], res.output["rule"]) == ("unjudged", "base_unverified")
    assert res.output["commit_sha"] is None
    res = run_gate(store, repo, tmp_path, clock, FakeActor())  # completes with no base
    assert res.outcome == "failed"
    assert res.error.startswith("base_unverified")
    store.delete("runs", "run-1")
    res = run_gate(store, repo, tmp_path, clock, FakeActor(), run=False)  # no run to read
    assert res.outcome == "failed"
    assert res.error.startswith("base_unverified")


def test_the_production_gate_has_the_app_lookup():
    from culture_rules.node.runner import default_ports
    from culture_rules.store.memory import MemoryStore

    code = default_ports(MemoryStore(), "spark2")["code"]
    gate = code._builtins["gate"]
    assert gate._pr_lookup is not None
