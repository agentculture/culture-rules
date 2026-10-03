"""Run summaries posted to a mesh channel (t21): c56 / h40."""

from __future__ import annotations

import logging

import pytest

from culture_rules.engine.reports import (
    ChannelPoster,
    RunReporter,
    summarize_run,
)
from culture_rules.engine.runs import RUNS_COLLECTION, Executor
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, ports_for, rule, step, workflow


class FakePoster:
    def __init__(self, error: Exception | None = None) -> None:
        self.posts: list[tuple[str, str]] = []
        self.error = error

    def post(self, channel: str, text: str) -> None:
        if self.error:
            raise self.error
        self.posts.append((channel, text))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> MemoryStore:
    return MemoryStore(clock=clock)


def finished_run(store, clock, actor=None, **rule_kw):
    actor = actor or FakeActor()
    ex = Executor(store, "spark", ports_for(actor), clock=clock)
    run = ex.start(rule(**rule_kw), workflow((step("a"),)))
    ex.run_until_idle()
    return ex.run(run["id"])


def test_poster_protocol():
    assert isinstance(FakePoster(), ChannelPoster)


def test_finished_run_posts_summary_to_channel(store, clock):
    doc = finished_run(store, clock)
    poster = FakePoster()
    assert RunReporter(poster, channel="#runs").report(doc) is True
    [(channel, text)] = poster.posts
    assert channel == "#runs"
    assert doc["id"] in text
    assert "r1" in text
    assert "succeeded" in text


def test_failed_run_summary_carries_the_error(store, clock):
    actor = FakeActor().on("a", ("fail", "boom", False))
    doc = finished_run(store, clock, actor)
    assert doc["status"] == "failed"
    assert "failed" in summarize_run(doc)
    assert doc["error"]["code"] in summarize_run(doc)


def test_running_run_is_not_posted(store, clock):
    actor = FakeActor().on("a", ("accept",))
    doc = finished_run(store, clock, actor)
    assert doc["status"] == "running"
    poster = FakePoster()
    assert RunReporter(poster, channel="#runs").report(doc) is False
    assert poster.posts == []


def test_per_rule_channel_overrides_default_and_unconfigured_is_silent(store, clock):
    doc = finished_run(store, clock)
    poster = FakePoster()
    RunReporter(poster, channel="#all", channels={"r1": "#rule1"}).report(doc)
    assert poster.posts[0][0] == "#rule1"
    quiet = FakePoster()
    assert RunReporter(quiet).report(doc) is False
    assert quiet.posts == []


def test_failing_post_never_fails_the_run(store, clock, caplog):
    doc = finished_run(store, clock)
    poster = FakePoster(error=ConnectionError("mesh down"))
    with caplog.at_level(logging.WARNING):
        assert RunReporter(poster, channel="#runs").report(doc) is False
    assert "mesh down" in caplog.text
    assert store.get(RUNS_COLLECTION, doc["id"])["status"] == "succeeded"


def test_each_run_is_reported_once(store, clock):
    doc = finished_run(store, clock)
    poster = FakePoster()
    rep = RunReporter(poster, channel="#runs")
    assert rep.report(doc) is True
    assert rep.report(doc) is False
    assert len(poster.posts) == 1


def test_failed_post_is_retried_on_next_observation(store, clock):
    doc = finished_run(store, clock)
    poster = FakePoster(error=OSError("x"))
    rep = RunReporter(poster, channel="#runs")
    assert rep.report(doc) is False
    poster.error = None
    assert rep.report(doc) is True


def test_observe_reads_change_feed_and_returns_token(store, clock):
    head = store.head(RUNS_COLLECTION)
    doc = finished_run(store, clock)
    poster = FakePoster()
    rep = RunReporter(poster, channel="#runs")
    token = rep.observe(store, head)
    assert len(poster.posts) == 1
    assert doc["id"] in poster.posts[0][1]
    assert rep.observe(store, token) == token
    assert len(poster.posts) == 1


def test_observe_survives_failing_poster(store, clock):
    head = store.head(RUNS_COLLECTION)
    finished_run(store, clock)
    rep = RunReporter(FakePoster(error=RuntimeError("no")), channel="#runs")
    assert rep.observe(store, head) != head
