"""Keysets end to end (state-between-runs t5): workflows over keyset.filter and keyset.add.

One node runs the workflows on MemoryStore with the real keyset ports (their clock is the
test clock). The workflows are fixtures here, never committed bundles:

* a two-run workflow built only from keyset.filter and keyset.add over a fixed list;
* the same pair with a fake step before the add and a fake commit step after it, each
  failing on demand, so a failed run and its re-run can be counted;
* a newspaper-shaped flow: a for_each search loop (search.web on a fake Brave, then
  keyset.filter, then newspaper.filter) yielding one list per section, newspaper.render, and
  keyset.add fed from the loop's real output;
* a generic for_each loop whose per-port output (one list per iteration) feeds keyset.add.

The boundary test checks that the work left the newspaper code, the trust list and the
model's validation untouched, and that no node module writes a shared variable.

Synthetic data only.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - a fixed git command, no shell
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from culture_rules import keysets
from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.runs import RUNS_COLLECTION, step_key, step_state
from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.model.validate import validate
from culture_rules.model.workflow import Workflow
from culture_rules.node.actions.keyset import (
    KEYSET_ADD_BUILTIN,
    KEYSET_FILTER_BUILTIN,
    KeysetAddPort,
    KeysetFilterPort,
)
from culture_rules.node.actions.newspaper import (
    FILTER_BUILTIN,
    RENDER_BUILTIN,
    SECTIONS_BUILTIN,
    NewspaperFilterPort,
    NewspaperRenderPort,
    NewspaperSectionsPort,
)
from culture_rules.node.actions.newspaper_render import NO_RESULTS
from culture_rules.node.actions.search import SearchWebPort
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.node.daemon import Node
from culture_rules.node.runner import BuiltinCodePort, NoopAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.node.test_node import BEATS

REPO = Path(__file__).resolve().parents[2]
SET = "flow-links"
SEARCH_ACTOR = "flow-search"
FAKE_STEP = "test.fake"
NORMALISE = {
    "lower_host": True,
    "drop_scheme": True,
    "drop_fragment": True,
    "drop_trailing_slash": True,
    "drop_query": ["utm_*", "ref"],
}
KEYSET = {"set": SET, "key_field": "url", "normalise": NORMALISE, "window_days": 14}
FIXED = [
    {"url": "https://a.example/one", "title": "one"},
    {"url": "https://b.example/two", "title": "two"},
    {"title": "keyless"},
]
FIXED_KEYS = ["a.example/one", "b.example/two"]
SUCCEEDED = "succeeded"


# --------------------------------------------------------------------------- the fakes


class FakeStep:
    """A builtin code step that hands its ``items`` on, or fails while ``fail`` is set."""

    supports_idempotency_key = True

    def __init__(self) -> None:
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def invoke(self, input, _key, _deadline, *, context):
        self.calls.append(context.step_id)
        if context.step_id in self.fail:
            return InvocationResult.failed(f"fake_failure: {context.step_id}", retryable=False)
        return InvocationResult.completed({"items": input.get("items") or []})


class Brave:
    """Brave's web search: the results set per query in ``answers`` (title, url)."""

    def __init__(self) -> None:
        self.answers: dict[str, list[str]] = {}
        self.queries: list[str] = []

    def __call__(self, method, url, headers, body, timeout):
        query = parse_qs(urlsplit(url).query)["q"][0]
        self.queries.append(query)
        results = [
            {
                "title": f"{query} story {n}",
                "url": link,
                "description": f"What happened in {query} ({n})",
                "meta_url": {"hostname": urlsplit(link).hostname},
                "age": "1 hour ago",
            }
            for n, link in enumerate(self.answers.get(query, []))
        ]
        return 200, json.dumps({"web": {"results": results}}).encode()


# --------------------------------------------------------------------------- workflow pieces


def _port(name, typ, required=True):
    return {"name": name, "type": typ, "required": required, "description": ""}


def _edge(source, source_port, target, target_port):
    return {
        "source": source,
        "source_port": source_port,
        "target": target,
        "target_port": target_port,
    }


def _step(step_id, config, inputs, outputs, **extra):
    return {
        "id": step_id,
        "name": step_id,
        "kind": "code",
        "inputs": inputs,
        "outputs": outputs,
        "config": config,
        **extra,
    }


