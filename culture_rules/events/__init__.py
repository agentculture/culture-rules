"""The event fabric: events-cli ingest, the ``events`` collection and change-stream triggers.

culture-rules ships no broker, MQTT server or event-history store of its own.
Transport and durable delivery belong to events-cli; this package subscribes
through it (lazily, behind the ``events`` extra), stores each envelope exactly
once in the StoragePort ``events`` collection (:mod:`.ingest`), fires triggers
from that collection's change feed (:mod:`.triggers`) and stamps lineage on the
envelopes culture-rules emits (:mod:`.emit`).
"""
