"""#53 t6: the newspaper builtins ``sections``, ``filter`` and ``issue``.

``newspaper.sections`` parses the ``newspaper_sections`` variable (a JSON string) and fails the
run naming the first invalid field (c12, h11). ``newspaper.filter`` applies a section's
must/must-not terms and site lists to its search results. ``newspaper.issue`` turns the
schedule slot into the local date and guards one issue per date (c15, h8).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.email import EmailSendPort
from culture_rules.node.actions.newspaper import (
    DEFAULT_COUNT,
    FILTER_BUILTIN,
    ISSUE_BUILTIN,
    ISSUES_COLLECTION,
    RENDER_BUILTIN,
    SECTIONS_BUILTIN,
    NewspaperFilterPort,
    NewspaperIssuePort,
    NewspaperSectionsPort,
    SectionsError,
    filter_results,
    local_date,
    parse_sections,
)
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

DEADLINE = datetime(2026, 10, 11, 23, tzinfo=UTC)
JERUSALEM = "Asia/Jerusalem"
SLOT = "2026-10-11T03:30:00+00:00"  # 06:30 in Jerusalem (IDT, UTC+3)
DATE = "2026-10-11"
CLAIM = "claim"


def ctx(config=None, run="r1"):
    return InvocationContext(
        run_id=run, step_id="news", kind="code", host="spark", config=config or {}
    )


def section(**extra):
    return {"name": "AI", "queries": ["edge ai"], **extra}


def sections_json(*items):
    return json.dumps(list(items))


def error_of(raw):
    with pytest.raises(SectionsError) as info:
        parse_sections(raw)
    return str(info.value)


# ------------------------------------------------------------------ builtin names


def test_builtin_names():
    names = (SECTIONS_BUILTIN, FILTER_BUILTIN, RENDER_BUILTIN, ISSUE_BUILTIN)
    assert names == (
        "newspaper.sections",
        "newspaper.filter",
        "newspaper.render",
        "newspaper.issue",
    )


def test_collection_name():
    assert ISSUES_COLLECTION == "newspaper_issues"


# ------------------------------------------------------------------ newspaper.sections


def test_sections_normalised_with_defaults():
    parsed = parse_sections(sections_json(section()))
    assert parsed == [
        {
            "name": "AI",
            "queries": ["edge ai"],
            "must": [],
            "must_not": [],
            "freshness": None,
            "count": DEFAULT_COUNT,
            "site_include": [],
            "site_exclude": [],
        }
    ]


def test_sections_keeps_every_given_field():
    full = section(
        must=["jetson"],
        must_not=["rumour"],
        freshness="pd",
        count=7,
        site_include=["nvidia.com"],
        site_exclude=["Spam.Example"],
    )
    parsed = parse_sections(sections_json(full))[0]
    assert parsed == {
        "name": "AI",
        "queries": ["edge ai"],
        "must": ["jetson"],
        "must_not": ["rumour"],
        "freshness": "pd",
        "count": 7,
        "site_include": ["nvidia.com"],
        "site_exclude": ["spam.example"],
    }


def test_bad_json_names_the_variable():
    assert error_of("[{").startswith("newspaper_sections: not valid JSON")


def test_not_a_list():
    assert error_of('{"name": "x"}').startswith("sections: ")


def test_empty_list():
    assert error_of("[]").startswith("sections: ")


def test_too_many_sections():
    assert error_of(sections_json(*[section()] * 21)).startswith("sections: ")


def test_section_not_an_object():
    assert error_of(sections_json(section(), "AI")).startswith("sections[1]: ")


def test_missing_name():
    assert error_of(sections_json({"queries": ["x"]})).startswith("sections[0].name: ")


def test_blank_name():
    assert error_of(sections_json(section(name="  "))).startswith("sections[0].name: ")


def test_long_name():
    assert error_of(sections_json(section(name="n" * 81))).startswith("sections[0].name: ")


def test_unknown_key_refused():
    message = error_of(sections_json(section(), section(colour="red")))
    assert message.startswith("sections[1].colour: unknown")


def test_empty_queries_names_the_index():
    message = error_of(sections_json(section(), section(), section(queries=[])))
    assert message.startswith("sections[2].queries: ")


def test_queries_missing():
    assert error_of(sections_json({"name": "AI"})).startswith("sections[0].queries: ")


def test_too_many_queries():
    assert error_of(sections_json(section(queries=["q"] * 6))).startswith("sections[0].queries")


def test_blank_query_names_its_index():
    message = error_of(sections_json(section(queries=["ok", " "])))
    assert message.startswith("sections[0].queries[1]: ")


def test_must_not_a_list():
    assert error_of(sections_json(section(must="jetson"))).startswith("sections[0].must: ")


def test_must_not_item_not_a_string():
    message = error_of(sections_json(section(must_not=["ok", 3])))
    assert message.startswith("sections[0].must_not[1]: ")


@pytest.mark.parametrize("value", ["daily", "", 1])
def test_bad_freshness(value):
    message = error_of(sections_json(section(freshness=value)))
    assert message.startswith("sections[0].freshness: ")


@pytest.mark.parametrize("value", [0, 21, "5", True, 2.5])
def test_bad_count(value):
    assert error_of(sections_json(section(count=value))).startswith("sections[0].count: ")


@pytest.mark.parametrize("host", ["not a host", "http://x.com", "", "a..b"])
def test_bad_site_include(host):
    message = error_of(sections_json(section(site_include=[host])))
    assert message.startswith("sections[0].site_include[0]: ")


def test_bad_site_exclude():
    message = error_of(sections_json(section(site_exclude="x.com")))
    assert message.startswith("sections[0].site_exclude: ")


def test_sections_port_completes():
    port = NewspaperSectionsPort()
    result = port.invoke({"sections": sections_json(section())}, "k", DEADLINE, context=ctx())
    assert result.outcome == COMPLETED
    assert result.output["sections"][0]["name"] == "AI"


def test_sections_port_fails_non_retryably_naming_the_field():
    port = NewspaperSectionsPort()
    raw = sections_json(section(), section(queries=[]))
    result = port.invoke({"sections": raw}, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert result.retryable is False
    assert "sections[1].queries" in result.error


def test_sections_port_missing_input():
    result = NewspaperSectionsPort().invoke({}, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert "newspaper_sections" in result.error


def test_sections_port_adds_the_search_query():
    # t8: search.web takes one query; a section's queries are joined with Brave's OR
    raw = sections_json(section(queries=["edge ai", "jetson thor"]))
    result = NewspaperSectionsPort().invoke({"sections": raw}, "k", DEADLINE, context=ctx())
    assert result.output["sections"][0]["query"] == "edge ai OR jetson thor"


def test_sections_port_refuses_a_joined_query_over_the_search_limit():
    raw = sections_json(section(queries=["q" * 300, "r" * 300]))
    result = NewspaperSectionsPort().invoke({"sections": raw}, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert "sections[0].queries" in result.error


def test_parse_sections_leaves_out_the_query():
    assert "query" not in parse_sections(sections_json(section()))[0]


# ------------------------------------------------------------------ newspaper.filter


def hit(url, title="t", snippet="s", source="src"):
    return {"title": title, "url": url, "snippet": snippet, "age": "1h", "source": source}


def norm(**extra):
    return parse_sections(sections_json(section(**extra)))[0]


def urls(results):
    return [item["url"] for item in results]


def test_filter_dedupes_by_url():
    results = [hit("https://a.com/1"), hit("https://a.com/1"), hit("https://b.com/2")]
    assert urls(filter_results(results, norm())) == ["https://a.com/1", "https://b.com/2"]


def test_filter_must_terms_case_insensitive():
    results = [hit("https://a.com/1", title="Jetson Thor"), hit("https://a.com/2")]
    assert urls(filter_results(results, norm(must=["jetson"]))) == ["https://a.com/1"]


def test_filter_must_not_terms():
    results = [hit("https://a.com/1", snippet="a RUMOUR says"), hit("https://a.com/2")]
    assert urls(filter_results(results, norm(must_not=["rumour"]))) == ["https://a.com/2"]


def test_filter_site_include_matches_subdomains():
    results = [
        hit("https://blogs.nvidia.com/x"),
        hit("https://nvidia.com/y"),
        hit("https://notnvidia.com/z"),
    ]
    kept = urls(filter_results(results, norm(site_include=["nvidia.com"])))
    assert kept == ["https://blogs.nvidia.com/x", "https://nvidia.com/y"]


def test_filter_site_exclude():
    results = [hit("https://spam.example/1"), hit("https://a.com/2")]
    assert urls(filter_results(results, norm(site_exclude=["spam.example"]))) == ["https://a.com/2"]


def test_filter_drops_non_http_urls():
    results = [hit("javascript:alert(1)"), hit("ftp://a.com/x"), hit("https://a.com/ok")]
    assert urls(filter_results(results, norm())) == ["https://a.com/ok"]


def test_filter_caps_at_count():
    results = [hit(f"https://a.com/{n}") for n in range(9)]
    assert len(filter_results(results, norm(count=3))) == 3


def test_filter_skips_non_objects():
    assert filter_results(["x", None, hit("https://a.com/1")], norm()) == [hit("https://a.com/1")]


def test_filter_port():
    port = NewspaperFilterPort()
    payload = {"section": norm(), "results": [hit("https://a.com/1"), hit("javascript:x")]}
    result = port.invoke(payload, "k", DEADLINE, context=ctx())
    assert result.output == {"results": [hit("https://a.com/1")]}


def test_filter_port_reads_the_loop_item_and_ignores_the_derived_query():
    # t8: inside the per-section loop the section arrives as the implicit ``item`` port,
    # carrying the ``query`` newspaper.sections added
    port = NewspaperFilterPort()
    payload = {"item": {**norm(), "query": "edge ai"}, "results": [hit("https://a.com/1")]}
    result = port.invoke(payload, "k", DEADLINE, context=ctx())
    assert result.output == {"results": [hit("https://a.com/1")]}


def test_filter_port_bad_section():
    port = NewspaperFilterPort()
    result = port.invoke({"section": {"name": "x"}, "results": []}, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert "section.queries" in result.error


def test_filter_port_results_not_a_list():
    port = NewspaperFilterPort()
    result = port.invoke({"section": norm(), "results": "x"}, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert result.retryable is False


# ------------------------------------------------------------------ local date


@pytest.mark.parametrize(
    ("slot", "expected"),
    [
        ("2026-10-11T03:30:00+00:00", "2026-10-11"),  # IDT (UTC+3): 06:30
        ("2026-10-10T21:30:00+00:00", "2026-10-11"),  # 00:30 local, previous UTC day
        ("2026-10-10T20:59:59+00:00", "2026-10-10"),  # 23:59:59 local
        ("2026-12-01T04:30:00Z", "2026-12-01"),  # IST (UTC+2): 06:30
        ("2026-11-30T22:00:00Z", "2026-12-01"),  # IST: midnight exactly
        ("2026-11-30T21:59:00Z", "2026-11-30"),  # IST: 23:59
    ],
)
def test_local_date_in_jerusalem(slot, expected):
    assert local_date(slot, JERUSALEM) == expected


def test_local_date_across_the_dst_change():
    # Israel leaves DST on 2026-10-25 (02:00 local): 06:30 local is 03:30 UTC before, 04:30 after.
    before = local_date("2026-10-24T03:30:00+00:00", JERUSALEM)
    after = local_date("2026-10-25T04:30:00+00:00", JERUSALEM)
    assert (before, after) == ("2026-10-24", "2026-10-25")


def test_local_date_refuses_a_naive_slot():
    with pytest.raises(ValueError, match="slot"):
        local_date("2026-10-11T03:30:00", JERUSALEM)


def test_local_date_refuses_an_unknown_tz():
    with pytest.raises(ValueError, match="tz"):
        local_date(SLOT, "Mars/Olympus")


# ------------------------------------------------------------------ newspaper.issue


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 11, 3, 30, 5, tzinfo=UTC)

    def __call__(self):
        return self.now


class Issues:
    def __init__(self):
        self.clock = Clock()
        self.store = MemoryStore()
        self.port = NewspaperIssuePort(self.store, clock=self.clock)

    def call(self, phase, run="r1", config=None, **extra):
        payload = {"slot": SLOT, "tz": JERUSALEM, "phase": phase, **extra}
        return self.port.invoke(payload, f"k-{run}", DEADLINE, context=ctx(config, run=run))

    def doc(self, day=DATE):
        return self.store.get(ISSUES_COLLECTION, day)


def answer(result):
    """A claim's answer without its summary deadline (tested on its own)."""
    return {k: v for k, v in result.output.items() if k != "summaries_until"}


