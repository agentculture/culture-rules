"""The engine node daemon: one process per host that *is* the rules engine.

:mod:`culture_rules.node.daemon` composes heartbeat, event ingest, rule firing
(:mod:`culture_rules.node.firing`), the run executor, actor adapters
(:mod:`culture_rules.node.actors`) and the run reporter; :mod:`culture_rules.node.runner`
backs ``culture-rules node run``. Standard-library only (drivers are imported lazily).
"""