def _filter_step(step_id="seen_filter"):
    return _step(
        step_id,
        {"builtin": KEYSET_FILTER_BUILTIN, **KEYSET},
        [_port("items", "array")],
        [_port("items", "array"), _port("kept", "integer"), _port("dropped", "integer")],
    )


def _add_step(step_id="seen_add", **extra):
    return _step(
        step_id,
        {"builtin": KEYSET_ADD_BUILTIN, **KEYSET, "max_keys": 200, **extra},
        [_port("items", "array")],
        [_port("added", "integer"), _port("already_present", "integer")],
    )


def _fake_step(step_id):
    """A fake step; its optional ``after`` input only orders it after another step."""
    inputs = [_port("items", "array", required=False), _port("after", "any", required=False)]
    return _step(step_id, {"builtin": FAKE_STEP}, inputs, [_port("items", "array")])


def pair_workflow() -> dict:
    """keyset.filter -> keyset.add over the workflow input ``items``; nothing else."""
    return {
        "id": "keyset-pair",
        "name": "Keyset pair",
        "inputs": [_port("items", "array")],
        "steps": [_filter_step(), _add_step()],
        "edges": [
            _edge("inputs", "items", "seen_filter", "items"),
            _edge("seen_filter", "items", "seen_add", "items"),
        ],
        "outputs": [
            {"name": "items", "source": "steps.seen_filter.outputs.items", "type": "array"},
            {"name": "added", "source": "steps.seen_add.outputs.added", "type": "integer"},
        ],
    }


def committed_workflow() -> dict:
    """filter -> work (fake) -> add -> commit (fake): the add just before its commit step."""
    return {
        "id": "keyset-committed",
        "name": "Keyset committed",
        "inputs": [_port("items", "array")],
        "steps": [_filter_step(), _fake_step("work"), _add_step(), _fake_step("commit")],
        "edges": [
            _edge("inputs", "items", "seen_filter", "items"),
            _edge("seen_filter", "items", "work", "items"),
            _edge("work", "items", "seen_add", "items"),
            _edge("work", "items", "commit", "items"),
            _edge("seen_add", "added", "commit", "after"),
        ],
        "outputs": [
            {"name": "items", "source": "steps.commit.outputs.items", "type": "array"},
        ],
    }


def per_port_workflow() -> dict:
    """for_each over groups (keyset.filter in the body, skipped for ``skip: true``; every group
    says ``skip``) whose per-port ``items`` output (one list per iteration, None for a skipped
    one) feeds the add."""
    body = _filter_step()
    body["inputs"] = [_port("item", "object")]
    body["config"]["with"] = {"items": "inputs.item.links"}
    body["config"]["when"] = {
        "op": "compare",
        "cmp": "==",
        "left": {"field": "item.skip"},
        "right": {"literal": False},
    }
    body["inputs"].append(_port("items", "array", required=False))
    return {
        "id": "keyset-per-port",
        "name": "Keyset per port",
        "inputs": [_port("groups", "array")],
        "steps": [
            {
                "id": "each",
                "name": "each",
                "kind": "for_each",
                "max_iterations": 10,
                "config": {"items": "groups"},
                "inputs": [_port("groups", "array")],
                "outputs": [_port("items", "array")],
                "body": [body],
            },
            _add_step(),
        ],
        "edges": [
            _edge("inputs", "groups", "each", "groups"),
            _edge("each", "items", "seen_add", "items"),
        ],
        "outputs": [
            {"name": "added", "source": "steps.seen_add.outputs.added", "type": "integer"},
        ],
    }


