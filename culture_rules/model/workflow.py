"""Workflow: *how* reusable work is done — a DAG of typed steps plus bounded loops.

A workflow never knows which trigger fired it: it sees only its declared inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from culture_rules.model.common import SCHEMA_VERSION, Model, RetryPolicy, doc
from culture_rules.model.placement import Placement

__all__ = [
    "LOOP_KINDS",
    "PORT_TYPES",
    "STEP_KINDS",
    "Edge",
    "Output",
    "Port",
    "Step",
    "Variable",
    "Workflow",
]

PortType = Literal["string", "number", "integer", "boolean", "object", "array", "any"]
StepKind = Literal["logic", "ai", "code", "actor_task", "for_each", "retry_until", "wait"]

PORT_TYPES: tuple[str, ...] = ("string", "number", "integer", "boolean", "object", "array", "any")
STEP_KINDS: tuple[str, ...] = (
    "logic",
    "ai",
    "code",
    "actor_task",
    "for_each",
    "retry_until",
    "wait",
)
LOOP_KINDS: tuple[str, ...] = ("for_each", "retry_until")

_VALUE_TYPE = "Value type"
_FREE_TEXT = "Free text"


@dataclass(frozen=True, kw_only=True)
class Port(Model):
    """A typed input or output port of a step or workflow."""

    name: str = doc("Port name, unique per side")
    type: PortType = doc(_VALUE_TYPE, default="any")
    required: bool = doc("Must be wired / supplied", default=True)
    description: str = doc(_FREE_TEXT, default="")


@dataclass(frozen=True, kw_only=True)
class Variable(Model):
    """A workflow-local variable."""

    name: str = doc("Variable name")
    type: PortType = doc(_VALUE_TYPE, default="any")
    default: Any = doc("Initial value (any JSON)", default=None)


@dataclass(frozen=True, kw_only=True)
class Output(Model):
    """An explicitly exported workflow output."""

    name: str = doc("Output name")
    type: PortType = doc(_VALUE_TYPE, default="any")
    source: str | None = doc(
        "Reference: inputs.<n>, vars.<n> or steps.<id>.outputs.<port>", default=None
    )


@dataclass(frozen=True, kw_only=True)
class Edge(Model):
    """Wires an output port to an input port. ``source`` may be ``inputs`` (workflow inputs)."""

    source: str = doc("Source step id, or 'inputs' for the workflow's inputs")
    source_port: str = doc("Output port on the source")
    target: str = doc("Target step id")
    target_port: str = doc("Input port on the target")


@dataclass(frozen=True, kw_only=True)
class Step(Model):
    """One unit of work. Loop kinds (for_each, retry_until) require max_iterations."""

    id: str = doc("Step id, unique within the workflow (including loop bodies)")
    name: str = doc("Display name", default="")
    description: str = doc(_FREE_TEXT, default="")
    kind: StepKind = doc("logic | ai | code | actor_task | for_each | retry_until")
    inputs: tuple[Port, ...] = doc("Typed input ports", default=())
    outputs: tuple[Port, ...] = doc("Typed output ports", default=())
    placement: Placement | None = doc(
        "Where the step runs; null means engine default", default=None
    )
    timeout_s: float | None = doc("Timeout in seconds (> 0)", default=None)
    retry: RetryPolicy | None = doc("Retry policy", default=None)
    config: dict[str, Any] = doc("Kind-specific configuration", default_factory=dict)
    max_iterations: int | None = doc("Loop bound (required for loop kinds, >= 1)", default=None)
    body: tuple[Step, ...] = doc("Steps run per iteration (loop kinds only)", default=())
    enabled: bool = doc("Disabled steps never execute", default=True)

    __schema_extra__: ClassVar[dict[str, Any]] = {
        "allOf": [
            {
                "if": {"properties": {"kind": {"enum": list(LOOP_KINDS)}}, "required": ["kind"]},
                "then": {
                    "required": ["max_iterations"],
                    "properties": {"max_iterations": {"type": "integer", "minimum": 1}},
                },
            }
        ]
    }


@dataclass(frozen=True, kw_only=True)
class Workflow(Model):
    """Inputs, variables, steps (+ edges) and exported outputs; no trigger knowledge."""

    id: str = doc("Stable workflow id")
    name: str = doc("Display name")
    description: str = doc(_FREE_TEXT, default="")
    version: int = doc("Definition version (>= 1)", default=1)
    inputs: tuple[Port, ...] = doc("Workflow inputs", default=())
    variables: tuple[Variable, ...] = doc("Workflow-local variables", default=())
    steps: tuple[Step, ...] = doc("Steps", default=())
    edges: tuple[Edge, ...] = doc("Port-to-port wiring", default=())
    outputs: tuple[Output, ...] = doc("Explicitly exported outputs", default=())
    enabled: bool = doc("Disabled workflows never run", default=True)
    schema_version: str = doc("Document schema version (MAJOR.MINOR)", default=SCHEMA_VERSION)
