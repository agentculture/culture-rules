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

from culture_rules.actors.limits import USAGE_COLLECTION, pool_doc_id
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
from culture_rules.node.actors import ACTORS_COLLECTION
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
OUTPUTS = "outputs"
MSG_1 = "msg-1"
MODEL = "model"
STATUS = "status"
HEADERS = "headers"
MESSAGE_ID = "message_id"
ITEM_ID = "item_id"
CONTENT = "content"
TRIGGER = "trigger"
PARAMS = "params"
IDEM_HEADER = "Idempotency-Key"
POOL = "qwen-spark2"
KEY_WINDOW = timedelta(hours=24)


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
        self.requests.append({"url": url, "json": request, HEADERS: dict(headers)})
        if self.down:
            raise ConnectionRefusedError("gateway down")
        results = json.loads(request["messages"][1][CONTENT])
        text = "\n".join(f"- [{r['title']}]({r['url']}): {r['snippet']}" for r in results)
        reply = {
            MODEL: request[MODEL],
            "choices": [{"message": {"role": "assistant", CONTENT: text or "No results."}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        return 200, json.dumps(reply).encode()


class Resend:
    """Resend's POST /emails: deduplicates on Idempotency-Key for 24 hours (then the key is
    forgotten), answers a reused live key with a different body 409, and with ``drop_acks``
    sends the email but loses the answer (Codex r2 #1, #4)."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.sent: list[dict] = []
        self.keys: dict[str, tuple] = {}
        self.calls = 0
        self.drop_acks = False

    def __call__(self, method, url, headers, body, timeout):
        self.calls += 1
        key, now = headers.get(IDEM_HEADER), self.clock()
        live = self.keys.get(key)
        if live is None or now - live[1] >= KEY_WINDOW:
            self.sent.append({"url": url, HEADERS: dict(headers), "json": json.loads(body)})
            live = self.keys[key] = (f"msg-{len(self.sent)}", now, body)
        elif live[2] != body:
            return 409, b'{"name": "invalid_idempotent_request"}'
        if self.drop_acks:
            raise TimeoutError("the answer was lost")
        return 200, json.dumps({"id": live[0]}).encode()


class Xteink:
    """xteink's library upload and device queue."""

    def __init__(self) -> None:
        self.uploads: list[bytes] = []
        self.queued: list[tuple[str, dict]] = []
        self.down = False

    def __call__(self, method, url, headers, body, timeout):
        if self.down:
            return 503, b'{"detail": "down"}'
        path = urlsplit(url).path
        if path == "/api/library":
            self.uploads.append(body)
            return 201, json.dumps({"item": {"id": ITEM}, "created": True}).encode()
        self.queued.append((path, json.loads(body)))
        return 201, json.dumps({"id": 11, "device_id": DEVICE, ITEM_ID: ITEM}).encode()


# --------------------------------------------------------------------------- the world


def seeded_actors() -> list[dict]:
    """The shipped actors with the operator's seeding: the recipient on the allowlist."""
    docs = []
    for actor in bundle().actors:
        doc = copy.deepcopy(actor.to_dict())
        if actor.id == EMAIL:
            doc[PARAMS]["connection"]["recipients"] = [READER]
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
                doc[TRIGGER] = {**doc[TRIGGER], PARAMS: {**doc[TRIGGER][PARAMS]}}
                doc[TRIGGER][PARAMS]["cron"] = cron
            self.base.put("rules", doc)
        shipped = json.loads(VARIABLES.read_text())
        values = {name: item["value"] for name, item in shipped.items()}
        values.update(
            newspaper_sections=json.dumps(SECTIONS), newspaper_to=[READER], newspaper_device=DEVICE
        )
        for name, value in values.items():
            self.base.put_variable(name, value, updated_by="test")
        self.brave, self.cortex = Brave(), Cortex()
        self.resend, self.xteink = Resend(self.clock), Xteink()
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
    assert run[STATUS] == "succeeded", (run.get("error"), run["steps"])
    return run


# --------------------------------------------------------------------------- the morning


def test_the_0630_slot_sends_one_email_and_queues_one_item():  # h13
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    run = finished(world)
    assert run[TRIGGER]["data"]["slot"] == "2026-10-11T03:30:00+00:00"
    assert len(world.resend.sent) == 1
    assert len(world.xteink.uploads) == 1
    assert world.xteink.queued == [(f"/api/devices/{DEVICE}/queue", {ITEM_ID: ITEM})]


def test_the_email_goes_from_newsletter_no_reply_to_the_seeded_reader():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    (mail,) = world.resend.sent
    assert mail["url"] == RESEND_EMAILS_URL
    assert mail["json"]["from"] == SENDER
    assert mail["json"]["to"] == [READER]
    assert mail["json"]["subject"] == SUBJECT
    assert mail[HEADERS][IDEM_HEADER]


def test_the_run_outputs_carry_the_message_and_item_ids():  # h17
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    outputs = finished(world)[OUTPUTS]
    expected = {MESSAGE_ID: MSG_1, ITEM_ID: ITEM, "date": "2026-10-11", "email_uncertain": False}
    assert outputs == expected
    issue = world.base.get(ISSUES_COLLECTION, "2026-10-11")
    assert issue["state"] == "delivered"
    assert issue[MESSAGE_ID] == MSG_1


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
    assert request[MODEL] == "cortex-spark2"
    assert request["max_tokens"] == 2000
    assert request["messages"][0][CONTENT] == NEWSPAPER_BRIEF


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
    assert second[STATUS] == "succeeded"
    assert step_state(second, "claim")[OUTPUTS]["reason"] == "already_delivered"
    assert len(world.resend.sent) == 1
    assert len(world.xteink.uploads) == 1


def test_the_repeat_run_spends_no_search_and_no_summary():
    world = World(cron="30,45 6 * * *")
    world.run_until(TODAY + timedelta(minutes=40))
    assert len(world.brave.queries) == 2
    assert len(world.cortex.requests) == 2
    second = world.runs()[1]
    for key in ("email", "push", "record", step_key("search", 0, "search_web")):
        assert step_state(second, key)[STATUS] == "skipped", key


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
    assert run[OUTPUTS][MESSAGE_ID] == MSG_1


def test_cortex_down_is_recorded_on_the_skipped_summary_steps():
    world = World()
    world.cortex.down = True
    world.run_until(TODAY + timedelta(minutes=30))
    run = finished(world)
    summary = step_state(run, step_key("summarise", 0, "summary"))
    assert summary[STATUS] == "skipped"
    assert "llm_unavailable" in summary["error"]["message"]
    assert len(world.cortex.requests) == 4  # two sections, two attempts each


# --------------------------------------------------------------------------- DST (h9)


def test_slots_across_the_dst_change_map_to_their_local_dates():
    start = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)  # IDT ends 2026-10-25 02:00 local
    world = World(start)
    world.run_until(datetime(2026, 10, 25, 5, 0, tzinfo=UTC), step_s=300)
    runs = world.runs()
    assert [r[TRIGGER]["data"]["slot"] for r in runs] == [
        "2026-10-24T03:30:00+00:00",  # 06:30 IDT (UTC+3)
        "2026-10-25T04:30:00+00:00",  # 06:30 IST (UTC+2)
    ]
    assert [r[OUTPUTS]["date"] for r in runs] == ["2026-10-24", "2026-10-25"]
    subjects = [mail["json"]["subject"] for mail in world.resend.sent]
    assert subjects == ["Daily newspaper 2026-10-24", "Daily newspaper 2026-10-25"]


def test_the_schedule_trigger_carries_the_rule_tz():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    assert finished(world)[TRIGGER]["data"]["tz"] == "Asia/Jerusalem"


def test_the_epub_round_trips_from_the_render():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    render = step_state(finished(world), "render")
    assert base64.b64decode(render[OUTPUTS]["epub_base64"])[:2] == b"PK"


# --------------------------------------------------------------------------- partial delivery


TWICE = "30 6,8 * * *"  # a second slot two hours on: past the first run's one-hour lease
SECOND_SLOT = datetime(2026, 10, 11, 5, 30, tzinfo=UTC)


def partial_then_retry() -> World:
    """Codex r1 #1: the 06:30 run emails, xteink is down so its push fails, and the 08:30
    run takes the date over once the first run's lease has run out."""
    world = World(cron=TWICE)
    world.xteink.down = True
    world.run_until(SECOND_SLOT - timedelta(minutes=10))
    world.xteink.down = False
    world.run_until(SECOND_SLOT + timedelta(minutes=20))
    return world


def test_a_takeover_after_a_failed_push_sends_no_second_email():
    world = partial_then_retry()
    assert world.resend.calls == 1


def test_a_takeover_after_a_failed_push_retries_only_the_push():
    world = partial_then_retry()
    first, second = world.runs()
    assert (first[STATUS], second[STATUS]) == ("failed", "succeeded")
    assert step_state(second, "email")[STATUS] == "skipped"
    assert len(world.xteink.queued) == 1


def test_a_takeover_after_a_failed_push_records_the_first_message():
    world = partial_then_retry()
    second = world.runs()[1]
    assert second[OUTPUTS][MESSAGE_ID] == MSG_1
    issue = world.base.get(ISSUES_COLLECTION, "2026-10-11")
    assert (issue["state"], issue["email"][MESSAGE_ID]) == ("delivered", MSG_1)


def test_the_email_carries_the_durable_per_date_key():
    world = World()
    world.run_until(TODAY + timedelta(minutes=20))
    (mail,) = world.resend.sent
    assert mail[HEADERS][IDEM_HEADER] == "newspaper:2026-10-11:email"


# --------------------------------------------------------------------------- a busy pool


def hold_the_pool(world: World) -> None:
    """A PR fix holds the qwen-spark2 pool's only slot all morning (Codex r1 #3)."""
    until = (TODAY + timedelta(hours=6)).isoformat()
    world.base.put(
        USAGE_COLLECTION,
        {
            "id": pool_doc_id(POOL),
            "pool": POOL,
            "rev": 1,
            "inflight": [{"key": "fix-run", "until": until, "actor": "qwen-fixer"}],
        },
    )


def test_a_busy_pool_still_sends_the_issue_by_0645():
    world = World()
    hold_the_pool(world)
    world.run_until(TODAY + timedelta(minutes=20))  # 06:45 in Jerusalem
    assert len(world.resend.sent) == 1


def test_a_busy_pool_sends_the_results_only_issue():
    world = World()
    hold_the_pool(world)
    world.run_until(TODAY + timedelta(minutes=20))
    assert finished(world)[OUTPUTS][MESSAGE_ID] == MSG_1
    assert world.html().count(SUMMARY_UNAVAILABLE) == 2


def test_a_busy_pool_never_reaches_cortex():
    world = World()
    hold_the_pool(world)
    world.run_until(TODAY + timedelta(minutes=20))
    assert world.cortex.requests == []


def test_a_busy_pool_records_the_llm_deadline_on_the_summary():
    world = World()
    hold_the_pool(world)
    world.run_until(TODAY + timedelta(minutes=20))
    summary = step_state(finished(world), step_key("summarise", 1, "summary"))
    assert "llm_deadline" in summary["error"]["message"]


# --------------------------------------------------------------------------- lost acknowledgements

OTHER_READER = "other@example.test"
ONCE = "30 6 11 10 *"  # only the 2026-10-11 slot
WORKFLOW = "daily-newspaper"
FIRST_SLOT = "2026-10-11T03:30:00+00:00"


def lost_ack(world: World) -> None:
    """The 06:30 run's email reaches Resend but every answer is lost: the run fails at the
    email step with the date claimed, its email accepted and unrecorded."""
    world.resend.drop_acks = True
    world.run_until(TODAY + timedelta(minutes=30))
    world.resend.drop_acks = False


def rerun(world: World, after: timedelta) -> dict:
    """The operator re-runs the 2026-10-11 issue ``after`` the first slot."""
    world.run_until(TODAY + after, step_s=600)
    inputs = dict(
        sections=json.dumps(SECTIONS),
        to=[READER],
        slot=FIRST_SLOT,
        tz="Asia/Jerusalem",
        device_id=DEVICE,
    )
    run = world.node.executor.start_workflow(WORKFLOW, inputs, "operator")
    world.run_until(TODAY + after + timedelta(minutes=30))
    return world.base.get(RUNS_COLLECTION, run["id"])


def test_a_lost_ack_leaves_the_first_run_failed_with_one_email_sent():
    world = World(cron=ONCE)
    lost_ack(world)
    (first,) = world.runs()
    assert (first[STATUS], len(world.resend.sent)) == ("failed", 1)


def test_a_rerun_25_hours_after_a_lost_ack_sends_no_second_email():  # Codex r2 #1
    world = World(cron=ONCE)
    lost_ack(world)
    rerun(world, timedelta(hours=25))
    assert len(world.resend.sent) == 1


def test_a_rerun_25_hours_after_a_lost_ack_reports_the_email_uncertain():
    world = World(cron=ONCE)
    lost_ack(world)
    run = rerun(world, timedelta(hours=25))
    assert (run[STATUS], run[OUTPUTS]["email_uncertain"]) == ("succeeded", True)


def test_a_rerun_25_hours_after_a_lost_ack_still_queues_the_issue():
    world = World(cron=ONCE)
    lost_ack(world)
    rerun(world, timedelta(hours=25))
    assert len(world.xteink.queued) == 1


def test_a_rerun_within_the_window_recovers_the_first_message():
    world = World(cron=ONCE)
    lost_ack(world)
    run = rerun(world, timedelta(hours=3))
    assert (run[OUTPUTS][MESSAGE_ID], len(world.resend.sent)) == (MSG_1, 1)


def change_the_reader(world: World) -> None:
    """The operator moves newspaper_to to another (allowlisted) reader and renames the
    sender after the 06:30 email was accepted but not recorded."""
    actor = world.base.get(ACTORS_COLLECTION, EMAIL)
    connection = actor[PARAMS]["connection"]
    connection["recipients"] = [READER, OTHER_READER]
    connection["local_part"] = "news"
    world.base.put(ACTORS_COLLECTION, actor)
    world.base.put_variable("newspaper_to", [OTHER_READER], updated_by="test")


def changed_after_a_lost_ack() -> World:
    world = World(cron=TWICE)
    lost_ack(world)
    change_the_reader(world)
    world.run_until(SECOND_SLOT + timedelta(minutes=30))
    return world


def test_changed_recipients_after_a_lost_ack_recover_the_first_message():  # Codex r2 #4
    world = changed_after_a_lost_ack()
    second = world.runs()[1]
    assert (second[STATUS], second[OUTPUTS][MESSAGE_ID]) == ("succeeded", MSG_1)


def test_changed_recipients_after_a_lost_ack_send_no_second_email():
    world = changed_after_a_lost_ack()
    assert len(world.resend.sent) == 1


def test_the_recovery_replays_the_frozen_recipients_and_sender():
    world = changed_after_a_lost_ack()
    (mail,) = world.resend.sent
    assert (mail["json"]["to"], mail["json"]["from"]) == ([READER], SENDER)


def test_the_fake_resend_refuses_a_reused_key_with_another_body():
    resend = Resend(Clock(TODAY))
    resend("POST", RESEND_EMAILS_URL, {IDEM_HEADER: "k"}, b'{"to": ["a"]}', 5)
    status, _ = resend("POST", RESEND_EMAILS_URL, {IDEM_HEADER: "k"}, b'{"to": ["b"]}', 5)
    assert status == 409