def newspaper_workflow() -> dict:
    """sections -> search loop (search.web -> keyset.filter -> newspaper.filter) -> render,
    then keyset.add over the loop's ``results`` -> commit (fake), as wired live."""
    search_web = _step(
        "search_web",
        {
            "builtin": "action",
            "idempotent": True,
            "action": {
                "kind": "search.web",
                "params": {
                    "actor": SEARCH_ACTOR,
                    "query": "inputs.item.query",
                    "count": "inputs.item.count",
                    "freshness": "inputs.item.freshness",
                },
            },
        },
        [_port("item", "object")],
        [_port("results", "array"), _port("query", "string"), _port("count", "integer")],
    )
    seen = _filter_step()
    section_filter = _step(
        "filter",
        {"builtin": FILTER_BUILTIN},
        [_port("item", "object"), _port("results", "array")],
        [_port("results", "array")],
    )
    # The loop's results port is one output object per section, {results: [...]} (the last
    # body step's outputs), so the add names the field that holds each section's items.
    after_render = _add_step(items_field="results")
    after_render["inputs"].append(_port("after", "any", required=False))
    return {
        "id": "keyset-newspaper",
        "name": "Keyset newspaper",
        "inputs": [_port("sections", "string"), _port("date", "string")],
        "steps": [
            _step(
                "sections",
                {"builtin": SECTIONS_BUILTIN},
                [_port("sections", "string")],
                [_port("sections", "array")],
            ),
            {
                "id": "search",
                "name": "search",
                "kind": "for_each",
                "max_iterations": 10,
                "config": {"items": "items"},
                "inputs": [_port("items", "array")],
                "outputs": [_port("results", "array")],
                "body": [search_web, seen, section_filter],
            },
            _step(
                "render",
                {"builtin": RENDER_BUILTIN},
                [
                    _port("date", "string"),
                    _port("sections", "array"),
                    _port("results", "array"),
                ],
                [_port("html", "string"), _port("subject", "string")],
            ),
            after_render,
            _fake_step("commit"),
        ],
        "edges": [
            _edge("inputs", "sections", "sections", "sections"),
            _edge("sections", "sections", "search", "items"),
            _edge("search_web", "results", "seen_filter", "items"),
            _edge("seen_filter", "items", "filter", "results"),
            _edge("inputs", "date", "render", "date"),
            _edge("sections", "sections", "render", "sections"),
            _edge("search", "results", "render", "results"),
            _edge("search", "results", "seen_add", "items"),
            _edge("render", "subject", "seen_add", "after"),
            _edge("seen_add", "added", "commit", "after"),
        ],
        "outputs": [
            {"name": "html", "source": "steps.render.outputs.html", "type": "string"},
        ],
    }


# --------------------------------------------------------------------------- the world


class World:
    """spark with the keyset ports, the newspaper builtins, a fake step and a fake Brave."""

    def __init__(self) -> None:
        self.clock = Clock()
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        self.base.put(
            ACTORS_COLLECTION,
            {
                "id": SEARCH_ACTOR,
                "name": SEARCH_ACTOR,
                "kind": "app",
                "enabled": True,
                "machine": "spark",
                "params": {
                    "surface": "search",
                    "actions": ["search.web"],
                    "connection": {"api_key": "grant:FAKE_SEARCH_KEY"},
                },
            },
        )
        self.fake = FakeStep()
        self.brave = Brave()
        clock = self.clock
        ports = {
            "action:noop": NoopAction(),
            "action:search.web": SearchWebPort(
                self.base, transport=self.brave, secrets=lambda _ref: "fake-key"
            ),
            "code": BuiltinCodePort(
                {
                    KEYSET_FILTER_BUILTIN: KeysetFilterPort(self.base, clock=clock),
                    KEYSET_ADD_BUILTIN: KeysetAddPort(self.base, clock=clock),
                    SECTIONS_BUILTIN: NewspaperSectionsPort(),
                    FILTER_BUILTIN: NewspaperFilterPort(),
                    RENDER_BUILTIN: NewspaperRenderPort(),
                    FAKE_STEP: self.fake,
                }
            ),
        }
        self.node = Node(
            self.base.peer(), "spark", actors=ports, clock=clock, heartbeat_options=BEATS
        )
        self.node.start()

    def run(self, definition: dict, inputs: dict) -> dict:
        self.base.put("workflows", definition)
        started = self.node.executor.start_workflow(definition["id"], inputs, "test")
        for _ in range(60):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors
        return self.base.get(RUNS_COLLECTION, started["id"])

    def keys(self) -> list[str]:
        return [d["key"] for d in keysets.show_set(self.base, SET, self.clock())]

    def first_seen(self) -> dict:
        return {d["key"]: d["first_seen"] for d in keysets.show_set(self.base, SET, self.clock())}

    def documents(self) -> int:
        return len(self.base.find(keysets.KEYSETS_COLLECTION, {"set": SET}))


