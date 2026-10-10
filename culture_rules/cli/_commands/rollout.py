"""``culture-rules rollout`` - roll a release out to the mesh, one step per verb, over the API.

The release is this CLI's own (``deploy_data.release()["version"]``): the operator runs the
CLI from the new release's venv, and the rollout document is keyed by that version. The steps,
in order: ``idle``, ``pause``, ``upgrade --host`` (one per host), ``seed``, ``import``,
``enable``, ``resume``, ``verify``; ``plan`` prints them. No verb runs more than one step and
nothing chains them. Every write is a dry run until ``--apply``; each applied step records its
outcome in the rollout document (``PUT /rollouts/{version}``, read-merge-write).

Guards refuse with exit 1 and a remediation naming the step to run first: ``pause`` while a
run is not terminal (``--force`` overrides; the only override), ``import`` before this
release's ``seed`` step is recorded, ``enable`` before ``pause`` recorded a snapshot, while an
online node runs an older engine, or while one of the release's new rules is not imported.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from culture_rules import deploy_data
from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
from culture_rules.cli._errors import EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import ID, collect_files, sections_overview, seg
from culture_rules.cli.registry import Context, Param, Verb
from culture_rules.client.http import ApiError

NOUN = "rollout"
SUMMARY = (
    "Rollout rolls this CLI's release out to every node, one dry-run-first step per verb: "
    "idle, pause, upgrade --host, seed, import, enable, resume, verify."
)
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "superseded"})
"""Run statuses that are final; any other status is a run still going."""
SHARED_VENV = "shared_venv"
INSTALL_SH = "install_sh"
_RUNS_LIMIT = 1000
_LIST_CAP = 64  # an outcome value list holds at most 64 items (the rollout document's bound)
_OUTCOME_BYTES = 4096
_NO_BUILTIN = "no_builtin"
_VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)(.*)$")

WHEEL = Param("wheel", help="the culture_rules-<version>-*.whl to roll out", required=True)
BUNDLE = Param("bundle", help="bundle directory (default: the one this wheel carries)")


# --------------------------------------------------------------------------- the release


def _release() -> dict[str, Any]:
    """This CLI's release manifest ``{version, new_rules, new_variables}`` (tests patch it)."""
    return deploy_data.release()


def release_version() -> str:
    return str(_release()["version"])


def _bundle_dir(bundle: str | None) -> Path:
    """``--bundle DIR`` or the bundle this wheel carries; a DIR of another release is refused."""
    if not bundle:
        return deploy_data.bundle_dir()
    root = Path(bundle)
    if not root.is_dir():
        raise CliError(EXIT_USER_ERROR, f"no such bundle directory: {bundle}", "omit --bundle")
    manifest = root / "release.json"
    if manifest.is_file():
        other = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        if other != release_version():
            raise CliError(
                EXIT_USER_ERROR,
                f"the bundle in {bundle} is release {other}, "
                f"this CLI's release is {release_version()}",
                "omit --bundle, or run the CLI from the bundle's release",
            )
    return root


def _seed_list(bundle: str | None) -> list[dict[str, Any]]:
    if not bundle:
        return deploy_data.variables()
    path = _bundle_dir(bundle) / "variables.json"
    if not path.is_file():
        raise CliError(EXIT_USER_ERROR, f"no variables.json in {bundle}", "omit --bundle")
    return json.loads(path.read_text(encoding="utf-8"))


def wheel_version(path: str) -> str:
    """The version in a wheel filename ``culture_rules-<version>-<tags>.whl``."""
    name = Path(path).name
    parts = name[: -len(".whl")].split("-") if name.endswith(".whl") else []
    if len(parts) < 5 or parts[0] != "culture_rules":
        raise CliError(
            EXIT_USER_ERROR,
            f"{name!r} is not a culture_rules wheel",
            "name the wheel built for this release: --wheel culture_rules-<v>-py3-none-any.whl",
        )
    return parts[1]


def check_wheel(path: str) -> Path:
    """``--wheel`` must exist and be this CLI's release (the operator names it explicitly)."""
    version = wheel_version(path)
    if version != release_version():
        raise CliError(
            EXIT_USER_ERROR,
            f"the wheel is release {version}, but this CLI's release is {release_version()}",
            "run the CLI from the new release's venv, or name that release's wheel",
        )
    wheel = Path(path)
    if not wheel.is_file():
        raise CliError(EXIT_USER_ERROR, f"no such wheel: {path}", "build it with 'uv build'")
    return wheel