def test_first_claim_delivers_and_records_the_claim():
    world = Issues()
    result = world.call(CLAIM)
    assert answer(result) == {"deliver": True, "date": DATE, "reason": "claimed"}
    assert world.doc()["state"] == "claimed"
    assert world.doc()["claimed_by"] == "r1"


def test_claim_is_idempotent_for_the_same_run():
    world = Issues()
    world.call(CLAIM)
    assert world.call(CLAIM).output["deliver"] is True


def test_second_claim_after_delivery_sends_nothing():  # h8
    world = Issues()
    world.call(CLAIM)
    recorded = world.call("record", message_id="m1", item_id="i1")
    assert recorded.output == {"recorded": True, "date": DATE}
    again = world.call(CLAIM, run="r2")
    assert answer(again) == {"deliver": False, "date": DATE, "reason": "already_delivered"}


def test_record_stores_the_delivery_ids():
    world = Issues()
    world.call(CLAIM)
    world.call("record", message_id="m1", item_id="i1")
    doc = world.doc()
    assert (doc["state"], doc["message_id"], doc["item_id"]) == ("delivered", "m1", "i1")


def test_record_reinvoked_by_the_same_run_answers_the_same():
    world = Issues()
    world.call(CLAIM)
    world.call("record", message_id="m1", item_id="i1")
    assert world.call("record", message_id="m1", item_id="i1").output["recorded"] is True


