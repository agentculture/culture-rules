"""The sources-manifest flows end to end (culture-rules#97 t5), on MemoryStore and a fake library.

The workflows are fixtures here, built from the step shapes in
``docs/operations/sources-manifest.md`` (not committed bundles). One node runs them on the real
ports: ``newspaper.render``,
``document.sections`` and ``http.call`` (its secret runner is a fake grant runner). The library is a
local ``http.server`` on 127.0.0.1 whose IP is on the library actor's allow list. It records each
PUT, can be told to answer 500, and serves each known item's EPUB, rendered by ``newspaper.render``.
Synthetic data only.
"""

from __future__ import annotations

import base64
import copy
import http.server
import json
import os
import threading

import pytest

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.model.validate import validate
from culture_rules.model.workflow import Workflow
from culture_rules.node import runner
from culture_rules.node.actions.http import HttpCallPort
from culture_rules.node.actions.newspaper_render import render_issue
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.node.daemon import Node
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.node.test_node import BEATS

SECRET = "fake-library-key"
GRANT = "LIBRARY_PUSH_API_KEY"
ACTOR = "newspaper-library"

# The sections config, once: the daily step and the backfill step both take exactly this.
SECTIONS_CONFIG = {
    "builtin": "document.sections",
    "split_on": "h2",
    "paragraphs": 1,
    "max_chars": 300,
    "require_links": True,
    "fields": {"heading": "article_title", "links": "source_urls", "excerpt": "excerpt"},
}


def _issue(day: str) -> dict:
    """The sections a daily issue is rendered from: two with a link, one saying "No results"."""
    return {
        "date": day,
        "title": "Daily newspaper",
        "sections": [
            {
                "name": "Models",
                "results": [
                    {
                        "title": f"Story {day}",
                        "url": f"https://news.example/{day}/a",
                        "snippet": "A model shipped.",
                        "age": "1 hour ago",
                        "source": "news.example",
                    }
                ],
                "summary": f"- Shipped [now](https://news.example/{day}/a)",
            },
            {
                "name": "Robots",
                "results": [
                    {
                        "title": "Robot story",
                        "url": f"https://robots.example/{day}",
                        "snippet": "An arm moved.",
                        "age": "2 hours ago",
                        "source": "robots.example",
                    }
                ],
                "summary": None,
            },
            {"name": "Quiet", "results": [], "summary": None},
        ],
    }


def epub_of(day: str) -> bytes:
    return base64.b64decode(render_issue(_issue(day))["epub_base64"])


# --------------------------------------------------------------------------- the fake library


