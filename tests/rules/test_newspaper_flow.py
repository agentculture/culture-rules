"""#53 t8: the shipped daily newspaper, end to end on fakes (h13, h10, h17, h9, h8).

One node (spark) runs the shipped bundle (docs/rules/newspaper) with its rule enabled, the
operator's seeding applied (the recipient on the email actor's allowlist and in
``newspaper_to``, the device id in ``newspaper_device``) and the real ports - email.send,
search.web, llm.complete, library.push and the newspaper builtins - on transport doubles of
Resend, Brave, the cortex gateway and xteink. The scheduler fires the 06:30 Asia/Jerusalem
slot; the run searches each section, summarises it, renders the issue, sends one email and
queues one item, and records the date.
"""

from __future__ import annotations

import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from culture_rules.apps.resend import RESEND_EMAILS_URL
from culture_rules.engine.runs import RUNS_COLLECTION, step_key, step_state
from culture_rules.node.actions.email import EmailSendPort
from culture_rules.node.actions.library import LibraryPushPort
from culture_rules.node.actions.llm import NEWSPAPER_BRIEF, LlmCompletePort
from culture_rules.node.actions.newspaper import (
    FILTER_BUILTIN,
    ISSUE_BUILTIN,
    ISSUES_COLLECTION,
    RENDER_BUILTIN,
    SECTIONS_BUILTIN,
    NewspaperFilterPort,
    NewspaperIssuePort,
    NewspaperRenderPort,
    NewspaperSectionsPort,
)
from culture_rules.node.actions.newspaper_render import SUMMARY_UNAVAILABLE
from culture_rules.node.actions.search import SearchWebPort
from culture_rules.node.daemon import Node
from culture_rules.node.runner import BuiltinCodePort, NoopAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.node.test_node import BEATS
from tests.rules.test_newspaper_bundle import (
    EMAIL,
    RULE,
    VARIABLES,
    bundle,
)

READER = "reader@example.test"
DEVICE = 3
ITEM = 7
SENDER = "newsletter-no-reply@culture.dev"
SECTIONS = [
    {"name": "Edge AI", "queries": ["edge ai", "jetson"], "freshness": "pw", "count": 3},
    {"name": "Robots", "queries": ["humanoid robot"]},
]
TODAY = datetime(2026, 10, 11, 3, 25, tzinfo=UTC)  # 06:25 in Jerusalem (IDT, UTC+3)
SUBJECT = "Daily newspaper 2026-10-11"
POST = "POST"


# --------------------------------------------------------------------------- the fakes