def test_record_by_another_run_keeps_the_first_delivery():
    world = Issues()
    world.call(CLAIM)
    world.call("record", message_id="m1", item_id="i1")
    assert world.call("record", run="r2", message_id="m2").output["recorded"] is False
    assert world.doc()["message_id"] == "m1"


def test_stale_takeover_reports_it():
    world = Issues()
    world.call(CLAIM)
    world.clock.now += timedelta(hours=2)
    assert world.call(CLAIM, run="r2").output["reason"] == "taken_over"


def test_record_without_a_claim_still_records():
    world = Issues()
    world.call("record", message_id="m1")
    assert world.doc()["state"] == "delivered"


def test_live_claim_by_another_run_is_respected():
    world = Issues()
    world.call(CLAIM)
    result = world.call(CLAIM, run="r2")
    assert answer(result) == {"deliver": False, "date": DATE, "reason": "claimed_by_another_run"}


def test_stale_claim_by_another_run_is_taken_over():
    world = Issues()
    world.call(CLAIM)
    world.clock.now += timedelta(hours=2)
    result = world.call(CLAIM, run="r2")
    assert result.output["deliver"] is True
    assert world.doc()["claimed_by"] == "r2"


def test_claim_timeout_from_config():
    world = Issues()
    world.call(CLAIM)
    world.clock.now += timedelta(seconds=61)
    result = world.call(CLAIM, run="r2", config={"claim_timeout_s": 60})
    assert result.output["deliver"] is True


