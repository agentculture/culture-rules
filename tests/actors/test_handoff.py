"""#39 t3: the Codex hand-off (``culture_rules.actors.handoff``).

``handoff.export`` packs ``start_sha..head_after`` out of the Codex fixer's worktree (on
spark) into the store; ``handoff.import`` rebuilds a fixer-owned worktree from it (on
spark2) for the unchanged gate. Real git in tmp dirs; the run-as seam is the gate tests'
:class:`LocalRunner`.

Acceptance criteria:

1. export packs, caps and stores; import fetches the base, indexes with ``--fix-thin`` and
   checks out exactly ``head_after`` -
   ``test_round_trip_imported_head_is_the_fixers_commit``,
   ``test_export_runs_git_only_through_the_run_as``;
2. the gate judges the same SHA the fixer made -
   ``test_the_gate_judges_the_commit_the_fixer_made``; over the cap -
   ``test_a_pack_over_the_cap_is_handoff_too_large``; a corrupt pack fails closed -
   ``test_a_corrupt_pack_fails_closed_and_leaves_no_worktree``,
   ``test_a_pack_whose_sha256_differs_is_refused_before_anything_runs``.
"""

from __future__ import annotations

import base64
import hashlib
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from culture_rules.actors.gate import PASS, GatePort
from culture_rules.actors.handoff import (
    EXPORT_BUILTIN,
    HANDOFFS_COLLECTION,
    IMPORT_BUILTIN,
    HandoffExportPort,
    HandoffImportPort,
    handoff_id,
    handoff_run_as_from_env,
)
from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.runner import default_ports
from culture_rules.store.memory import MemoryStore
from tests.actors.test_gate import (  # noqa: F401 - fixtures
    GIT_ENV,
    PASSING,
    LocalRunner,
    Repo,
    clock,
    gate_yaml,
    git,
    store,
)
from tests.engine.run_helpers import T0

DEADLINE = T0 + timedelta(days=30)
EXPORT_CTX = InvocationContext(
    "run-1", "fix[1]/export", "code", "spark", config={"builtin": EXPORT_BUILTIN}
)
IMPORT_CTX = InvocationContext(
    "run-1", "fix[1]/import", "code", "spark2", config={"builtin": IMPORT_BUILTIN}
)
WORKSPACE_GLOB = "culture_rules_handoff.*"
BIG = "".join(f"line {i}: {hashlib.sha256(str(i).encode()).hexdigest()}\n" for i in range(800))


class World:
    """The upstream repo (base + PR head), the Codex fixer's clone and the fixer's tmp."""

    def __init__(self, tmp_path: Path, clock) -> None:  # noqa: F811
        self.clock = clock
        (tmp_path / "up").mkdir()
        self.up = Repo(tmp_path / "up", gate_yaml([PASSING]))
        # a PR head holding a large file: a fix to it packs as a delta against the start
        self.up.start = self.up.commit("data", {"data.txt": BIG})
        self.codex = tmp_path / "codex"
        git(tmp_path, "clone", "-q", str(self.up.wt), str(self.codex))
        self.fixer_tmp = tmp_path / "fixer-tmp"
        self.fixer_tmp.mkdir()
        self.runner = LocalRunner(
            env={**GIT_ENV, "HOME": str(tmp_path), "TMPDIR": str(self.fixer_tmp)}
        )

    @property
    def url(self) -> str:
        return f"file://{self.up.wt}"

    def fix(self, text: str = "x = 3\n") -> str:
        (self.codex / "src" / "app.py").write_text(text)
        git(self.codex, "commit", "-q", "-am", "codex fix")
        return git(self.codex, "rev-parse", "HEAD")

    def workspaces(self) -> list[Path]:
        return sorted(self.fixer_tmp.glob(WORKSPACE_GLOB))


@pytest.fixture
def world(tmp_path, clock) -> World:  # noqa: F811
    return World(tmp_path, clock)


def export(store, world, head, *, ctx=EXPORT_CTX, start=None):  # noqa: F811
    port = HandoffExportPort(store, run_as=world.runner, clock=world.clock)
    inputs = {
        "worktree": str(world.codex),
        "start_sha": start or world.up.start,
        "head_after": head,
    }
    return port.invoke(inputs, "k", DEADLINE, context=ctx)


