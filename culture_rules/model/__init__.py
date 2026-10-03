"""culture_rules.model — the domain model: Rule, Workflow, Step, Action, Actor, Machine, Placement.

Stdlib-only frozen dataclasses. Each model round-trips losslessly through
``to_json``/``from_json``; :mod:`culture_rules.model.validate` returns structured
errors; :mod:`culture_rules.model.schema` generates the committed ``schemas/``.
"""