def test_phase_from_config():
    world = Issues()
    payload = {"slot": SLOT, "tz": JERUSALEM}
    result = world.port.invoke(payload, "k", DEADLINE, context=ctx({"phase": CLAIM}))
    assert result.output["deliver"] is True


def test_tz_from_config():
    world = Issues()
    payload = {"slot": "2026-10-10T21:30:00Z", "phase": CLAIM}
    result = world.port.invoke(payload, "k", DEADLINE, context=ctx({"tz": JERUSALEM}))
    assert result.output["date"] == DATE


def test_different_dates_are_independent():
    world = Issues()
    world.call(CLAIM)
    world.call("record", message_id="m1")
    result = world.call(CLAIM, run="r2", slot="2026-10-12T03:30:00Z")
    assert answer(result) == {"deliver": True, "date": "2026-10-12", "reason": "claimed"}


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"slot": SLOT, "tz": JERUSALEM, "phase": "send"}, "phase"),
        ({"tz": JERUSALEM, "phase": CLAIM}, "slot"),
        ({"slot": "yesterday", "tz": JERUSALEM, "phase": CLAIM}, "slot"),
        ({"slot": SLOT, "tz": "Nowhere/Land", "phase": CLAIM}, "tz"),
        ({"slot": SLOT, "phase": CLAIM}, "tz"),
    ],
)
def test_issue_bad_input_names_the_field(payload, field):
    result = Issues().port.invoke(payload, "k", DEADLINE, context=ctx())
    assert result.outcome == FAILED
    assert result.retryable is False
    assert field in result.error