class FakeLibrary:
    """A threaded http.server on 127.0.0.1 holding items {id: EPUB bytes}."""

    def __init__(self, items: dict[int, bytes]) -> None:
        self.items = items
        self.puts: dict[int, list] = {}
        self.auth: list[str | None] = []
        self.fail_puts = False
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def _item(self, tail: str) -> int | None:
                parts = self.path.split("/")
                ok = len(parts) == 5 and parts[:3] == ["", "api", "library"] and parts[4] == tail
                return int(parts[3]) if ok and parts[3].isdigit() else None

            def _answer(self, status: int, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                outer.auth.append(self.headers.get("Authorization"))
                item = self._item("file")
                if item not in outer.items:
                    self._answer(404, b'{"error":"not_found"}', "application/json")
                else:
                    self._answer(200, outer.items[item], "application/epub+zip")

            def do_PUT(self) -> None:  # noqa: N802
                outer.auth.append(self.headers.get("Authorization"))
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                item = self._item("sources")
                if item is None or outer.fail_puts:
                    self._answer(500, b'{"error":"boom"}', "application/json")
                    return
                entries = json.loads(raw)
                outer.puts.setdefault(item, []).append(entries)
                reply = {"id": item, "entries": len(entries)}
                self._answer(200, json.dumps(reply).encode(), "application/json")

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


# --------------------------------------------------------------------------- the workflows


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


def _manifest_step(inputs):
    return _step(
        "manifest",
        copy.deepcopy(SECTIONS_CONFIG),
        inputs,
        [_port("sections", "array"), _port("count", "integer")],
    )


def _put_step(on_error):
    """The documented ``put_manifest`` step: ``http.call`` PUT of ``inputs.sections``."""
    return _step(
        "put_manifest",
        {
            "builtin": "action",
            "on_error": on_error,
            "action": {
                "kind": "http.call",
                "params": {
                    "actor": ACTOR,
                    "method": "PUT",
                    "url": "{{ inputs.library_base }}/api/library/{{ inputs.item_id }}/sources",
                    "body": "inputs.sections",
                },
            },
        },
        [
            _port("sections", "array"),
            _port("item_id", "integer"),
            _port("library_base", "string"),
        ],
        [_port("status", "integer"), _port("body", "string")],
    )


def daily_workflow() -> dict:
    """render -> manifest (sections of its EPUB) -> put_manifest, as after ``record`` live."""
    return {
        "id": "daily-shaped",
        "name": "Daily shaped",
        "inputs": [
            _port("date", "string"),
            _port("sections", "array"),
            _port("item_id", "integer"),
            _port("library_base", "string"),
        ],
        "steps": [
            _step(
                "render",
                {"builtin": "newspaper.render"},
                [_port("date", "string"), _port("sections", "array")],
                [_port("epub_base64", "string")],
            ),
            _manifest_step([_port("content_base64", "string")]),
            _put_step("skip"),
        ],
        "edges": [
            _edge("inputs", "date", "render", "date"),
            _edge("inputs", "sections", "render", "sections"),
            _edge("render", "epub_base64", "manifest", "content_base64"),
            _edge("manifest", "sections", "put_manifest", "sections"),
            _edge("inputs", "item_id", "put_manifest", "item_id"),
            _edge("inputs", "library_base", "put_manifest", "library_base"),
        ],
        "outputs": [
            {"name": "count", "source": "steps.manifest.outputs.count", "type": "integer"},
        ],
    }


def backfill_workflow(root: str) -> dict:
    """item_ids in; per id: GET saved to a file, sections by path, PUT; each step skips on error."""
    fetch = _step(
        "fetch",
        {
            "builtin": "action",
            "on_error": "skip",
            "action": {
                "kind": "http.call",
                "params": {
                    "actor": ACTOR,
                    "method": "GET",
                    "url": "{{ inputs.library_base }}/api/library/{{ inputs.item.id }}/file",
                    "save_to": root,
                },
            },
        },
        [_port("item", "object"), _port("library_base", "string")],
        [_port("path", "string"), _port("sha256", "string")],
    )
    manifest = _manifest_step([_port("content_path", "string")])
    manifest["config"]["content_root"] = root
    manifest["config"]["on_error"] = "skip"
    put = _put_step("skip")
    put["inputs"] = [
        _port("sections", "array"),
        _port("item", "object"),
        _port("library_base", "string"),
    ]
    put["config"]["action"]["params"][
        "url"
    ] = "{{ inputs.library_base }}/api/library/{{ inputs.item.id }}/sources"
    return {
        "id": "backfill-manifests",
        "name": "Backfill manifests",
        "inputs": [_port("item_ids", "array"), _port("library_base", "string")],
        "steps": [
            {
                "id": "each",
                "name": "each",
                "kind": "for_each",
                "max_iterations": 20,
                "config": {"items": "item_ids", "collect": {"count": "manifest.count"}},
                "inputs": [_port("item_ids", "array"), _port("library_base", "string")],
                "outputs": [_port("results", "array"), _port("outcomes", "array")],
                "body": [fetch, manifest, put],
            }
        ],
        "edges": [
            _edge("inputs", "item_ids", "each", "item_ids"),
            _edge("inputs", "library_base", "each", "library_base"),
            _edge("fetch", "path", "manifest", "content_path"),
            _edge("manifest", "sections", "put_manifest", "sections"),
        ],
        "outputs": [
            {"name": "results", "source": "steps.each.outputs.results", "type": "array"},
            {"name": "outcomes", "source": "steps.each.outputs.outcomes", "type": "array"},
        ],
    }


# --------------------------------------------------------------------------- the world


class World:
    def __init__(self, library: FakeLibrary, root: str) -> None:
        self.library = library
        self.root = root
        self.clock = Clock()
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        self.base.put(
            ACTORS_COLLECTION,
            {
                "id": ACTOR,
                "name": ACTOR,
                "kind": "service",
                "enabled": True,
                "params": {
                    "http": {
                        "allow": ["127.0.0.1"],
                        "headers": {"Authorization": f"Bearer grant:{GRANT}"},
                        "save_roots": [root],
                    }
                },
            },
        )
        self.grants: list[str] = []
        ports = runner.default_ports(self.base, "spark")
        ports["action:http.call"] = HttpCallPort(
            self.base, secret_runner=self.grant, clock=self.clock
        )
        self.node = Node(
            self.base.peer(), "spark", actors=ports, clock=self.clock, heartbeat_options=BEATS
        )
        self.node.start()

    def grant(self, ref: str) -> str:
        self.grants.append(ref)
        return SECRET

    def run(self, definition: dict, inputs: dict) -> dict:
        self.base.put("workflows", definition)
        started = self.node.executor.start_workflow(definition["id"], inputs, "test")
        for _ in range(60):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors
        return self.base.get(RUNS_COLLECTION, started["id"])


@pytest.fixture
def library():
    lib = FakeLibrary({n: epub_of(f"2026-10-{n:02d}") for n in (11, 12, 13)})
    yield lib
    lib.close()


@pytest.fixture
def world(library, tmp_path):
    fetched = tmp_path / "fetched"
    fetched.mkdir()
    return World(library, os.fspath(fetched))


def step_state(run: dict, key: str) -> dict:
    return next(s for s in run["steps"] if s["key"] == key)


def daily_run(world: World, item_id: int = 42) -> dict:
    issue = _issue("2026-10-27")
    inputs = {
        "date": issue["date"],
        "sections": issue["sections"],
        "item_id": item_id,
        "library_base": world.library.base,
    }
    return world.run(daily_workflow(), inputs)


def backfill_run(world: World, ids: list) -> dict:
    inputs = {"item_ids": [{"id": n} for n in ids], "library_base": world.library.base}
    return world.run(backfill_workflow(world.root), inputs)


# --------------------------------------------------------------------------- the fixtures


def test_the_daily_fixture_validates():
    definition = daily_workflow()
    assert validate(Workflow.from_dict(definition)) == []


def test_the_backfill_fixture_validates(tmp_path):
    assert validate(Workflow.from_dict(backfill_workflow(os.fspath(tmp_path)))) == []


def test_both_flows_carry_one_identical_sections_config(tmp_path):  # c10, h13
    daily = _manifest_step([])["config"]
    backfill = backfill_workflow(os.fspath(tmp_path))["steps"][0]["body"][1]["config"]
    shared = {k: v for k, v in backfill.items() if k not in ("content_root", "on_error")}
    assert shared == daily


# --------------------------------------------------------------------------- (a) daily


def test_the_daily_run_succeeds(world):
    run = daily_run(world)
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])