@pytest.fixture
def world():
    return World()


def outputs_of(run: dict, key: str) -> dict:
    return step_state(run, key)["outputs"]


def inputs_of(run: dict, key: str) -> dict:
    return step_state(run, key)["inputs"]


# --------------------------------------------------------------------------- the fixtures


@pytest.mark.parametrize(
    "build", [pair_workflow, committed_workflow, per_port_workflow, newspaper_workflow]
)
def test_the_fixture_validates(build):
    assert validate(Workflow.from_dict(build())) == []


# --------------------------------------------------------------------------- (1) two runs


def two_pair_runs(world: World) -> tuple[dict, dict]:
    first = world.run(pair_workflow(), {"items": FIXED})
    second = world.run(pair_workflow(), {"items": FIXED})
    return first, second


def test_the_first_pair_run_succeeds(world):  # c1
    first, _ = two_pair_runs(world)
    assert first["status"] == SUCCEEDED, (first.get("error"), first["steps"])


def test_the_first_pair_run_keeps_every_item(world):  # c1
    first, _ = two_pair_runs(world)
    assert first["outputs"]["items"] == FIXED


def test_the_first_pair_run_adds_each_keyed_item(world):  # c1
    first, _ = two_pair_runs(world)
    assert first["outputs"]["added"] == 2


def test_the_second_pair_run_succeeds(world):  # c1
    _, second = two_pair_runs(world)
    assert second["status"] == SUCCEEDED, (second.get("error"), second["steps"])


def test_the_second_pair_run_drops_what_the_first_added(world):  # c1, h1
    _, second = two_pair_runs(world)
    assert second["outputs"]["items"] == [{"title": "keyless"}]


def test_the_second_pair_run_counts_the_dropped_items(world):  # c1, h1
    _, second = two_pair_runs(world)
    assert outputs_of(second, "seen_filter")["dropped"] == 2


def test_the_set_holds_the_normalised_keys_after_two_runs(world):  # c1
    two_pair_runs(world)
    assert world.keys() == FIXED_KEYS


def test_a_key_comes_back_once_its_window_has_passed(world):  # c1
    world.run(pair_workflow(), {"items": FIXED})
    world.clock.advance(timedelta(days=15).total_seconds())
    again = world.run(pair_workflow(), {"items": FIXED})
    assert again["outputs"]["items"] == FIXED


# --------------------------------------------------------------------------- (2) failures


def test_a_run_failing_before_the_add_fails(world):  # c7, h9
    world.fake.fail = {"work"}
    run = world.run(committed_workflow(), {"items": FIXED})
    assert run["status"] == "failed"


def test_a_run_failing_before_the_add_leaves_the_set_unchanged(world):  # c7, h9
    world.fake.fail = {"work"}
    world.run(committed_workflow(), {"items": FIXED})
    assert world.documents() == 0


def test_a_run_failing_before_the_add_never_reaches_the_add(world):  # c7, h9
    world.fake.fail = {"work"}
    run = world.run(committed_workflow(), {"items": FIXED})
    assert step_state(run, "seen_add")["status"] != SUCCEEDED


def test_after_a_failure_before_the_add_a_rerun_keeps_every_item(world):  # c7, h9
    world.fake.fail = {"work"}
    world.run(committed_workflow(), {"items": FIXED})
    world.fake.fail = set()
    rerun = world.run(committed_workflow(), {"items": FIXED})
    assert rerun["outputs"]["items"] == FIXED


def fail_at_commit_then_rerun(world: World) -> tuple[dict, dict, dict]:
    """The commit fails on the first run and succeeds on the re-run; the first-seen times
    after the first run are returned with both runs."""
    world.fake.fail = {"commit"}
    first = world.run(committed_workflow(), {"items": FIXED})
    seen_after_first = world.first_seen()
    world.fake.fail = set()
    rerun = world.run(committed_workflow(), {"items": FIXED})
    return first, rerun, seen_after_first


def test_a_run_failing_at_its_commit_fails(world):  # c8, h10
    first, _, _ = fail_at_commit_then_rerun(world)
    assert first["status"] == "failed"