def test_bad_claim_timeout_config():
    result = Issues().call(CLAIM, config={"claim_timeout_s": -1})
    assert result.outcome == FAILED
    assert "claim_timeout_s" in result.error


# ------------------------------------------------------------------ fenced delivery (Codex r1 #1)

BEFORE_EMAIL = "before_email"
BEFORE_PUSH = "before_push"
RECORD = "record"
HTML = "<p>issue</p>"
SUBJECT = "Daily newspaper 2026-10-11"
MESSAGE_ID = "message_id"
SEND = "send"
LOST = "claim_lost"
EMAIL_KEY = f"newspaper:{DATE}:email"
UNCERTAIN = "email_uncertain"
HTML_FIELD, KEY_FIELD = "html", "idempotency_key"
R2 = "r2"
M1 = "m1"
OTHER_HTML = "<p>other</p>"
SEND_UNTIL = "send_until"


def emailed(world, run="r1", message=M1, html=HTML):
    """A run that claimed, passed its email fence and recorded its sent email."""
    world.call(CLAIM, run=run)
    world.call(BEFORE_EMAIL, run=run, subject=SUBJECT, html=html)
    return world.call(BEFORE_PUSH, run=run, message_id=message)


def taken_over(world):
    """r1 sent the email and died before the push; r2 takes the date after the lease."""
    emailed(world)
    world.clock.now += timedelta(hours=2)
    world.call(CLAIM, run=R2)


def test_the_email_fence_answers_send_with_the_durable_key():
    world = Issues()
    world.call(CLAIM)
    out = world.call(BEFORE_EMAIL, subject=SUBJECT, html=HTML).output
    assert out == {
        SEND: True,
        "date": DATE,
        "subject": SUBJECT,
        HTML_FIELD: HTML,
        KEY_FIELD: EMAIL_KEY,
        UNCERTAIN: False,
        SEND_UNTIL: "2026-10-12T02:30:05+00:00",
    }


def test_after_a_takeover_the_sent_email_is_not_sent_again():
    world = Issues()
    taken_over(world)
    out = world.call(BEFORE_EMAIL, run=R2, subject=SUBJECT, html=OTHER_HTML).output
    assert out[SEND] is False


def test_after_a_takeover_the_recorded_message_id_is_reported():
    world = Issues()
    taken_over(world)
    out = world.call(BEFORE_EMAIL, run=R2, subject=SUBJECT, html=OTHER_HTML).output
    assert out[MESSAGE_ID] == M1


def test_a_takeover_reuses_the_first_payload_and_key():
    world = Issues()
    world.call(CLAIM)
    world.call(BEFORE_EMAIL, subject=SUBJECT, html=HTML)  # sent, then the node died
    world.clock.now += timedelta(hours=2)
    world.call(CLAIM, run=R2)
    out = world.call(BEFORE_EMAIL, run=R2, subject=SUBJECT, html=OTHER_HTML).output
    assert (out[SEND], out[HTML_FIELD], out[KEY_FIELD]) == (True, HTML, EMAIL_KEY)


def test_after_a_takeover_the_push_fence_passes_with_the_recorded_message():
    world = Issues()
    taken_over(world)
    world.call(BEFORE_EMAIL, run=R2, subject=SUBJECT, html=HTML)
    out = world.call(BEFORE_PUSH, run=R2).output
    assert out == {MESSAGE_ID: M1, "date": DATE, UNCERTAIN: False}


def test_a_slow_owner_taken_over_cannot_send_the_email():
    world = Issues()
    world.call(CLAIM)
    world.clock.now += timedelta(hours=2)
    world.call(CLAIM, run=R2)
    result = world.call(BEFORE_EMAIL, subject=SUBJECT, html=HTML)
    assert result.outcome == FAILED
    assert result.error.startswith(LOST)


def test_a_slow_owner_taken_over_cannot_push():
    world = Issues()
    world.call(CLAIM)
    world.call(BEFORE_EMAIL, subject=SUBJECT, html=HTML)
    world.clock.now += timedelta(hours=2)
    world.call(CLAIM, run=R2)
    result = world.call(BEFORE_PUSH, message_id=M1)
    assert result.error.startswith(LOST)