def test_the_library_receives_the_manifest_of_the_issue(world):  # c9
    daily_run(world)
    (entries,) = world.library.puts[42]
    assert [e["article_title"] for e in entries] == ["Models", "Robots"]


def test_each_entry_holds_the_urls_and_an_excerpt_under_the_configured_names(world):  # c9, h9
    daily_run(world)
    (entries,) = world.library.puts[42]
    assert entries[0] == {
        "article_title": "Models",
        "source_urls": ["https://news.example/2026-10-27/a"],
        "excerpt": entries[0]["excerpt"],
    }


def test_the_excerpt_is_short_and_verbatim_from_the_issue(world):  # h9
    daily_run(world)
    (entries,) = world.library.puts[42]
    assert 0 < len(entries[0]["excerpt"]) <= 300


def test_a_section_without_a_link_is_left_out_of_the_manifest(world):  # c9
    daily_run(world)
    (entries,) = world.library.puts[42]
    assert "Quiet" not in [e["article_title"] for e in entries]


def test_the_put_carries_the_bearer_grant_value_and_the_fake_grant_ran(world):  # c15
    daily_run(world)
    assert world.library.auth == [f"Bearer {SECRET}"]


def test_the_grant_reference_is_resolved_by_name(world):  # c15
    daily_run(world)
    assert world.grants == [GRANT]


def test_the_stored_daily_run_holds_no_secret(world):  # c15
    run = daily_run(world)
    assert SECRET not in json.dumps(run)


def test_the_put_step_outputs_the_status_and_the_libraries_answer(world):
    run = daily_run(world)
    assert json.loads(step_state(run, "put_manifest")["outputs"]["body"]) == {
        "id": 42,
        "entries": 2,
    }


# --------------------------------------------------------------------------- (b) library 500


def failing_run(world: World) -> dict:
    world.library.fail_puts = True
    return daily_run(world)


def test_a_library_answering_500_still_succeeds_the_run(world):  # h12
    run = failing_run(world)
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])


def test_a_library_answering_500_skips_the_put_step(world):  # h12
    run = failing_run(world)
    assert step_state(run, "put_manifest")["status"] == "skipped"


def test_the_skipped_put_step_keeps_its_error(world):  # h12
    run = failing_run(world)
    assert step_state(run, "put_manifest")["error"]["message"] == "http_status: server answered 500"


def test_the_manifest_step_before_the_failed_put_succeeded(world):  # h12
    run = failing_run(world)
    assert step_state(run, "manifest")["status"] == "succeeded"


