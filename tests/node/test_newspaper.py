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
R2 = "r2"
M1 = "m1"
OTHER_HTML = "<p>other</p>"


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
        "html": HTML,
        "idempotency_key": EMAIL_KEY,
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
    assert (out[SEND], out["html"], out["idempotency_key"]) == (True, HTML, EMAIL_KEY)


def test_after_a_takeover_the_push_fence_passes_with_the_recorded_message():
    world = Issues()
    taken_over(world)
    world.call(BEFORE_EMAIL, run=R2, subject=SUBJECT, html=HTML)
    assert world.call(BEFORE_PUSH, run=R2).output == {MESSAGE_ID: M1, "date": DATE}


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
    assert world.doc()["email"][MESSAGE_ID] == M1


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
