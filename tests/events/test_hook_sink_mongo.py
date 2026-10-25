"""Discord event retention on the MongoDB adapter (marker ``mongo``; skips without docker)."""

from __future__ import annotations

from datetime import datetime

import pytest

from culture_rules.events.hook_sink import DISCORD_TTL_INDEX, ensure_discord_event_ttl, sink
from culture_rules.events.ingest import EVENTS_COLLECTION

pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
_mongo_tests = pytest.importorskip("tests.store.test_mongo", reason="Mongo binding missing")

pytestmark = pytest.mark.mongo


@pytest.fixture
def mongo_store():
    _mongo_tests.get_rig()  # skips when docker / the image is unavailable
    binding = _mongo_tests.TestMongoStore()
    yield binding.make_store()
    _mongo_tests._release_stores()


def _app(surface: str, type: str) -> dict:
    return {
        "id": f"{surface}-app",
        "kind": "app",
        "enabled": True,
        "params": {"surface": surface, "events": [type]},
    }


def test_discord_events_get_a_ttl_index_and_a_date_others_do_not(mongo_store):
    mongo_store.ensure_collections(EVENTS_COLLECTION)
    ensure_discord_event_ttl(mongo_store)
    ensure_discord_event_ttl(mongo_store)  # idempotent
    db = mongo_store._db
    ttl = {i["name"]: i for i in db[EVENTS_COLLECTION].list_indexes()}[DISCORD_TTL_INDEX]
    assert ttl["expireAfterSeconds"] == 0
    assert dict(ttl["key"]) == {"expires_at": 1}
    discord_type, github_type = "discord.message.created", "github.pr.merged"
    assert sink(mongo_store, _app("discord", discord_type), discord_type, {}, "d:1", "a") == (
        "accepted"
    )
    assert sink(mongo_store, _app("github", github_type), github_type, {}, "g1", "a") == (
        "accepted"
    )
    by_type = {d["envelope"]["type"]: d for d in mongo_store.find(EVENTS_COLLECTION)}
    assert isinstance(by_type[discord_type]["expires_at"], datetime)
    assert "expires_at" not in by_type[github_type]