def test_a_run_failing_at_its_commit_has_added_its_keys(world):  # c8, h10
    first, _, _ = fail_at_commit_then_rerun(world)
    assert outputs_of(first, "seen_add")["added"] == 2


def test_the_rerun_after_a_failed_commit_succeeds(world):  # c8, h10
    _, rerun, _ = fail_at_commit_then_rerun(world)
    assert rerun["status"] == SUCCEEDED, (rerun.get("error"), rerun["steps"])


def test_the_rerun_after_a_failed_commit_holds_one_document_per_key(world):  # c8, h10
    fail_at_commit_then_rerun(world)
    assert world.documents() == 2


def test_the_rerun_after_a_failed_commit_keeps_the_first_seen_times(world):  # c8, h10
    _, _, seen_after_first = fail_at_commit_then_rerun(world)
    assert world.first_seen() == seen_after_first


def test_the_rerun_after_a_failed_commit_adds_nothing_twice(world):  # c8, h10
    _, rerun, _ = fail_at_commit_then_rerun(world)
    assert outputs_of(rerun, "seen_add")["added"] == 0


# --------------------------------------------------------------------------- (3) newspaper

SECTIONS = [
    {"name": "Edge AI", "queries": ["edge ai"]},
    {"name": "Robots", "queries": ["robots"]},
]
EDGE_ONE = "https://news.example/edge/one"
EDGE_TWO = "https://news.example/edge/two"
EDGE_THREE = "https://news.example/edge/three"
ROBOT_ONE = "https://robots.example/arm/one"
FIRST_DAY = {"edge ai": [EDGE_ONE, EDGE_TWO], "robots": [ROBOT_ONE]}
# Day two: edge/one again, written another way; robots only repeats.
SECOND_DAY = {
    "edge ai": ["https://News.Example/edge/one/?utm_source=feed#top", EDGE_THREE],
    "robots": [ROBOT_ONE],
}


def newspaper_run(world: World, answers: dict, date: str) -> dict:
    world.brave.answers = answers
    inputs = {"sections": json.dumps(SECTIONS), "date": date}
    return world.run(newspaper_workflow(), inputs)


def two_newspaper_runs(world: World) -> tuple[dict, dict]:
    first = newspaper_run(world, FIRST_DAY, "2026-10-28")
    second = newspaper_run(world, SECOND_DAY, "2026-10-29")
    return first, second


def html_of(run: dict) -> str:
    return outputs_of(run, "render")["html"]


def test_the_first_newspaper_run_succeeds(world):  # c10
    first, _ = two_newspaper_runs(world)
    assert first["status"] == SUCCEEDED, (first.get("error"), first["steps"])


def test_the_second_newspaper_run_succeeds(world):  # c10
    _, second = two_newspaper_runs(world)
    assert second["status"] == SUCCEEDED, (second.get("error"), second["steps"])


def test_the_first_issue_links_every_result(world):  # c10
    first, _ = two_newspaper_runs(world)
    html = html_of(first)
    assert [u in html for u in (EDGE_ONE, EDGE_TWO, ROBOT_ONE)] == [True, True, True]


def test_the_add_after_the_loop_stores_every_result_of_the_first_run(world):  # c10, h11
    newspaper_run(world, FIRST_DAY, "2026-10-28")
    assert world.keys() == ["news.example/edge/one", "news.example/edge/two", ROBOT_ONE[8:]]


def test_the_add_after_the_loop_counts_every_result_of_the_first_run(world):  # c10, h11
    first = newspaper_run(world, FIRST_DAY, "2026-10-28")
    assert outputs_of(first, "seen_add")["added"] == 3


def test_the_add_receives_the_loops_results_one_object_per_section(world):  # the shape
    first = newspaper_run(world, FIRST_DAY, "2026-10-28")
    received = inputs_of(first, "seen_add")["items"]
    assert [sorted(entry) for entry in received] == [["results"], ["results"]]


def test_without_items_field_the_add_over_the_loop_results_stores_nothing(world):  # the shape
    definition = newspaper_workflow()
    add = next(s for s in definition["steps"] if s["id"] == "seen_add")
    del add["config"]["items_field"]
    world.brave.answers = FIRST_DAY
    world.run(definition, {"sections": json.dumps(SECTIONS), "date": "2026-10-28"})
    assert world.documents() == 0