def import_(store, world, out, head, *, url=None, url_ok=None, key="k"):  # noqa: F811
    port = HandoffImportPort(
        store,
        run_as=world.runner,
        clock=world.clock,
        clone_url_ok=url_ok or (lambda _url: True),
    )
    inputs = {
        "handoff_id": out["handoff_id"],
        "start_sha": world.up.start,
        "base_sha": world.up.base,
        "head_after": head,
        "clone_url": url or world.url,
    }
    return port.invoke(inputs, key, DEADLINE, context=IMPORT_CTX)


def round_trip(store, world, head):  # noqa: F811
    exported = export(store, world, head)
    assert exported.outcome == "completed", exported.error
    imported = import_(store, world, exported.output, head)
    assert imported.outcome == "completed", imported.error
    return dict(imported.output)


# ------------------------------------------------------------------------ the round trip


def test_round_trip_imported_head_is_the_fixers_commit(store, world):  # noqa: F811
    head = world.fix()
    out = round_trip(store, world, head)
    assert git(Path(out["worktree"]), "rev-parse", "HEAD") == head
    assert out["commit_sha"] == head
    assert out["head_after"] == head
    assert out["start_sha"] == world.up.start
    assert out["head_before"] == world.up.start
    assert Path(out["worktree"]).parent.parent == world.fixer_tmp
    assert git(Path(out["worktree"]), "status", "--porcelain") == ""


def test_the_import_keeps_the_pack_with_a_receipt(store, world):  # noqa: F811
    """Codex round 1 #2: the pack outlives a successful import (it expires a day after
    it), with a receipt naming the invocation and the worktree it built."""
    head = world.fix()
    out = round_trip(store, world, head)
    (doc,) = store.find(HANDOFFS_COLLECTION)
    assert doc["imported"]["idempotency_key"] == "k"
    assert doc["imported"]["worktree"] == out["worktree"]
    assert doc["pack"]


# ----------------------------------------------- a lost acknowledgement (Codex round 1 #2)


def imported_once(store, world):  # noqa: F811
    """An export and a successful import whose result the engine then lost."""
    head = world.fix()
    exported = export(store, world, head).output
    first = import_(store, world, exported, head)
    assert first.outcome == "completed", first.error
    return head, exported, dict(first.output)


def test_a_replay_after_a_lost_acknowledgement_succeeds_again(store, world):  # noqa: F811
    head, exported, first = imported_once(store, world)
    again = import_(store, world, exported, head)
    assert again.outcome == "completed", again.error
    assert dict(again.output) == first


def test_a_replay_reuses_the_intact_worktree(store, world):  # noqa: F811
    head, exported, first = imported_once(store, world)
    again = import_(store, world, exported, head)
    assert again.outcome == "completed", again.error
    assert world.workspaces() == [Path(first["worktree"]).parent]


def test_a_replay_whose_worktree_is_gone_rebuilds_it_from_the_pack(store, world):  # noqa: F811
    head, exported, first = imported_once(store, world)
    shutil.rmtree(Path(first["worktree"]).parent)
    again = import_(store, world, exported, head)
    assert again.outcome == "completed", again.error
    assert git(Path(again.output["worktree"]), "rev-parse", "HEAD") == head


def test_a_replay_whose_worktree_changed_rebuilds_a_clean_one(store, world):  # noqa: F811
    head, exported, first = imported_once(store, world)
    (Path(first["worktree"]) / "stray.txt").write_text("not the fixer's\n")
    again = import_(store, world, exported, head)
    assert again.output["worktree"] != first["worktree"]
    assert git(Path(again.output["worktree"]), "status", "--porcelain") == ""


def test_a_rebuilt_worktree_is_the_receipts(store, world):  # noqa: F811
    head, exported, first = imported_once(store, world)
    shutil.rmtree(Path(first["worktree"]).parent)
    again = import_(store, world, exported, head)
    doc = store.get(HANDOFFS_COLLECTION, exported["handoff_id"])
    assert doc["imported"]["worktree"] == again.output["worktree"]


def test_another_invocation_after_an_import_is_refused(store, world):  # noqa: F811
    head, exported, _first = imported_once(store, world)
    res = import_(store, world, exported, head, key="another-step")
    assert res.outcome == "failed"
    assert res.error.startswith("handoff_already_imported")


