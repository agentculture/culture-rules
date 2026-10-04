"""Enrol a machine's mesh agents as agent actors (``actors enrol-agents``, t31).

Pure planning, no HTTP: :func:`read_server_yaml` and :func:`desired_actors` read the
machine-local ``~/.culture/server.yaml`` (``server.name`` plus an ``agents: {suffix: workdir}``
map) and each workdir's ``culture.yaml`` (through :mod:`culture_rules.actors.config`);
:func:`plan_enrolment` diffs that against the actors the API already holds.

Rules the plan keeps:

* The actor id is the mesh nick, ``<server.name>-<suffix>``; ``machine`` is the server name
  (or the ``machine`` override), ``harness`` is culture's ``backend``, ``model`` is the
  agent's. ``config_source`` is ``repo`` with ``repo`` set to the workdir, which is exactly
  what the engine node reads to launch the agent.
* Managed actors carry ``params.enrolled_by == "enrol-agents"``. Anything without that marker
  is hand-made and never touched (a clash on the id is only a warning).
* A managed actor of this machine that is no longer listed is **disabled, never deleted**.
  The plan first stamps ``params.disabled_by_enrol`` so the tool can tell its own disables from
  an operator's: a relisted agent is re-enabled only when that stamp is present, otherwise it
  stays disabled with a warning.
* An agent whose workdir is unreadable is still *listed*, so it is neither created nor
  disabled; the problem is reported as a warning.

YAML parsing needs the optional ``yaml`` extra and is imported lazily.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from culture_rules.actors import config as actor_config

MARKER = "enrol-agents"
DISABLED_STAMP = "disabled_by_enrol"
DEFAULT_SERVER_YAML = "~/.culture/server.yaml"

# Fields an enrolled actor document carries (everything else on the server is dropped from a PUT
# body, which the model would reject or recompute).
_FIELDS = (
    "name",
    "description",
    "kind",
    "capabilities",
    "harness",
    "model",
    "machine",
    "config_source",
    "repo",
    "params",
    "enabled",
)
# Fields the tool owns: a change here is an update; every other field is preserved.
_MANAGED = ("name", "description", "kind", "harness", "model", "machine", "config_source", "repo")


class EnrolError(ValueError):
    """The server.yaml could not be read; ``missing_extra`` marks a missing PyYAML."""

    def __init__(self, message: str, *, missing_extra: bool = False):
        super().__init__(message)
        self.missing_extra = missing_extra


@dataclass(frozen=True)
class ServerInfo:
    name: str
    agents: dict[str, Any]
    path: str = ""


@dataclass
class Desired:
    """What server.yaml says this machine should enrol."""

    machine: str
    actors: list[dict[str, Any]] = field(default_factory=list)
    listed: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Change:
    """One planned write. ``disable`` with a body stamps it (PUT) before the POST /disable."""

    action: str  # create | update | disable
    id: str
    body: dict[str, Any] | None
    reason: str = ""


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _yaml() -> Any:
    try:
        import yaml  # noqa: PLC0415 - optional extra
    except ImportError as exc:
        raise EnrolError(
            "PyYAML is required to read server.yaml and culture.yaml; install with "
            "'pip install culture-rules[yaml]'",
            missing_extra=True,
        ) from exc
    return yaml


def read_server_yaml(path: str | Path) -> ServerInfo:
    """Parse ``server.yaml``: ``server.name`` and the ``agents`` suffix -> workdir map."""
    yaml = _yaml()
    p = Path(path).expanduser()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise EnrolError(f"cannot read {p}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise EnrolError(f"invalid YAML in {p}: {exc}") from exc
    server = raw.get("server") if isinstance(raw, dict) else None
    name = server.get("name") if isinstance(server, dict) else None
    if not name:
        raise EnrolError(f"{p} has no server.name")
    agents = raw.get("agents") or {}
    if not isinstance(agents, dict):
        raise EnrolError(f"{p}: 'agents' must be a mapping of suffix to workdir")
    return ServerInfo(str(name), {str(k): v for k, v in agents.items()}, str(p))


def _workdir(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        value = entry.get("path") or entry.get("workdir")
        return value if isinstance(value, str) else None
    return None


def _body(cfg: actor_config.ActorConfig, nick: str, machine: str, workdir: str) -> dict[str, Any]:
    params: dict[str, Any] = {"enrolled_by": MARKER}
    if cfg.extras.get("engine"):
        params["engine"] = cfg.extras["engine"]
    body: dict[str, Any] = {
        "id": nick,
        "name": nick,
        "description": f"Mesh agent {nick} on {machine}",
        "kind": "agent",
        "harness": cfg.harness,
        "machine": machine,
        "config_source": "repo",
        "repo": workdir,
        "params": params,
        "enabled": True,
    }
    if cfg.model:
        body["model"] = cfg.model
    return body


def desired_actors(server: ServerInfo, *, machine: str | None = None) -> Desired:
    """The agent actors server.yaml and the workdirs' culture.yaml files describe."""
    target = machine or server.name
    out = Desired(machine=target)
    for suffix, entry in server.agents.items():
        nick = f"{server.name}-{suffix}"
        out.listed.add(nick)
        workdir = _workdir(entry)
        if not workdir:
            out.warnings.append(f"agent {suffix!r}: no workdir in server.yaml; skipped")
            continue
        workdir = str(Path(workdir).expanduser())
        try:
            configs = actor_config.load_all_from_repo(workdir)
        except FileNotFoundError:
            out.warnings.append(f"agent {suffix!r}: no culture.yaml in {workdir}; skipped")
            continue
        except (OSError, actor_config.ActorConfigError) as exc:
            out.warnings.append(f"agent {suffix!r}: cannot read {workdir}: {exc}; skipped")
            continue
        cfg = next((c for c in configs if c.key == suffix), None)
        if cfg is None:
            out.warnings.append(
                f"agent {suffix!r}: no agent with that suffix in {workdir}/culture.yaml; skipped"
            )
            continue
        out.actors.append(_body(cfg, nick, target, workdir))
    return out