def test_a_slow_owner_still_records_the_email_it_sent():
    world = Issues()
    world.call(CLAIM)
    world.call(BEFORE_EMAIL, subject=SUBJECT, html=HTML)
    world.clock.now += timedelta(hours=2)
    world.call(CLAIM, run=R2)
    world.call(BEFORE_PUSH, message_id=M1)
    assert world.doc()[EMAIL][MESSAGE_ID] == M1


def test_a_lost_claim_is_not_retryable():
    world = Issues()
    world.call(CLAIM)
    world.clock.now += timedelta(hours=2)
    world.call(CLAIM, run=R2)
    assert world.call(BEFORE_EMAIL, subject=SUBJECT, html=HTML).retryable is False


def test_the_fences_renew_the_lease():
    world = Issues()
    world.call(CLAIM)
    world.clock.now += timedelta(minutes=50)
    world.call(BEFORE_EMAIL, subject=SUBJECT, html=HTML)
    world.clock.now += timedelta(minutes=50)  # 100 min after the claim, 50 after the fence
    assert world.call(CLAIM, run=R2).output["reason"] == "claimed_by_another_run"


def test_the_email_fence_needs_the_rendered_issue():
    world = Issues()
    world.call(CLAIM)
    result = world.call(BEFORE_EMAIL, subject=SUBJECT)
    assert result.error.startswith("bad_input")


def test_the_push_fence_needs_a_sent_email():
    world = Issues()
    world.call(CLAIM)
    world.call(BEFORE_EMAIL, subject=SUBJECT, html=HTML)
    assert world.call(BEFORE_PUSH).error.startswith("bad_input")


def test_record_stores_the_library_progress():
    world = Issues()
    emailed(world)
    world.call(RECORD, message_id=M1, item_id=7, queue_id=11)
    library = world.doc()["library"]
    assert (library["item_id"], library["queue_id"]) == ("7", "11")


def test_the_claim_gives_the_summary_deadline_from_the_slot():
    out = Issues().call(CLAIM).output
    assert out["summaries_until"] == "2026-10-11T03:40:00+00:00"


def test_the_summary_window_from_config():
    out = Issues().call(CLAIM, config={"summary_window_s": 900}).output
    assert out["summaries_until"] == "2026-10-11T03:45:00+00:00"


def test_bad_summary_window_config():
    result = Issues().call(CLAIM, config={"summary_window_s": 0})
    assert "summary_window_s" in result.error


# ------------------------------------------------------------------ Codex r2 #1 and #4

READER = "reader@example.test"
OTHER = "other@example.test"
EMAIL_ACTOR = "newspaper-email"
SENDER = "newsletter-no-reply@culture.dev"
GATE = {"email_actor": EMAIL_ACTOR}
DAY_S = 24 * 3600
FIRST_AT = "2026-10-11T03:30:05+00:00"
FIRST_MESSAGE = "msg-1"
EMAIL = "email"
REQUEST = ("to", "sender", "subject", HTML_FIELD, KEY_FIELD)


def email_actor(recipients=(READER,), local_part="newsletter-no-reply"):
    connection = {
        "api_key": "grant:RESEND_API_KEY",
        "domain": "culture.dev",
        "local_part": local_part,
        "recipients": list(recipients),
    }
    params = {"surface": EMAIL, "connection": connection}
    return {"id": EMAIL_ACTOR, "name": EMAIL, "kind": "app", "enabled": True, "params": params}


class ExpiringResend:
    """Resend's POST /emails: a key dedupes for 24 hours, a reused key with a different
    body is 409, and ``drop_acks`` loses the answer after the email went out."""

    def __init__(self, clock):
        self.clock = clock
        self.sent: list[dict] = []
        self.keys: dict[str, tuple] = {}
        self.drop_acks = False

    def __call__(self, method, url, headers, body, timeout):
        key, now = headers.get("Idempotency-Key"), self.clock()
        live = self.keys.get(key)
        if live is not None and (now - live[1]).total_seconds() < DAY_S:
            if live[2] != body:
                return 409, b'{"name": "invalid_idempotent_request"}'
            return 200, json.dumps({"id": live[0]}).encode()
        self.sent.append(json.loads(body))
        self.keys[key] = (f"msg-{len(self.sent)}", now, body)
        if self.drop_acks:
            raise TimeoutError("the answer was lost")
        return 200, json.dumps({"id": self.keys[key][0]}).encode()