def test_the_export_stores_a_verifiable_capped_pack(store, world):  # noqa: F811
    head = world.fix()
    res = export(store, world, head)
    assert res.output["handoff_id"] == handoff_id("run-1", "fix[1]/export", head)
    doc = store.get(HANDOFFS_COLLECTION, res.output["handoff_id"])
    pack = base64.b64decode(doc["pack"])
    assert pack.startswith(b"PACK")
    assert doc["size"] == len(pack)
    assert doc["sha256"] == hashlib.sha256(pack).hexdigest()
    assert doc["start_sha"] == world.up.start
    assert doc["head_after"] == head


def test_the_gate_judges_the_commit_the_fixer_made(store, world, tmp_path, clock):  # noqa: F811
    head = world.fix()
    out = round_trip(store, world, head)
    gate = GatePort(store, run_as=LocalRunner(), bundle_dir=tmp_path / "bundles", clock=clock)
    ctx = InvocationContext("run-1", "fix[1]/gate", "code", "spark2", config={"builtin": "gate"})
    inputs = {
        "worktree": out["worktree"],
        "base_sha": world.up.base,
        "start_sha": out["start_sha"],
        "commit_sha": out["commit_sha"],
    }
    res = gate.invoke(inputs, "k", DEADLINE, context=ctx)
    assert res.outcome == "completed", res.error
    assert res.output["verdict"] == PASS
    assert res.output["agent_commit_sha"] == head
    assert "x = 3" in res.output["diff"]


def test_a_thin_pack_is_completed_from_the_fetched_start(store, world):  # noqa: F811
    """The pack leaves out what the start has (a delta base); ``--fix-thin`` restores it."""
    (world.codex / "data.txt").write_text(BIG.replace("line 400:", "line 400 (fixed):"))
    git(world.codex, "commit", "-q", "-am", "codex fix")
    head = git(world.codex, "rev-parse", "HEAD")
    size = export(store, world, head).output["size"]
    assert size < len(BIG) // 20
    store.delete(HANDOFFS_COLLECTION, handoff_id("run-1", "fix[1]/export", head))
    out = round_trip(store, world, head)
    text = (Path(out["worktree"]) / "data.txt").read_text()
    assert "line 400 (fixed):" in text


def test_no_agent_commit_round_trips_to_the_start(store, world):  # noqa: F811
    out = round_trip(store, world, world.up.start)
    assert git(Path(out["worktree"]), "rev-parse", "HEAD") == world.up.start


def test_export_runs_git_only_through_the_run_as(store, world):  # noqa: F811
    head = world.fix()
    export(store, world, head)
    assert world.runner.calls
    assert {call[0] for call in world.runner.calls} == {"env"}
    assert {call[1] for call in world.runner.calls} == {"GIT_CONFIG_GLOBAL=/dev/null"}
    assert set(world.runner.cwds) == {str(world.codex)}


# ------------------------------------------------------------------------ the refusals


def test_a_pack_over_the_cap_is_handoff_too_large(store, world):  # noqa: F811
    head = world.fix()
    ctx = InvocationContext(
        "run-1",
        "fix[1]/export",
        "code",
        "spark",
        config={"builtin": EXPORT_BUILTIN, "max_bytes": 40},
    )
    res = export(store, world, head, ctx=ctx)
    assert res.outcome == "failed"
    assert res.error.startswith("handoff_too_large")
    assert not res.retryable
    assert store.find(HANDOFFS_COLLECTION) == []


@pytest.mark.parametrize("cap", [0, True, "8", 11 * 1024 * 1024])
def test_a_bad_cap_is_bad_config(store, world, cap):  # noqa: F811
    ctx = InvocationContext("run-1", "e", "code", "spark", config={"max_bytes": cap})
    res = export(store, world, world.fix(), ctx=ctx)
    assert res.error.startswith("bad_config")


