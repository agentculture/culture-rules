"""t31: the pure planner behind ``actors enrol-agents`` (server.yaml + culture.yaml files)."""

from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from culture_rules.actors import enrol_agents as ea  # noqa: E402


def write_mesh(tmp_path, agents, server="spark", name="server.yaml"):
    """``agents``: suffix -> culture.yaml text (None = missing workdir)."""
    listed = {}
    for suffix, text in agents.items():
        wd = tmp_path / "work" / suffix
        listed[suffix] = str(wd)
        if text is not None:
            wd.mkdir(parents=True, exist_ok=True)
            (wd / "culture.yaml").write_text(text)
        elif (wd / "culture.yaml").exists():
            (wd / "culture.yaml").unlink()
    lines = [f"server:\n  name: {server}\n  host: 127.0.0.1\n  port: 6667\nagents:"]
    lines += [f"  {k}: {v}" for k, v in listed.items()]
    f = tmp_path / name
    f.write_text("\n".join(lines) + "\n")
    return f


FLAT = "suffix: {s}\nbackend: {b}\nmodel: {m}\n"


def flat(s, b="claude", m="opus"):
    return FLAT.format(s=s, b=b, m=m)


def desired(path, machine=None):
    return ea.desired_actors(ea.read_server_yaml(path), machine=machine)


def apply_plan(plan, existing):
    """Simulate the server applying a plan to a list of actor docs."""
    docs = {d["id"]: dict(d) for d in existing}
    for ch in plan.changes:
        if ch.action in ("create", "update"):
            docs[ch.id] = dict(ch.body)
        elif ch.action == "disable":
            if ch.body:
                docs[ch.id] = dict(ch.body)
            docs[ch.id]["enabled"] = False
    return list(docs.values())


def test_creates_one_actor_per_listed_agent(tmp_path):
    f = write_mesh(tmp_path, {"a": flat("a"), "b": flat("b", "codex", "gpt")})
    d = desired(f)
    plan = ea.plan_enrolment([], d.actors, machine=d.machine)
    assert [(c.action, c.id) for c in plan.changes] == [
        ("create", "spark-a"),
        ("create", "spark-b"),
    ]
    body = plan.changes[1].body
    assert body["kind"] == "agent" and body["machine"] == "spark"
    assert body["harness"] == "codex" and body["model"] == "gpt" and body["enabled"] is True
    assert body["params"]["enrolled_by"] == ea.MARKER
    assert body["config_source"] == "repo" and body["repo"].endswith("/b")


def test_second_plan_is_empty(tmp_path):
    f = write_mesh(tmp_path, {"a": flat("a")})
    d = desired(f)
    first = ea.plan_enrolment([], d.actors, machine=d.machine)
    after = apply_plan(first, [])
    again = ea.plan_enrolment(after, d.actors, machine=d.machine)
    assert again.changes == []


def test_changed_model_is_an_update_and_keeps_other_fields(tmp_path):
    f = write_mesh(tmp_path, {"a": flat("a", m="opus")})
    d = desired(f)
    after = apply_plan(ea.plan_enrolment([], d.actors, machine="spark"), [])
    after[0]["capabilities"] = ["review"]
    after[0]["params"]["custom"] = 1
    f2 = write_mesh(tmp_path, name="again.yaml", agents={"a": flat("a", m="sonnet")})
    d2 = desired(f2)
    plan = ea.plan_enrolment(after, d2.actors, machine="spark")
    assert [(c.action, c.id) for c in plan.changes] == [("update", "spark-a")]
    body = plan.changes[0].body
    assert body["model"] == "sonnet" and body["capabilities"] == ["review"]
    assert body["params"]["custom"] == 1


def test_removed_agent_is_disabled_not_deleted_and_stays_quiet(tmp_path):
    f = write_mesh(tmp_path, {"a": flat("a"), "b": flat("b")})
    d = desired(f)
    after = apply_plan(ea.plan_enrolment([], d.actors, machine="spark"), [])
    f2 = write_mesh(tmp_path, name="v2.yaml", agents={"a": flat("a")})
    d2 = desired(f2)
    plan = ea.plan_enrolment(after, d2.actors, machine="spark")
    assert [(c.action, c.id) for c in plan.changes] == [("disable", "spark-b")]
    after2 = apply_plan(plan, after)
    assert {x["id"] for x in after2} == {"spark-a", "spark-b"}  # nothing deleted
    assert ea.plan_enrolment(after2, d2.actors, machine="spark").changes == []