class Mail(Issues):
    """Issues plus the email actor and email.send on an expiring Resend fake."""

    def __init__(self, recipients=(READER,)):
        super().__init__()
        self.store.put(ACTORS_COLLECTION, email_actor(recipients))
        self.resend = ExpiringResend(self.clock)
        self.email = EmailSendPort(
            self.store,
            transport=self.resend,
            secrets=lambda _ref: "fake-key-value",
            clock=self.clock,
        )

    def fence(self, run="r1", to=(READER,), html=HTML):
        to = list(to) if isinstance(to, tuple) else to
        return self.call(BEFORE_EMAIL, run=run, config=GATE, subject=SUBJECT, html=html, to=to)

    def send(self, gate):
        """The email step as the workflow wires it from the fence's output."""
        out = gate.output
        if not out[SEND]:
            return None
        params = {k: out.get(k) for k in REQUEST}
        params["not_after"] = out.get(SEND_UNTIL)
        context = InvocationContext(
            run_id="r", step_id=EMAIL, kind="action", host="spark", actor=EMAIL_ACTOR
        )
        return self.email.invoke(params, "engine-key", DEADLINE, context=context)

    def lost_ack(self, to=(READER,)):
        """r1 fences and sends; Resend accepts but the answer is lost and the node dies."""
        self.call(CLAIM)
        self.resend.drop_acks = True
        self.send(self.fence(to=to))
        self.resend.drop_acks = False

    def lost_ack_gate(self):
        """Like :meth:`lost_ack`, returning the fence's answer the email step keeps."""
        self.call(CLAIM)
        self.resend.drop_acks = True
        gate = self.fence()
        self.send(gate)
        self.resend.drop_acks = False
        return gate

    def takeover(self, hours, to=(READER,)):
        self.clock.now += timedelta(hours=hours)
        self.call(CLAIM, run=R2)
        gate = self.fence(run=R2, to=to, html=OTHER_HTML)
        return gate, self.send(gate)


def test_the_email_fence_records_the_first_attempt():
    world = Mail()
    world.call(CLAIM)
    world.fence()
    attempt = world.doc()["email_attempt"]
    assert (attempt["at"], attempt["key"]) == (FIRST_AT, EMAIL_KEY)


def test_a_second_fence_keeps_the_first_attempt_time():
    world = Mail()
    world.lost_ack()
    world.takeover(hours=2)
    assert world.doc()["email_attempt"]["at"] == FIRST_AT


def test_a_takeover_inside_the_window_resends_and_resend_dedupes():
    world = Mail()
    world.lost_ack()
    _, sent = world.takeover(hours=2)
    assert (sent.output[MESSAGE_ID], len(world.resend.sent)) == (FIRST_MESSAGE, 1)


def test_a_takeover_after_25_hours_sends_nothing():
    world = Mail()
    world.lost_ack()
    world.takeover(hours=25)
    assert len(world.resend.sent) == 1


def test_a_takeover_after_the_window_answers_send_false_and_uncertain():
    world = Mail()
    world.lost_ack()
    gate, _ = world.takeover(hours=25)
    assert (gate.output[SEND], gate.output[UNCERTAIN]) == (False, True)


def test_an_uncertain_email_is_recorded_with_its_attempt_time():
    world = Mail()
    world.lost_ack()
    world.takeover(hours=25)
    assert world.doc()[EMAIL] == {"uncertain": True, "attempted_at": FIRST_AT}


def test_the_window_closes_at_23_hours():
    world = Mail()
    world.lost_ack()
    gate, _ = world.takeover(hours=23)
    assert gate.output[SEND] is False


def test_a_fresh_email_is_not_uncertain():
    world = Mail()
    world.call(CLAIM)
    assert world.fence().output[UNCERTAIN] is False


def test_the_push_fence_passes_an_uncertain_email_and_reports_it():
    world = Mail()
    world.lost_ack()
    world.takeover(hours=25)
    out = world.call(BEFORE_PUSH, run=R2).output
    assert out == {MESSAGE_ID: None, "date": DATE, UNCERTAIN: True}