def _normal(doc: dict[str, Any]) -> dict[str, Any]:
    out = {k: copy.deepcopy(doc[k]) for k in _FIELDS if doc.get(k) is not None}
    out["id"] = doc["id"]
    out.setdefault("params", {})
    out.setdefault("capabilities", [])
    out["capabilities"] = list(out["capabilities"])
    out.setdefault("enabled", True)
    return out


def _managed(doc: dict[str, Any]) -> bool:
    return (doc.get("params") or {}).get("enrolled_by") == MARKER


def _merge(existing: dict[str, Any], want: dict[str, Any], enabled: bool) -> dict[str, Any]:
    """``existing`` with the tool-owned fields from ``want``; the rest is preserved."""
    body = _normal(existing)
    for key in _MANAGED:
        if want.get(key) is None:
            body.pop(key, None)
        else:
            body[key] = want[key]
    params = dict(body["params"])
    params["enrolled_by"] = MARKER
    params.pop(DISABLED_STAMP, None)
    if "engine" in want["params"]:
        params["engine"] = want["params"]["engine"]
    else:
        params.pop("engine", None)
    body["params"] = params
    body["enabled"] = enabled
    return body


def plan_enrolment(
    existing: list[dict[str, Any]],
    desired: list[dict[str, Any]],
    *,
    machine: str,
    listed: set[str] | None = None,
) -> Plan:
    """Diff ``desired`` against the actors the API holds (``existing``, deleted ones included)."""
    plan = Plan()
    by_id = {d["id"]: d for d in existing}
    wanted = {w["id"]: w for w in desired}
    keep = set(listed or ()) | set(wanted)
    for ident, want in wanted.items():
        have = by_id.get(ident)
        if have is None:
            plan.changes.append(Change("create", ident, want, "listed in server.yaml"))
        elif not _managed(have):
            plan.warnings.append(f"{ident}: exists and was not enrolled by this tool; left as is")
        elif have.get("deleted_at"):
            plan.warnings.append(f"{ident}: soft-deleted; restore or purge it, then re-run")
        else:
            _plan_existing(plan, ident, have, want)
    for ident, have in sorted(by_id.items()):
        if (
            ident not in keep
            and _managed(have)
            and have.get("machine") == machine
            and not have.get("deleted_at")
            and have.get("enabled", True)
        ):
            stamped = _normal(have)
            stamped["params"][DISABLED_STAMP] = True
            body = None if (have.get("params") or {}).get(DISABLED_STAMP) else stamped
            plan.changes.append(Change("disable", ident, body, "no longer listed in server.yaml"))
    return plan


def _plan_existing(plan: Plan, ident: str, have: dict[str, Any], want: dict[str, Any]) -> None:
    enabled = bool(have.get("enabled", True))
    reason = "managed fields changed"
    if not enabled:
        if (have.get("params") or {}).get(DISABLED_STAMP):
            enabled, reason = True, "listed again; re-enabling what this tool disabled"
        else:
            plan.warnings.append(f"{ident}: disabled by an operator; left disabled")
    body = _merge(have, want, enabled)
    current = _normal(have)
    if body != current:
        plan.changes.append(Change("update", ident, body, reason))