class Brave:
    """Brave's web search: three results per query, distinct per query."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def __call__(self, method, url, headers, body, timeout):
        query = parse_qs(urlsplit(url).query)["q"][0]
        self.queries.append(query)
        slug = query.replace(" ", "-")
        results = [
            {
                "title": f"{query} story {n}",
                "url": f"https://news.example/{slug}/{n}",
                "description": f"What happened in {query} ({n})",
                "meta_url": {"hostname": "news.example"},
                "age": "2 hours ago",
            }
            for n in range(3)
        ]
        return 200, json.dumps({"web": {"results": results}}).encode()


class Cortex:
    """The gateway's chat completions: one bullet linking each given result, or down."""

    def __init__(self) -> None:
        self.down = False
        self.requests: list[dict] = []

    def __call__(self, url, data, headers, timeout):
        request = json.loads(data)
        self.requests.append({"url": url, "json": request, "headers": dict(headers)})
        if self.down:
            raise ConnectionRefusedError("gateway down")
        results = json.loads(request["messages"][1]["content"])
        text = "\n".join(f"- [{r['title']}]({r['url']}): {r['snippet']}" for r in results)
        reply = {
            "model": request["model"],
            "choices": [{"message": {"role": "assistant", "content": text or "No results."}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        return 200, json.dumps(reply).encode()


class Resend:
    """Resend's POST /emails: records each send, deduplicating on Idempotency-Key."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.keys: dict[str, str] = {}

    def __call__(self, method, url, headers, body, timeout):
        key = headers.get("Idempotency-Key")
        if key not in self.keys:
            self.sent.append({"url": url, "headers": dict(headers), "json": json.loads(body)})
            self.keys[key] = f"msg-{len(self.sent)}"
        return 200, json.dumps({"id": self.keys[key]}).encode()


class Xteink:
    """xteink's library upload and device queue."""

    def __init__(self) -> None:
        self.uploads: list[bytes] = []
        self.queued: list[tuple[str, dict]] = []

    def __call__(self, method, url, headers, body, timeout):
        path = urlsplit(url).path
        if path == "/api/library":
            self.uploads.append(body)
            return 201, json.dumps({"item": {"id": ITEM}, "created": True}).encode()
        self.queued.append((path, json.loads(body)))
        return 201, json.dumps({"id": 11, "device_id": DEVICE, "item_id": ITEM}).encode()


# --------------------------------------------------------------------------- the world


def seeded_actors() -> list[dict]:
    """The shipped actors with the operator's seeding: the recipient on the allowlist."""
    docs = []
    for actor in bundle().actors:
        doc = copy.deepcopy(actor.to_dict())
        if actor.id == EMAIL:
            doc["params"]["connection"]["recipients"] = [READER]
        docs.append(doc)
    return docs


class World:
    """spark running the shipped newspaper on fakes, from ``start``."""

    def __init__(self, start: datetime = TODAY, *, cron: str | None = None) -> None:
        self.clock = Clock(start)
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        for doc in seeded_actors():
            self.base.put("actors", doc)
        for wf in bundle().workflows:
            self.base.put("workflows", wf.to_dict())
        for r in bundle().rules:
            doc = {**r.to_dict(), "enabled": True}
            if cron is not None:
                doc["trigger"] = {**doc["trigger"], "params": {**doc["trigger"]["params"]}}
                doc["trigger"]["params"]["cron"] = cron
            self.base.put("rules", doc)
        shipped = json.loads(VARIABLES.read_text())
        values = {name: item["value"] for name, item in shipped.items()}
        values.update(
            newspaper_sections=json.dumps(SECTIONS), newspaper_to=[READER], newspaper_device=DEVICE
        )
        for name, value in values.items():
            self.base.put_variable(name, value, updated_by="test")
        self.brave, self.cortex = Brave(), Cortex()
        self.resend, self.xteink = Resend(), Xteink()
        self.node = Node(
            self.base.peer(),
            "spark",
            actors=self.ports(),
            clock=self.clock,
            heartbeat_options=BEATS,
        )
        self.node.start()

    def ports(self) -> dict:
        base, clock = self.base, self.clock

        def fake_secret(_ref):
            return "fake-key-value"

        return {
            "action:noop": NoopAction(),
            "action:search.web": SearchWebPort(base, transport=self.brave, secrets=fake_secret),
            "action:llm.complete": LlmCompletePort(
                base, transport=self.cortex, secret_runner=fake_secret, clock=clock
            ),
            "action:email.send": EmailSendPort(base, transport=self.resend, secrets=fake_secret),
            "action:library.push": LibraryPushPort(
                base, transport=self.xteink, secret_runner=fake_secret, clock=clock
            ),
            "code": BuiltinCodePort(
                {
                    SECTIONS_BUILTIN: NewspaperSectionsPort(),
                    FILTER_BUILTIN: NewspaperFilterPort(),
                    RENDER_BUILTIN: NewspaperRenderPort(),
                    ISSUE_BUILTIN: NewspaperIssuePort(base, clock=clock),
                }
            ),
        }

    def run_until(self, end: datetime, step_s: float = 60) -> None:
        while self.clock() < end:
            self.clock.advance(step_s)
            report = self.node.run_once()
            assert report.errors == [], report.errors

    def runs(self) -> list[dict]:
        docs = self.base.find(RUNS_COLLECTION, {"rule_id": RULE})
        return sorted(docs, key=lambda d: d["created_at"])

    def html(self, n: int = 0) -> str:
        return self.resend.sent[n]["json"]["html"]


def finished(world: World) -> dict:
    (run,) = world.runs()
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])
    return run


# --------------------------------------------------------------------------- the morning


def test_the_0630_slot_sends_one_email_and_queues_one_item():  # h13
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    run = finished(world)
    assert run["trigger"]["data"]["slot"] == "2026-10-11T03:30:00+00:00"
    assert len(world.resend.sent) == 1
    assert len(world.xteink.uploads) == 1
    assert world.xteink.queued == [(f"/api/devices/{DEVICE}/queue", {"item_id": ITEM})]


def test_the_email_goes_from_newsletter_no_reply_to_the_seeded_reader():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    (mail,) = world.resend.sent
    assert mail["url"] == RESEND_EMAILS_URL
    assert mail["json"]["from"] == SENDER
    assert mail["json"]["to"] == [READER]
    assert mail["json"]["subject"] == SUBJECT
    assert mail["headers"]["Idempotency-Key"]


def test_the_run_outputs_carry_the_message_and_item_ids():  # h17
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    outputs = finished(world)["outputs"]
    assert outputs == {"message_id": "msg-1", "item_id": ITEM, "date": "2026-10-11"}
    issue = world.base.get(ISSUES_COLLECTION, "2026-10-11")
    assert issue["state"] == "delivered"
    assert issue["message_id"] == "msg-1"


def test_each_section_is_searched_once_with_its_queries_joined():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    assert world.brave.queries == ["edge ai OR jetson", "humanoid robot"]


def test_the_summaries_come_from_cortex_with_the_locked_brief_and_no_tools():  # c11
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    assert len(world.cortex.requests) == 2
    request = world.cortex.requests[0]["json"]
    assert "tools" not in request
    assert request["model"] == "cortex-spark2"
    assert request["max_tokens"] == 2000
    assert request["messages"][0]["content"] == NEWSPAPER_BRIEF


def test_every_summarised_link_is_a_result_and_the_summary_is_shown():  # h5
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    html = world.html()
    assert '<a href="https://news.example/edge-ai-OR-jetson/0">' in html
    assert '<a href="https://news.example/humanoid-robot/2">' in html
    assert SUMMARY_UNAVAILABLE not in html


def test_the_epub_is_the_dated_article_upload():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    (upload,) = world.xteink.uploads
    assert b'name="kind"\r\n\r\narticle' in upload
    assert SUBJECT.encode() in upload
    assert b"application/epub+zip" in upload


# --------------------------------------------------------------------------- one per date


def test_a_second_run_for_the_same_date_sends_nothing():  # h8
    world = World(cron="30,45 6 * * *")
    world.run_until(TODAY + timedelta(minutes=40))
    first, second = world.runs()
    assert second["status"] == "succeeded"
    assert step_state(second, "claim")["outputs"]["reason"] == "already_delivered"
    assert len(world.resend.sent) == 1
    assert len(world.xteink.uploads) == 1


def test_the_repeat_run_spends_no_search_and_no_summary():
    world = World(cron="30,45 6 * * *")
    world.run_until(TODAY + timedelta(minutes=40))
    assert len(world.brave.queries) == 2
    assert len(world.cortex.requests) == 2
    second = world.runs()[1]
    for key in ("email", "push", "record", step_key("search", 0, "search_web")):
        assert step_state(second, key)["status"] == "skipped", key


# --------------------------------------------------------------------------- cortex down


def test_cortex_down_still_sends_the_results_only_issue():  # q1, c25
    world = World()
    world.cortex.down = True
    world.run_until(TODAY + timedelta(minutes=30))
    run = finished(world)
    assert len(world.resend.sent) == 1
    assert len(world.xteink.queued) == 1
    html = world.html()
    assert html.count(SUMMARY_UNAVAILABLE) == 2
    assert '<a href="https://news.example/humanoid-robot/0">' in html
    assert run["outputs"]["message_id"] == "msg-1"


def test_cortex_down_is_recorded_on_the_skipped_summary_steps():
    world = World()
    world.cortex.down = True
    world.run_until(TODAY + timedelta(minutes=30))
    run = finished(world)
    summary = step_state(run, step_key("summarise", 0, "summary"))
    assert summary["status"] == "skipped"
    assert "llm_unavailable" in summary["error"]["message"]
    assert len(world.cortex.requests) == 4  # two sections, two attempts each


# --------------------------------------------------------------------------- DST (h9)


def test_slots_across_the_dst_change_map_to_their_local_dates():
    start = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)  # IDT ends 2026-10-25 02:00 local
    world = World(start)
    world.run_until(datetime(2026, 10, 25, 5, 0, tzinfo=UTC), step_s=300)
    runs = world.runs()
    assert [r["trigger"]["data"]["slot"] for r in runs] == [
        "2026-10-24T03:30:00+00:00",  # 06:30 IDT (UTC+3)
        "2026-10-25T04:30:00+00:00",  # 06:30 IST (UTC+2)
    ]
    assert [r["outputs"]["date"] for r in runs] == ["2026-10-24", "2026-10-25"]
    subjects = [mail["json"]["subject"] for mail in world.resend.sent]
    assert subjects == ["Daily newspaper 2026-10-24", "Daily newspaper 2026-10-25"]


def test_the_schedule_trigger_carries_the_rule_tz():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    assert finished(world)["trigger"]["data"]["tz"] == "Asia/Jerusalem"


def test_the_epub_round_trips_from_the_render():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    render = step_state(finished(world), "render")
    assert base64.b64decode(render["outputs"]["epub_base64"])[:2] == b"PK"