def test_the_push_fence_reports_a_sent_email_as_certain():
    world = Issues()
    assert emailed(world).output[UNCERTAIN] is False


def test_a_later_fence_of_an_uncertain_date_still_sends_nothing():
    world = Mail()
    world.lost_ack()
    world.takeover(hours=25)
    world.clock.now += timedelta(hours=2)
    world.call(CLAIM, run="r3")
    assert world.fence(run="r3").output[SEND] is False


def test_the_email_fence_freezes_the_recipients_and_the_sender():
    world = Mail()
    world.call(CLAIM)
    out = world.fence().output
    assert (out["to"], out["sender"]) == ([READER], SENDER)


def test_a_takeover_replays_the_first_recipients():
    world = Mail(recipients=(READER, OTHER))
    world.lost_ack()
    gate, _ = world.takeover(hours=2, to=[OTHER])
    assert gate.output["to"] == [READER]


def test_changed_recipients_after_a_lost_ack_still_recover_the_first_message():
    world = Mail(recipients=(READER, OTHER))
    world.lost_ack()
    _, sent = world.takeover(hours=2, to=[OTHER])
    assert (sent.outcome, sent.output[MESSAGE_ID]) == (COMPLETED, FIRST_MESSAGE)


def test_a_changed_sender_after_a_lost_ack_still_recovers_the_first_message():
    world = Mail()
    world.lost_ack()
    world.store.put(ACTORS_COLLECTION, email_actor(local_part="news"))
    _, sent = world.takeover(hours=2)
    assert sent.output[MESSAGE_ID] == FIRST_MESSAGE


def test_a_frozen_recipient_removed_from_the_allowlist_is_refused():
    world = Mail(recipients=(READER, OTHER))
    world.lost_ack()
    world.store.put(ACTORS_COLLECTION, email_actor(recipients=(OTHER,)))
    _, sent = world.takeover(hours=2, to=[OTHER])
    assert (sent.error, sent.retryable) == ("recipient_not_allowed", False)


def test_the_email_fence_without_its_actor_is_bad_input():
    world = Issues()
    world.call(CLAIM)
    res = world.call(BEFORE_EMAIL, config={"email_actor": "missing"}, subject=SUBJECT, html=HTML)
    assert res.error.startswith("bad_input: email_actor")


def test_the_email_fence_refuses_recipients_that_are_not_a_list():
    world = Mail()
    world.call(CLAIM)
    assert world.fence(to=READER).error.startswith("bad_input: to")


# ------------------------------------------------------------------ Codex r3: the frozen window


def test_the_email_fence_answers_the_first_attempt_plus_23_hours():
    world = Mail()
    world.lost_ack()
    gate, _ = world.takeover(hours=2)
    assert gate.output[SEND_UNTIL] == "2026-10-12T02:30:05+00:00"


def test_a_same_run_retry_past_the_window_sends_nothing():
    """The run's email step retries with the fence's answer it persisted, 25 hours on."""
    world = Mail()
    gate = world.lost_ack_gate()
    world.clock.now += timedelta(hours=25)
    retried = world.send(gate)
    assert (retried.error, len(world.resend.sent)) == ("email_window_passed", 1)


def test_the_push_fence_after_a_refused_late_retry_is_uncertain():
    world = Mail()
    gate = world.lost_ack_gate()
    world.clock.now += timedelta(hours=25)
    world.send(gate)
    out = world.call(BEFORE_PUSH).output
    assert out == {MESSAGE_ID: None, "date": DATE, UNCERTAIN: True}


def test_the_push_fence_after_a_refused_late_retry_records_the_email_uncertain():
    world = Mail()
    world.lost_ack_gate()
    world.clock.now += timedelta(hours=25)
    world.call(BEFORE_PUSH)
    assert world.doc()[EMAIL] == {"uncertain": True, "attempted_at": FIRST_AT}


def test_a_failed_email_inside_the_window_still_fails_the_push_fence():
    """A refused send (say recipient_not_allowed) is never passed off as uncertain while
    the date could still be emailed."""
    world = Mail()
    world.lost_ack_gate()
    world.clock.now += timedelta(hours=22)
    assert world.call(BEFORE_PUSH).error.startswith("bad_input: message_id")