def _parse_version(value: Any) -> tuple[tuple[int, ...], str] | None:
    match = _VERSION_RE.match(value) if isinstance(value, str) else None
    if match is None:
        return None
    nums = [int(x) for x in match.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums), match.group(2)


def is_older(version: Any, release: str) -> bool:
    """Whether a node's ``version`` is older than ``release``.

    Numeric release parts compare as numbers; at equal numbers a plain release is newest and
    a pre/dev/local suffix that differs from the release's counts as older. An unknown
    (``None``) or unparseable version is older unless it equals the release exactly.
    """
    mine, theirs = _parse_version(version), _parse_version(release)
    if mine is None or theirs is None:
        return version != release
    if mine[0] != theirs[0]:
        return mine[0] < theirs[0]
    return bool(mine[1]) and mine[1] != theirs[1]


# --------------------------------------------------------------------------- API helpers


def _get(ctx: Context, path: str, query: Mapping[str, Any] | None = None) -> Any:
    return _api.call(lambda: ctx.client.request("GET", path, query=query))


def _optional_get(ctx: Context, path: str) -> Any:
    """``GET path``, or None when it answers 404."""

    def fetch() -> Any:
        try:
            return ctx.client.request("GET", path)
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

    return _api.call(fetch)


def _rollout_path(version: str) -> str:
    return f"/rollouts/{seg(version)}"


def _rollout_doc(ctx: Context, version: str) -> dict[str, Any] | None:
    return _optional_get(ctx, _rollout_path(version))


def _steps(doc: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict((doc or {}).get("steps") or {})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _fit(outcome: Mapping[str, Any]) -> dict[str, Any]:
    """An outcome inside the rollout document's bounds: lists capped, else replaced by counts."""
    fitted = {
        k: (list(v)[:_LIST_CAP] if isinstance(v, list | tuple) else v) for k, v in outcome.items()
    }
    if len(json.dumps(fitted)) <= _OUTCOME_BYTES:
        return fitted
    return {k: (f"{len(v)} item(s)" if isinstance(v, list) else v) for k, v in fitted.items()}


def record(
    ctx: Context,
    step: str,
    outcome: Mapping[str, Any],
    *,
    snapshot: list[str] | None = None,
    applied_at: str | None = None,
) -> dict[str, Any]:
    """Merge one step's outcome into this release's rollout document (read, merge, ``PUT``)."""
    version = release_version()
    current = _rollout_doc(ctx, version) or {}
    steps = _steps(current)
    steps[step] = {"applied_at": applied_at or _now(), "outcome": _fit(outcome)}
    body = {
        "version": version,
        "snapshot": list(current.get("snapshot") or []) if snapshot is None else snapshot,
        "steps": steps,
    }
    return _api.call(lambda: ctx.client.request("PUT", _rollout_path(version), body=body))


def _record_if_allowed(ctx: Context, step: str, outcome: Mapping[str, Any]) -> dict[str, Any]:
    """Record a step of an editor verb: the document write is admin, so a 403 is reported."""
    try:
        record(ctx, step, outcome)
    except CliError as exc:
        cause = exc.__cause__
        if not (isinstance(cause, ApiError) and cause.status == 403):
            raise
        return {"recorded": False, "reason": "recording the step needs the admin role"}
    return {"recorded": True}


def active_runs(ctx: Context) -> list[dict[str, Any]]:
    """Runs whose status is not terminal (running ones first queried by status, then newest)."""
    seen: dict[str, dict[str, Any]] = {}
    for query in ({"status": "running", "limit": _RUNS_LIMIT}, {"limit": _RUNS_LIMIT}):
        for run in _get(ctx, "/runs", query).get("items", []):
            if run.get("status") not in TERMINAL:
                seen[str(run.get("id"))] = run
    return sorted(seen.values(), key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))))


def _rules(ctx: Context) -> list[dict[str, Any]]:
    return list(_get(ctx, "/rules").get("items", []))