def test_a_seen_url_is_gone_from_the_second_issue(world):  # c10, h11
    _, second = two_newspaper_runs(world)
    assert "edge/one" not in html_of(second)


def test_a_fresh_url_is_in_the_second_issue(world):  # c10, h11
    _, second = two_newspaper_runs(world)
    assert EDGE_THREE in html_of(second)


def test_a_section_emptied_by_the_filter_renders_no_results(world):  # c13, h14
    _, second = two_newspaper_runs(world)
    robots = html_of(second).split("Robots", 1)[1]
    assert NO_RESULTS in robots


def test_the_emptied_section_does_not_repeat_its_link(world):  # c13, h14
    _, second = two_newspaper_runs(world)
    assert ROBOT_ONE not in html_of(second)


def test_the_filter_drops_the_seen_result_inside_the_loop(world):  # c10
    _, second = two_newspaper_runs(world)
    assert outputs_of(second, step_key("search", 0, "seen_filter"))["dropped"] == 1


def test_the_second_add_stores_only_the_fresh_url(world):  # c10, h11
    _, second = two_newspaper_runs(world)
    assert outputs_of(second, "seen_add")["added"] == 1


# --------------------------------------------------------------------------- per-port lists

GROUPS = [
    {"links": [{"url": "https://a.example/one"}], "skip": False},
    {"links": [{"url": "https://a.example/two"}], "skip": True},
    {"links": [{"url": "https://a.example/three"}], "skip": False},
]


def test_a_per_port_loop_output_with_a_skipped_iteration_succeeds(world):  # c10
    run = world.run(per_port_workflow(), {"groups": GROUPS})
    assert run["status"] == SUCCEEDED, (run.get("error"), run["steps"])


def test_a_per_port_loop_output_hands_the_add_one_list_per_iteration(world):  # the shape
    run = world.run(per_port_workflow(), {"groups": GROUPS})
    assert inputs_of(run, "seen_add")["items"] == [
        [{"url": "https://a.example/one"}],
        None,
        [{"url": "https://a.example/three"}],
    ]


def test_the_add_stores_the_keys_of_the_iterations_that_ran(world):  # c10
    world.run(per_port_workflow(), {"groups": GROUPS})
    assert world.keys() == ["a.example/one", "a.example/three"]


# --------------------------------------------------------------------------- (4) boundaries

UNTOUCHED = [
    "culture_rules/node/actions/newspaper.py",
    "culture_rules/node/actions/newspaper_render.py",
    "culture_rules/node/actions/newspaper_issue.py",
]
# trusted.py and validate.py left this list with the green PR merge prompt: github.merge needs
# the merge trust role and the click's custom_id filter needs its trigger check, so a branch
# diff cannot tell keyset work from them either. The keysets still add no trust role or check.
# action_kinds.py left this list in 0.38.0: the next new action kind (repodocs.sync, #124)
# must edit the catalogue, so a branch diff cannot tell keyset work from it. The boundary
# itself (the keysets add no action kind, h14) is checked on the catalogue below.


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 B607 - fixed arguments, no shell
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )


def _changed_files() -> set[str]:
    try:
        inside = _git("rev-parse", "--is-inside-work-tree")
    except OSError:
        pytest.skip("git is not available")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("not inside a git checkout")
    if _git("rev-parse", "--verify", "--quiet", "origin/main").returncode != 0:
        pytest.skip("origin/main is unknown here")
    diff = _git("diff", "origin/main", "--name-only")
    if diff.returncode != 0:
        pytest.skip(f"git diff failed: {diff.stderr.strip()}")
    return set(diff.stdout.split())


def test_the_work_touches_no_newspaper_trust_or_validation_module():  # c13, h14
    assert sorted(_changed_files() & set(UNTOUCHED)) == []


def test_the_keysets_add_no_action_kind():  # h14: they are builtin code steps
    assert [kind for kind in ACTION_KINDS if "keyset" in kind] == []


def test_no_node_module_calls_put_variable():  # h9
    node = REPO / "culture_rules" / "node"
    callers = [
        os.fspath(p.relative_to(REPO))
        for p in sorted(node.rglob("*.py"))
        if "put_variable" in p.read_text(encoding="utf-8")
    ]
    assert callers == []
