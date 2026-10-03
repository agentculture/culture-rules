"""Record one demo.greet envelope in the events collection, as events-cli ingest would."""

import sys
from datetime import UTC, datetime

from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.store.mongo import MongoConfig, MongoStore

store = MongoStore(MongoConfig.from_env())
envelope = {"id": sys.argv[1], "kind": "event", "type": "demo.greet", "data": {"args": {"who": sys.argv[2]}}}
store.insert(EVENTS_COLLECTION, event_document(envelope, host="spark", received_at=datetime.now(UTC)))
print("recorded", envelope["id"])