def _enabled(rules: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted(str(r["id"]) for r in rules if r.get("enabled"))


def _toggle(ctx: Context, name: str) -> bool:
    """``POST /controls/<name>``; True when the engine was already in that state (409)."""

    def post() -> bool:
        try:
            ctx.client.request("POST", f"/controls/{name}")
        except ApiError as exc:
            if exc.status == 409:
                return True
            raise
        return False

    return bool(_api.call(post))


def envelope(verb: str, ctx: Context, **fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "verb": f"rollout {verb}",
        "release": release_version(),
        "applied": ctx.apply,
        "dry_run": not ctx.apply,
    }
    out.update(fields)
    return out


def _run_line(run: Mapping[str, Any]) -> str:
    return f"  {run.get('id')}  {run.get('status')}  rule={run.get('rule_id')}"


# --------------------------------------------------------------------------- read-only verbs


def _overview(ctx: Context) -> Any:
    return sections_overview(NOUN, SUMMARY, ctx, "/rollouts")


def _list(ctx: Context) -> Any:
    return _get(ctx, "/rollouts")


def _show(ctx: Context, id: str) -> Any:
    return _get(ctx, _rollout_path(id))


def _idle(ctx: Context) -> dict[str, Any]:
    runs = active_runs(ctx)
    lines = [f"release {release_version()}: {len(runs)} run(s) not terminal"]
    lines += [_run_line(r) for r in runs]
    lines.append(
        "idle: pause may run (culture-rules rollout pause)"
        if not runs
        else "not idle: wait for these runs before 'rollout pause' (or pass --force)"
    )
    return {
        "verb": "rollout idle",
        "release": release_version(),
        "idle": not runs,
        "runs": runs,
        "lines": lines,
    }


def install_flags(name: str, install: Mapping[str, Any]) -> list[str]:
    """``install.sh`` flags from a machine record's install section (no ``--wheel``/``--apply``).

    A field left ``None`` (or empty) keeps install.sh's own default, so its flag is omitted.
    """
    flags = ["--node-name", str(install.get("node_name") or name)]
    for secret in install.get("secrets") or ():
        flags += ["--secret", str(secret)]
    optional = (
        ("--gate-run-as", install.get("gate_run_as")),
        ("--extras", ",".join(install.get("extras") or ()) or None),
        ("--wheelhouse", install.get("wheelhouse")),
        ("--python", install.get("python")),
        ("--events-host", install.get("events_host")),
        ("--events-port", install.get("events_port")),
    )
    for flag, value in optional:
        if value not in (None, ""):
            flags += [flag, str(value)]
    return flags


def host_order(machines: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Hosts in upgrade order: the shared-venv host(s) first (the API lives there), then the
    install.sh hosts by name, then the machines without an install section (not upgradeable)."""

    def rank(m: Mapping[str, Any]) -> tuple[int, str]:
        mode = (m.get("install") or {}).get("mode")
        return ({SHARED_VENV: 0, INSTALL_SH: 1}.get(mode, 2), str(m.get("name") or m.get("id")))

    return [dict(m) for m in sorted(machines, key=rank)]


def _host_entry(machine: Mapping[str, Any]) -> dict[str, Any]:
    name = str(machine.get("name") or machine.get("id"))
    install = machine.get("install")
    if not install:
        return {"host": name, "mode": None, "note": "not upgradeable: add an install section"}
    if install.get("mode") == SHARED_VENV:
        note = "uv pip install the wheel into the shared venv, restart the API and the node"
        return {"host": name, "mode": SHARED_VENV, "note": note}
    return {"host": name, "mode": INSTALL_SH, "flags": install_flags(name, install)}


def _host_line(entry: Mapping[str, Any]) -> str:
    if entry["mode"] is None:
        return f"  -  {entry['host']}: {entry['note']}"
    head = f"  upgrade --host {entry['host']} --wheel WHEEL --apply"
    if entry["mode"] == SHARED_VENV:
        return f"{head}   ({SHARED_VENV}: {entry['note']}; never install.sh)"
    return f"{head}   (install.sh {' '.join(entry['flags'])})"


def _plan(ctx: Context, wheel: str) -> dict[str, Any]:
    version, release = release_version(), _release()
    machines = _get(ctx, "/machines").get("items", [])
    doc = _rollout_doc(ctx, version)
    if doc is not None and "pause" in _steps(doc):
        snapshot, source = list(doc.get("snapshot") or []), "the rollout document (pause)"
    else:
        snapshot, source = _enabled(_rules(ctx)), "the rules enabled now (pause takes it)"
    check_wheel(wheel)
    hosts = [_host_entry(m) for m in host_order(machines)]
    new_rules, new_vars = list(release.get("new_rules") or []), release.get("new_variables") or []
    lines = [
        f"rollout plan for release {version} (wheel {Path(wheel).name}); nothing was written",
        "steps, one command each, in order (each is a dry run until --apply):",
        "  1. culture-rules rollout idle",
        "  2. culture-rules rollout pause --apply",
        "  3. upgrade each host: the shared-venv host first, then the install.sh hosts by name",
        *(_host_line(h) for h in hosts),
        "  4. culture-rules rollout seed --apply",
        "  5. culture-rules rollout import --apply",
        "  6. culture-rules rollout enable --apply",
        "  7. culture-rules rollout resume --apply",
        "  8. culture-rules rollout verify",
        f"snapshot ({len(snapshot)} rule(s), from {source}): {', '.join(snapshot) or '-'}",
        f"new rules: {', '.join(new_rules) or '-'}",
        f"new variables: {', '.join(map(str, new_vars)) or '-'}",
    ]
    return {
        "verb": "rollout plan",
        "release": version,
        "wheel": str(wheel),
        "steps": ["idle", "pause", "upgrade", "seed", "import", "enable", "resume", "verify"],
        "hosts": hosts,
        "snapshot": snapshot,
        "snapshot_source": source,
        "new_rules": new_rules,
        "new_variables": list(new_vars),
        "lines": lines,
    }


# --------------------------------------------------------------------------- pause / resume


def _pause(ctx: Context, force: bool = False) -> dict[str, Any]:
    """Snapshot the enabled rules into the rollout document, then pause the engine."""
    version = release_version()
    runs = active_runs(ctx)
    if runs and not force:
        raise CliError(
            EXIT_USER_ERROR,
            f"pause refused: {len(runs)} run(s) not terminal: "
            + ", ".join(str(r.get("id")) for r in runs[:10]),
            "run 'culture-rules rollout idle' and wait until it reports idle (or pass --force)",
        )
    doc = _rollout_doc(ctx, version)
    kept = doc is not None and "pause" in _steps(doc)
    snapshot = list(doc.get("snapshot") or []) if kept else _enabled(_rules(ctx))
    paused = bool(_get(ctx, "/controls").get("paused"))
    out = envelope(
        "pause",
        ctx,
        snapshot=snapshot,
        snapshot_kept=kept,
        forced=[str(r.get("id")) for r in runs],
        would=[
            {"method": "PUT", "path": _rollout_path(version), "snapshot": len(snapshot)},
            {"method": "POST", "path": "/controls/pause", "skip": paused},
        ],
    )
    lines = [
        f"snapshot: {len(snapshot)} enabled rule(s)"
        + (" (kept from the recorded pause step)" if kept else ""),
        *(f"  {rid}" for rid in snapshot),
    ]
    if runs:
        lines.append(f"forced past {len(runs)} run(s) not terminal")
    if not ctx.apply:
        lines.append(
            "dry-run: would store the snapshot, then "
            + ("report the engine already paused" if paused else "POST /controls/pause")
        )
        return {**out, "engine": "paused" if paused else "running", "lines": lines}
    outcome = {"snapshot": len(snapshot), "forced": out["forced"], "engine": "pausing"}
    record(ctx, "pause", outcome, snapshot=snapshot)  # the snapshot lands before the pause
    already = paused or _toggle(ctx, "pause")
    state = "already paused" if already else "paused"
    record(ctx, "pause", {**outcome, "engine": state}, snapshot=snapshot)
    lines.append(f"engine {state}")
    return {**out, "engine": state, "lines": lines}


def _resume(ctx: Context) -> dict[str, Any]:
    version = release_version()
    paused = bool(_get(ctx, "/controls").get("paused"))
    previous = _steps(_rollout_doc(ctx, version)).get("resume")
    out = envelope(
        "resume", ctx, would={"method": "POST", "path": "/controls/resume", "skip": not paused}
    )
    if not ctx.apply:
        what = "POST /controls/resume" if paused else "report the engine already resumed"
        return {
            **out,
            "engine": "paused" if paused else "running",
            "lines": [f"dry-run: would {what} and record the resume step"],
        }
    already = not paused or _toggle(ctx, "resume")
    state = "already resumed" if already else "resumed"
    # an engine already resumed keeps the recorded resume time (verify's "since resume")
    keep = previous.get("applied_at") if already and isinstance(previous, Mapping) else None
    record(ctx, "resume", {"engine": state}, applied_at=keep)
    controls = _get(ctx, "/controls")
    return {
        **out,
        "engine": state,
        "controls": controls,
        "lines": [f"engine {state}", f"controls: {json.dumps(controls)}"],
    }


# --------------------------------------------------------------------------- seed / import


def _seed(ctx: Context, bundle: str | None = None, force: bool = False) -> dict[str, Any]:
    """PUT each seed variable that does not exist yet (``--force``: every one)."""
    seeds = _seed_list(bundle)
    existing = {str(v.get("name")) for v in _get(ctx, "/variables").get("items", [])}
    to_set = [s for s in seeds if force or s["name"] not in existing]
    skipped = [s["name"] for s in seeds if s not in to_set]
    would = [
        {
            "method": "PUT",
            "path": f"/variables/{seg(s['name'])}",
            "body": {"value": s["value"], "description": s.get("description")},
        }
        for s in to_set
    ]
    lines = [f"skip {n}: already set (--force replaces it)" for n in skipped]
    out = envelope("seed", ctx, set=[s["name"] for s in to_set], skipped=skipped, would=would)
    if not ctx.apply:
        lines += [f"dry-run: would set {w['path'].rsplit('/', 1)[-1]}" for w in would]
        return {**out, "lines": lines}
    for w in would:
        _api.call(lambda w=w: ctx.client.request("PUT", w["path"], body=w["body"]))
        lines.append(f"set {w['path'].rsplit('/', 1)[-1]}")
    record(ctx, "seed", {"set": out["set"], "skipped": skipped})
    lines.append(f"seed recorded: {len(to_set)} set, {len(skipped)} skipped")
    return {**out, "lines": lines}


def _import(ctx: Context, bundle: str | None = None) -> dict[str, Any]:
    """Import the bundle's workflows and rules in one ``POST /import`` (actors are not)."""
    version = release_version()
    if "seed" not in _steps(_rollout_doc(ctx, version)):
        raise CliError(
            EXIT_USER_ERROR,
            f"import refused: release {version}'s seed step is not recorded",
            "run 'culture-rules rollout seed --apply' first",
        )
    root = str(_bundle_dir(bundle))
    files = {**collect_files(root, None, "workflows"), **collect_files(root, None, "rules")}
    plan = _api.call(
        lambda: ctx.client.request("POST", "/import", body={"files": files, "apply": ctx.apply})
    )
    counts: dict[str, int] = {}
    for change in plan.get("changes", []):
        counts[change.get("action", "?")] = counts.get(change.get("action", "?"), 0) + 1
    out = envelope(
        "import",
        ctx,
        applied=bool(plan.get("applied")),
        would={"method": "POST", "path": "/import", "files": sorted(files)},
        result=plan,
    )
    lines = [
        ("imported" if out["applied"] else "dry-run: would import")
        + f" {len(files)} file(s): "
        + ", ".join(f"{n} {a}" for a, n in sorted(counts.items()))
    ]
    if ctx.apply:
        out["record"] = _record_if_allowed(ctx, "import", counts)
        lines.append("import recorded" if out["record"]["recorded"] else out["record"]["reason"])
    return {**out, "lines": lines}


# --------------------------------------------------------------------------- enable


def _enable_guards(ctx: Context, version: str) -> list[str]:
    """The snapshot recorded at pause; refuses while a node is behind or no pause recorded."""
    doc = _rollout_doc(ctx, version)
    if "pause" not in _steps(doc):
        raise CliError(
            EXIT_USER_ERROR,
            f"enable refused: release {version} has no snapshot (its pause step is not recorded)",
            "run 'culture-rules rollout pause --apply' first",
        )
    behind = [
        f"{m.get('name')} ({m.get('engine_version') or 'unknown'})"
        for m in _get(ctx, "/machines/status").get("items", [])
        if m.get("online") and is_older(m.get("engine_version"), version)
    ]
    if behind:
        raise CliError(
            EXIT_USER_ERROR,
            f"enable refused: online node(s) run an engine older than {version}: "
            + ", ".join(behind),
            "run 'culture-rules rollout upgrade --host <name> --wheel <whl> --apply' first",
        )
    return list((doc or {}).get("snapshot") or [])


def _enable(ctx: Context) -> dict[str, Any]:
    """Re-enable exactly the snapshot plus the release's new rules; list every rule left off."""
    version = release_version()
    snapshot = _enable_guards(ctx, version)
    new_rules = [str(r) for r in _release().get("new_rules") or []]
    rules = _rules(ctx)
    ids, enabled = {str(r["id"]) for r in rules}, set(_enabled(rules))
    absent_new = [r for r in new_rules if r not in ids]
    if absent_new:
        raise CliError(
            EXIT_USER_ERROR,
            f"enable refused: the release's new rule(s) are not imported: {', '.join(absent_new)}",
            "run 'culture-rules rollout import --apply' first",
        )
    target = list(dict.fromkeys([*snapshot, *new_rules]))
    missing = [r for r in target if r not in ids]  # a snapshot rule deleted since the pause
    to_enable = [r for r in target if r in ids and r not in enabled]
    left = sorted(ids - enabled - set(to_enable))
    out = envelope(
        "enable",
        ctx,
        enable=to_enable,
        already_enabled=[r for r in target if r in enabled],
        missing=missing,
        left_disabled=left,
        would=[{"method": "POST", "path": f"/rules/{seg(r)}/enable"} for r in to_enable],
    )
    verb = "enabled" if ctx.apply else "dry-run: would enable"
    if ctx.apply:
        for rid in to_enable:
            _api.call(lambda rid=rid: ctx.client.request("POST", f"/rules/{seg(rid)}/enable"))
        outcome = {"enabled": to_enable, "left_disabled": left, "missing": missing}
        out["record"] = _record_if_allowed(ctx, "enable", outcome)
    lines = [
        f"{verb} {len(to_enable)} rule(s): {', '.join(to_enable) or '-'}",
        f"already enabled: {len(out['already_enabled'])}",
        f"left disabled ({len(left)}): {', '.join(left) or '-'}",
    ]
    if missing:
        lines.append(f"snapshot rule(s) no longer present: {', '.join(missing)}")
    return {**out, "lines": lines}


# --------------------------------------------------------------------------- upgrade


def _upgrade(ctx: Context, host: str, wheel: str) -> dict[str, Any]:
    from culture_rules.cli import _rollout_upgrade  # noqa: PLC0415 - it imports this module

    return _rollout_upgrade.upgrade(ctx, host, wheel)


# --------------------------------------------------------------------------- verify


def _check(name: str, ok: bool, detail: str, fix: str = "") -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail, "fix": "" if ok else fix}


def _check_nodes(ctx: Context, version: str) -> dict[str, Any]:
    nodes = _get(ctx, "/machines/status").get("items", [])
    off = [
        f"{m.get('name')}={m.get('engine_version')}"
        for m in nodes
        if m.get("engine_version") != version
    ]
    if not nodes:
        detail = "no enrolled nodes"
    elif off:
        detail = "not on the release: " + ", ".join(off)
    else:
        detail = f"{len(nodes)} node(s) on {version}"
    return _check(
        "nodes",
        bool(nodes) and not off,
        detail,
        "culture-rules rollout upgrade --host <name> --wheel <whl> --apply",
    )


def _check_api(ctx: Context, version: str) -> dict[str, Any]:
    api = _get(ctx, "/health").get("version")
    return _check(
        "api",
        api == version,
        f"API version {api}",
        "upgrade the API host (its shared-venv upgrade restarts the API)",
    )


def _check_engine(ctx: Context) -> dict[str, Any]:
    paused = bool(_get(ctx, "/controls").get("paused"))
    return _check(
        "engine",
        not paused,
        "paused" if paused else "running",
        "culture-rules rollout resume --apply",
    )


def _check_rules(ctx: Context, doc: Mapping[str, Any] | None) -> dict[str, Any]:
    fix = "culture-rules rollout enable --apply"
    if "pause" not in _steps(doc):
        return _check("rules", False, "no snapshot recorded (pause step missing)", fix)
    want = set((doc or {}).get("snapshot") or []) | set(_release().get("new_rules") or [])
    have = set(_enabled(_rules(ctx)))
    if want == have:
        return _check("rules", True, f"{len(have)} enabled = snapshot + new rules")
    detail = (
        f"not enabled: {', '.join(sorted(want - have)) or '-'}; "
        f"enabled beyond the snapshot: {', '.join(sorted(have - want)) or '-'}"
    )
    return _check("rules", False, detail, fix)


def _parse_ts(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _mentions_no_builtin(error: Any) -> bool:
    if not isinstance(error, Mapping):
        return False
    return error.get("code") == _NO_BUILTIN or _NO_BUILTIN in str(error.get("message") or "")


def _check_no_builtin(ctx: Context, doc: Mapping[str, Any] | None) -> dict[str, Any]:
    resume = _steps(doc).get("resume") or {}
    since = _parse_ts(resume.get("applied_at")) if isinstance(resume, Mapping) else None
    if since is None:
        return _check(
            "no_builtin", False, "no resume step recorded", "culture-rules rollout resume --apply"
        )
    failed = _get(ctx, "/runs", {"status": "failed", "limit": _RUNS_LIMIT}).get("items", [])
    hits = []
    for run in failed:
        at = _parse_ts(run.get("finished_at") or run.get("created_at"))
        if at is None or at < since:
            continue
        full = _get(ctx, f"/runs/{seg(str(run['id']))}")
        errors = [full.get("error"), *(s.get("error") for s in full.get("steps") or ())]
        if any(_mentions_no_builtin(e) for e in errors):
            hits.append(str(run["id"]))
    detail = f"run(s) failed with no_builtin: {', '.join(hits)}" if hits else "none since resume"
    return _check(
        "no_builtin",
        not hits,
        detail,
        "inspect with 'culture-rules runs show <id>'; the node lacks a builtin",
    )


def _verify(ctx: Context) -> dict[str, Any]:
    version = release_version()
    doc = _rollout_doc(ctx, version)
    checks = [
        _check_nodes(ctx, version),
        _check_api(ctx, version),
        _check_engine(ctx),
        _check_rules(ctx, doc),
        _check_no_builtin(ctx, doc),
    ]
    failed = [c for c in checks if not c["ok"]]
    if failed:
        raise CliError(
            EXIT_USER_ERROR,
            f"verify: {len(failed)} of {len(checks)} check(s) failed for release {version}:\n"
            + "\n".join(f"- {c['check']}: {c['detail']} (fix: {c['fix']})" for c in failed),
            "fix each failed check, then re-run 'culture-rules rollout verify'",
        )
    return {
        "verb": "rollout verify",
        "release": version,
        "ok": True,
        "checks": checks,
        "lines": [f"ok  {c['check']}: {c['detail']}" for c in checks]
        + [f"release {version} verified"],
    }


# --------------------------------------------------------------------------- registry


VERBS: list[Verb] = [
    Verb(NOUN, "overview", "Describe the rollout noun and its steps", _overview),
    Verb(NOUN, "list", "List rollout documents (one per release)", _list),
    Verb(NOUN, "show", "Show one release's rollout document", _show, (ID,)),
    Verb(
        NOUN,
        "plan",
        "Print the steps, hosts with install flags, snapshot and new rules (writes nothing)",
        _plan,
        (WHEEL,),
    ),
    Verb(NOUN, "idle", "List runs that are not terminal (pause needs none)", _idle),
    Verb(
        NOUN,
        "pause",
        "Store the enabled-rule snapshot, then pause the engine",
        _pause,
        (Param("force", "boolean", "pause even while runs are not terminal"),),
        True,
        "admin",
    ),
    Verb(
        NOUN,
        "seed",
        "Set the release's seed variables that do not exist yet",
        _seed,
        (BUNDLE, Param("force", "boolean", "also replace variables that already exist")),
        True,
        "admin",
    ),
    Verb(
        NOUN,
        "import",
        "Import the bundle's workflows and rules (after seed)",
        _import,
        (BUNDLE,),
        True,
        "editor",
    ),
    Verb(
        NOUN,
        "enable",
        "Re-enable the snapshot plus the release's new rules; list the rules left disabled",
        _enable,
        (),
        True,
        "editor",
    ),
    Verb(
        NOUN,
        "upgrade",
        "Upgrade one host's node to the wheel (install.sh over ssh, or the shared venv)",
        _upgrade,
        (Param("host", help="the machine record to upgrade", required=True), WHEEL),
        True,
        "admin",
    ),
    Verb(NOUN, "resume", "Resume the engine", _resume, (), True, "admin"),
    Verb(NOUN, "verify", "Check versions, engine, enabled rules and no_builtin failures", _verify),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Rollout: roll this release out, one step per verb.")