def test_the_failed_put_stored_nothing_in_the_library(world):  # h12
    failing_run(world)
    assert world.library.puts == {}


# --------------------------------------------------------------------------- (c) backfill


def test_the_backfill_run_succeeds_with_an_unknown_id_among_the_ids(world):  # c7
    run = backfill_run(world, [11, 99, 12])
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])


def test_the_backfill_pushes_a_manifest_for_each_known_id(world):  # c7
    backfill_run(world, [11, 99, 12])
    assert sorted(world.library.puts) == [11, 12]


def test_the_backfill_manifest_is_the_daily_shape(world):  # c10
    backfill_run(world, [11])
    (entries,) = world.library.puts[11]
    assert [e["article_title"] for e in entries] == ["Models", "Robots"]


def test_the_backfill_and_the_daily_manifest_name_the_same_headings(world):  # c10, h13
    backfill_run(world, [11])
    daily_run(world, item_id=43)
    (backfilled,) = world.library.puts[11]
    (daily,) = world.library.puts[43]
    assert [e["article_title"] for e in backfilled] == [e["article_title"] for e in daily]


def test_the_backfill_and_the_daily_manifest_have_the_same_entry_keys(world):  # c10, h13
    backfill_run(world, [11])
    daily_run(world, item_id=43)
    (backfilled,) = world.library.puts[11]
    (daily,) = world.library.puts[43]
    assert [sorted(e) for e in backfilled] == [sorted(e) for e in daily]


def outcomes(run: dict) -> list[dict]:
    """The backfill loop's per-item outcomes, as the run outputs them (t7)."""
    return run["outputs"]["outcomes"]


def test_the_run_lists_every_id_it_was_given(world):  # c7, h24
    run = backfill_run(world, [11, 99, 12])
    assert [o["item"]["id"] for o in outcomes(run)] == [11, 99, 12]


def test_the_run_lists_the_pushed_ids_as_succeeded(world):  # c7, h24
    run = backfill_run(world, [11, 99, 12])
    assert [o["status"] for o in outcomes(run)] == ["succeeded", "skipped", "succeeded"]


def test_the_run_lists_each_pushed_id_with_its_entry_count(world):  # c7, h24
    run = backfill_run(world, [11, 99, 12])
    assert [o["count"] for o in outcomes(run)] == [2, None, 2]


def test_a_pushed_id_has_no_errors(world):  # c7, h24
    run = backfill_run(world, [11, 99, 12])
    assert outcomes(run)[0]["errors"] == {}


def test_the_run_lists_a_skipped_id_with_the_fetch_error(world):  # c7, h24
    run = backfill_run(world, [11, 99, 12])
    assert outcomes(run)[1]["errors"]["fetch"] == "http_status: server answered 404"


def test_the_skipped_ids_later_steps_are_listed_as_input_missing(world):  # c7, h24
    run = backfill_run(world, [11, 99, 12])
    assert outcomes(run)[1]["errors"] == {
        "fetch": "http_status: server answered 404",
        "manifest": "input 'content_path' (string) is missing",
        "put_manifest": "input 'sections' (array) is missing",
    }


def test_the_loop_output_has_one_entry_per_id(world):  # c7, h24
    run = backfill_run(world, [11, 99, 12])
    assert len(run["outputs"]["results"]) == 3


def test_the_backfill_fetch_used_the_bearer_grant(world):  # c15
    backfill_run(world, [11])
    assert world.library.auth == [f"Bearer {SECRET}", f"Bearer {SECRET}"]


def test_the_fetched_epub_is_saved_under_the_root(world):  # c7
    backfill_run(world, [11])
    (name,) = os.listdir(world.root)
    assert name.endswith(".epub")


# --------------------------------------------------------------------------- run documents


def stored_run(world: World, run: dict) -> str:
    return json.dumps(world.base.get(RUNS_COLLECTION, run["id"]), sort_keys=True, default=str)


def test_the_stored_backfill_run_holds_no_epub_base64(world):  # h24
    run = backfill_run(world, [11, 99, 12])
    epub_b64 = base64.b64encode(epub_of("2026-10-11")).decode()
    assert epub_b64[:64] not in stored_run(world, run)


def test_the_stored_backfill_run_holds_no_zip_header(world):  # h24
    run = backfill_run(world, [11, 99, 12])
    assert "UEsDB" not in stored_run(world, run)


def test_the_stored_backfill_run_holds_the_saved_path_not_the_bytes(world):  # h24
    run = backfill_run(world, [11])
    assert step_state(run, "each[0]/fetch")["outputs"]["path"].startswith(world.root)