def test_relisted_agent_is_re_enabled_only_if_this_tool_disabled_it(tmp_path):
    f = write_mesh(tmp_path, {"a": flat("a"), "b": flat("b")})
    d = desired(f)
    after = apply_plan(ea.plan_enrolment([], d.actors, machine="spark"), [])
    d_a = desired(write_mesh(tmp_path, name="v2.yaml", agents={"a": flat("a")}))
    after = apply_plan(ea.plan_enrolment(after, d_a.actors, machine="spark"), after)
    plan = ea.plan_enrolment(after, d.actors, machine="spark")
    assert [(c.action, c.id) for c in plan.changes] == [("update", "spark-b")]
    after = apply_plan(plan, after)
    b = next(x for x in after if x["id"] == "spark-b")
    assert b["enabled"] is True and "disabled_by_enrol" not in b["params"]
    # an operator-disabled actor stays disabled, with a warning
    b["enabled"] = False
    plan = ea.plan_enrolment(after, d.actors, machine="spark")
    assert plan.changes == [] and any("spark-b" in w for w in plan.warnings)


def test_hand_made_actors_are_untouched(tmp_path):
    f = write_mesh(tmp_path, {"a": flat("a")})
    d = desired(f)
    hand = {"id": "spark-zzz", "name": "x", "kind": "agent", "machine": "spark", "enabled": True}
    clash = {"id": "spark-a", "name": "mine", "kind": "agent", "machine": "spark", "enabled": True}
    plan = ea.plan_enrolment([hand, clash], d.actors, machine="spark")
    assert plan.changes == []
    assert any("spark-a" in w for w in plan.warnings)


def test_other_machines_actors_are_not_disabled(tmp_path):
    f = write_mesh(tmp_path, {"a": flat("a")})
    d = desired(f)
    other = {
        "id": "thor-q",
        "name": "thor-q",
        "kind": "agent",
        "machine": "thor",
        "enabled": True,
        "params": {"enrolled_by": ea.MARKER},
    }
    assert ea.plan_enrolment([other], d.actors, machine="spark").changes[0].action == "create"
    plan = ea.plan_enrolment([other], d.actors, machine="spark")
    assert all(c.id != "thor-q" for c in plan.changes)


def test_multi_agent_yaml_enrols_the_matching_suffix_only(tmp_path):
    multi = (
        "agents:\n  - suffix: other\n    backend: codex\n  - suffix: a\n    backend: acp\n"
        "    model: m1\n"
    )
    d = desired(write_mesh(tmp_path, {"a": multi}))
    assert [a["id"] for a in d.actors] == ["spark-a"]
    assert d.actors[0]["harness"] == "acp" and d.actors[0]["model"] == "m1"


def test_multi_agent_without_a_matching_suffix_warns_and_enrols_none(tmp_path):
    d = desired(write_mesh(tmp_path, {"a": "agents:\n  - suffix: zzz\n    backend: claude\n"}))
    assert d.actors == [] and any("'a'" in w for w in d.warnings)


def test_missing_workdir_warns_and_is_skipped(tmp_path):
    d = desired(write_mesh(tmp_path, {"a": flat("a"), "gone": None}))
    assert [a["id"] for a in d.actors] == ["spark-a"]
    assert any("gone" in w for w in d.warnings)


def test_a_missing_workdir_does_not_disable_an_enrolled_agent(tmp_path):
    d = desired(write_mesh(tmp_path, {"a": flat("a")}))
    after = apply_plan(ea.plan_enrolment([], d.actors, machine="spark"), [])
    d2 = desired(write_mesh(tmp_path, name="v2.yaml", agents={"a": None}))
    assert ea.plan_enrolment(after, d2.actors, machine="spark", listed=d2.listed).changes == []


def test_machine_override(tmp_path):
    d = desired(write_mesh(tmp_path, {"a": flat("a")}), machine="thor")
    assert d.machine == "thor" and d.actors[0]["machine"] == "thor"
    assert d.actors[0]["id"] == "spark-a"  # the nick still follows the server name


def test_unreadable_server_yaml_is_an_error(tmp_path):
    with pytest.raises(ea.EnrolError):
        ea.read_server_yaml(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("agents: {a: /x}\n")
    with pytest.raises(ea.EnrolError):  # no server.name
        ea.read_server_yaml(bad)