def _tamper(store, doc_id: str, *, rehash: bool) -> None:  # noqa: F811
    doc = dict(store.get(HANDOFFS_COLLECTION, doc_id))
    pack = bytearray(base64.b64decode(doc["pack"]))
    pack[len(pack) // 2] ^= 0xFF
    doc["pack"] = base64.b64encode(bytes(pack)).decode("ascii")
    if rehash:
        doc["sha256"] = hashlib.sha256(bytes(pack)).hexdigest()
    store.put(HANDOFFS_COLLECTION, doc)


def test_a_corrupt_pack_fails_closed_and_leaves_no_worktree(store, world):  # noqa: F811
    """The stored hash matches the corrupt bytes, so ``index-pack --strict`` catches it."""
    head = world.fix("x = 3\n" + "y = 1\n" * 200)
    out = export(store, world, head).output
    _tamper(store, out["handoff_id"], rehash=True)
    res = import_(store, world, out, head)
    assert res.outcome == "failed"
    assert res.error.startswith("handoff_corrupt")
    assert "worktree" not in res.output
    assert world.workspaces() == []


def test_a_pack_whose_sha256_differs_is_refused_before_anything_runs(store, world):  # noqa: F811
    head = world.fix()
    out = export(store, world, head).output
    world.runner.calls.clear()
    _tamper(store, out["handoff_id"], rehash=False)
    res = import_(store, world, out, head)
    assert res.error.startswith("handoff_corrupt")
    assert world.runner.calls == []


def test_a_pack_without_head_after_is_corrupt(store, world):  # noqa: F811
    """A pack of other commits (a valid pack, the right hash) never yields a worktree."""
    head = world.fix()
    out = export(store, world, head).output
    other = world.fix("x = 4\n")
    doc = dict(store.get(HANDOFFS_COLLECTION, out["handoff_id"]))
    doc["head_after"] = other
    store.put(HANDOFFS_COLLECTION, doc)
    res = import_(store, world, out, other)
    assert res.error.startswith("handoff_corrupt")
    assert world.workspaces() == []


def test_a_handoff_naming_other_commits_is_a_mismatch(store, world):  # noqa: F811
    head = world.fix()
    out = export(store, world, head).output
    res = import_(store, world, out, world.up.base)
    assert res.error.startswith("handoff_mismatch")


def test_a_missing_handoff_fails(store, world):  # noqa: F811
    head = world.fix()
    res = import_(store, world, {"handoff_id": "hof_" + "0" * 32}, head)
    assert res.error.startswith("handoff_missing")


def test_a_head_after_not_descending_from_start_is_refused(store, world):  # noqa: F811
    git(world.codex, "reset", "-q", "--hard", world.up.base)
    head = world.fix("x = 9\n")  # on the base, beside the PR head: history rewritten
    res = export(store, world, head)
    assert res.outcome == "failed"
    assert res.error.startswith("handoff_not_descendant")
    assert store.find(HANDOFFS_COLLECTION) == []


def test_a_worktree_whose_head_is_not_head_after_is_refused(store, world):  # noqa: F811
    head = world.fix()
    world.fix("x = 5\n")  # the worktree moved on past head_after
    res = export(store, world, head)
    assert res.error.startswith("handoff_head_mismatch")


def test_a_start_the_worktree_lacks_is_source_unavailable(store, world):  # noqa: F811
    head = world.fix()
    res = export(store, world, head, start="e" * 40)
    assert res.error.startswith("handoff_source_unavailable")


def test_an_unfetchable_base_is_retryable(store, world, tmp_path):  # noqa: F811
    head = world.fix()
    out = export(store, world, head).output
    res = import_(store, world, out, head, url=f"file://{tmp_path / 'nowhere'}")
    assert res.error.startswith("handoff_fetch_failed")
    assert res.retryable
    assert world.workspaces() == []
    assert store.get(HANDOFFS_COLLECTION, out["handoff_id"]) is not None


@pytest.mark.parametrize(
    "url",
    ["ext::sh -c touch% /tmp/pwned", "file:///etc", "https://example.com/o/r.git"],
)
def test_the_default_clone_url_check_admits_only_github_https(store, world, url):  # noqa: F811
    head = world.fix()
    out = export(store, world, head).output
    port = HandoffImportPort(store, run_as=world.runner, clock=world.clock)
    inputs = {
        "handoff_id": out["handoff_id"],
        "start_sha": world.up.start,
        "head_after": head,
        "clone_url": url,
    }
    world.runner.calls.clear()
    res = port.invoke(inputs, "k", DEADLINE, context=IMPORT_CTX)
    assert res.error.startswith("bad_input")
    assert world.runner.calls == []


def test_old_handoffs_are_pruned_on_export(store, world):  # noqa: F811
    head = world.fix()
    old = export(store, world, head).output["handoff_id"]
    world.clock.advance(2 * 24 * 3600)
    ctx = InvocationContext("run-2", "fix[0]/export", "code", "spark", config={})
    new = export(store, world, head, ctx=ctx).output["handoff_id"]
    assert [d["id"] for d in store.find(HANDOFFS_COLLECTION)] == [new]
    assert old != new


# ------------------------------------------------------------------------ configuration


def test_unconfigured_run_as_refuses_both_steps(store, world):  # noqa: F811
    head = world.fix()
    exp = HandoffExportPort.from_env(store, {}).invoke(
        {"worktree": str(world.codex), "start_sha": world.up.start, "head_after": head},
        "k",
        DEADLINE,
        context=EXPORT_CTX,
    )
    assert exp.error.startswith("handoff_runner_unconfigured")
    assert "CULTURE_RULES_HANDOFF_RUN_AS" in exp.error
    imp = HandoffImportPort.from_env(store, {}).invoke(
        {
            "handoff_id": "hof_" + "1" * 32,
            "start_sha": world.up.start,
            "head_after": head,
            "clone_url": "https://github.com/o/r.git",
        },
        "k",
        DEADLINE,
        context=IMPORT_CTX,
    )
    assert imp.error.startswith("handoff_runner_unconfigured")
    assert "CULTURE_RULES_GATE_RUN_AS" in imp.error


@pytest.mark.parametrize("prefix", ["ssh spark", "su culture-codex-fixer -c", "bash -c"])
def test_a_shell_joining_prefix_is_refused(prefix):
    run_as, why = handoff_run_as_from_env({"CULTURE_RULES_HANDOFF_RUN_AS": prefix})
    assert run_as is None
    assert "may not start with" in why


def test_a_sudo_prefix_reads_from_its_own_env():
    env = {"CULTURE_RULES_HANDOFF_RUN_AS": "sudo -n -u culture-codex-fixer --"}
    run_as, _ = handoff_run_as_from_env(env)
    assert run_as.prefix == ("sudo", "-n", "-u", "culture-codex-fixer", "--")


def test_sudo_under_no_new_privs_is_run_as_blocked(store, world):  # noqa: F811
    env = {"CULTURE_RULES_HANDOFF_RUN_AS": "sudo -n -u culture-codex-fixer --"}
    port = HandoffExportPort.from_env(store, env, no_new_privs=lambda: True)
    res = port.invoke(
        {"worktree": str(world.codex), "start_sha": world.up.start, "head_after": world.fix()},
        "k",
        DEADLINE,
        context=EXPORT_CTX,
    )
    assert res.error.startswith("run_as_blocked")


def test_default_ports_register_both_handoff_builtins():
    code = default_ports(MemoryStore(), "spark")["code"]
    assert isinstance(code._builtins[EXPORT_BUILTIN], HandoffExportPort)
    assert isinstance(code._builtins[IMPORT_BUILTIN], HandoffImportPort)


# ------------------------------------------------------------ per-step config.run_as

SUDO_PREFIX = ["sudo", "-n", "-u", "culture-codex-fixer", "--"]


class _Recorder:
    """A fake process runner: records each argv and fails, so nothing real runs."""

    def __init__(self) -> None:
        self.argvs: list[list[str]] = []

    def __call__(self, argv, **_kwargs) -> int:
        self.argvs.append(list(argv))
        return 1


def _export_with(store, world, prefix, config, recorder):  # noqa: F811
    from culture_rules.actors.gate import RunAs

    port = HandoffExportPort(
        store, run_as=RunAs(prefix, run=recorder) if prefix else None, clock=world.clock
    )
    ctx = InvocationContext("run-1", "fix[1]/export", "code", "spark", config=config)
    inputs = {
        "worktree": str(world.codex),
        "start_sha": world.up.start,
        "head_after": world.fix(),
    }
    return port.invoke(inputs, "k", DEADLINE, context=ctx)


def test_step_run_as_replaces_the_account_in_the_prefix(store, world):  # noqa: F811
    rec = _Recorder()
    config = {"builtin": EXPORT_BUILTIN, "run_as": "culture-claude-fixer"}
    _export_with(store, world, SUDO_PREFIX, config, rec)
    assert rec.argvs[0][:5] == ["sudo", "-n", "-u", "culture-claude-fixer", "--"]


def test_a_step_without_run_as_keeps_the_env_default(store, world):  # noqa: F811
    rec = _Recorder()
    _export_with(store, world, SUDO_PREFIX, {"builtin": EXPORT_BUILTIN}, rec)
    assert rec.argvs[0][:5] == SUDO_PREFIX


@pytest.mark.parametrize(
    "bad", ["-u", "Root", "a b", "x;y", "a" * 33, "", "../x", 5, ["a"], "a\nb"]
)
def test_a_malformed_step_run_as_is_refused_before_running(store, world, bad):  # noqa: F811
    rec = _Recorder()
    config = {"builtin": EXPORT_BUILTIN, "run_as": bad}
    res = _export_with(store, world, SUDO_PREFIX, config, rec)
    assert res.error.startswith("bad_config")
    assert rec.argvs == []


def test_a_step_run_as_without_the_env_prefix_is_refused(store, world):  # noqa: F811
    rec = _Recorder()
    config = {"builtin": EXPORT_BUILTIN, "run_as": "culture-claude-fixer"}
    res = _export_with(store, world, None, config, rec)
    assert res.error.startswith("handoff_runner_unconfigured")
    assert rec.argvs == []


def test_a_step_run_as_needs_an_account_slot_in_the_prefix(store, world):  # noqa: F811
    rec = _Recorder()
    config = {"builtin": EXPORT_BUILTIN, "run_as": "culture-claude-fixer"}
    res = _export_with(store, world, ["doas"], config, rec)
    assert res.error.startswith("bad_config")
    assert rec.argvs == []


@pytest.mark.parametrize(
    "prefix",
    [
        ["sudo", "-n", "--", "env", "-u", "SOME_VAR"],  # -u after the command boundary
        ["sudo", "-n", "-u", "old", "-u", "other", "--"],  # two account selectors
        ["sudo", "-n", "-u", "old", "-uother", "--"],  # an attached selector
        ["sudo", "-nu", "old", "--"],  # a combined short-option cluster
        ["sudo", "-n", "--user=old", "-u", "other", "--"],  # long and short together
        ["sudo", "-n", "-g", "grp", "-u", "old", "--"],  # an option outside the allowlist
        ["sudo", "-n", "-u", "old"],  # no command boundary
        ["runuser", "-u", "old", "--"],  # not sudo
    ],
)
def test_a_prefix_whose_account_is_not_exactly_one_sudo_selector_is_refused(
    store, world, prefix  # noqa: F811
):
    """Codex review of #56 wave 0: run_as must decide the account, or nothing runs."""
    rec = _Recorder()
    config = {"builtin": EXPORT_BUILTIN, "run_as": "culture-claude-fixer"}
    res = _export_with(store, world, prefix, config, rec)
    assert res.error.startswith("bad_config")
    assert rec.argvs == []


@pytest.mark.parametrize(
    "prefix, expected",
    [
        (
            ["sudo", "-n", "--user=culture-codex-fixer", "--"],
            ["sudo", "-n", "--user=culture-claude-fixer", "--"],
        ),
        (
            ["/usr/bin/sudo", "-n", "-H", "--user", "x", "--", "/usr/bin/env", "PATH=/usr/bin"],
            ["/usr/bin/sudo", "-n", "-H", "--user", "culture-claude-fixer", "--"],
        ),
    ],
)
def test_the_single_selector_is_replaced_and_the_rest_kept(
    store, world, prefix, expected  # noqa: F811
):
    rec = _Recorder()
    config = {"builtin": EXPORT_BUILTIN, "run_as": "culture-claude-fixer"}
    _export_with(store, world, prefix, config, rec)
    assert rec.argvs[0][: len(expected)] == expected
    assert rec.argvs[0][: len(prefix)][len(expected) :] == prefix[len(expected) :]
