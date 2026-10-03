"""Tests for culture_rules.actors.config (task t4)."""

from pathlib import Path

import pytest

pytest.importorskip("yaml")

from culture_rules.actors.config import (  # noqa: E402
    ActorConfigError,
    load_all_from_repo,
    load_from_record,
    load_from_repo,
    parse_culture_yaml,
)

FIX = Path(__file__).parent / "fixtures" / "culture_yaml"
REPO = Path(__file__).resolve().parent.parent


def test_single_agent_shape_keeps_extras():
    cfg = load_from_repo(FIX / "culture.yaml")
    assert cfg.key == "culture"
    assert cfg.harness == "colleague"
    assert cfg.model == "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
    assert cfg.extras["engine"] == "vllm-openai"
    assert cfg.extras["base_url"] == "http://localhost:8001/v1"
    assert cfg.source == "repo"


def test_agents_list_shape():
    cfgs = load_all_from_repo(FIX / "steward.yaml")
    assert [c.key for c in cfgs] == ["steward"]
    assert cfgs[0].harness == "colleague"


def test_multiple_agents_and_suffix_filter():
    text = "agents:\n- suffix: a\n  backend: claude\n  model: m1\n- suffix: b\n  backend: codex\n"
    assert [c.key for c in parse_culture_yaml(text)] == ["a", "b"]
    with pytest.raises(ActorConfigError):
        load_from_repo(FIX / "steward.yaml", suffix="nope")


def test_this_repo_equals_db_record():
    repo = load_from_repo(REPO)
    rec = load_from_record({"suffix": "culture-rules", "backend": "claude"})
    assert rec.source == "db" and repo.source == "repo"
    assert repo == rec
    assert repo.harness == "claude"


def test_record_equals_repo_with_model_and_extras():
    repo = load_from_repo(FIX / "culture.yaml")
    rec = load_from_record(
        {
            "suffix": "culture",
            "backend": "colleague",
            "model": repo.model,
            "engine": "vllm-openai",
            "base_url": "http://localhost:8001/v1",
            "channels": ["#general"],
            "system_prompt": repo.system_prompt,
            "tags": ["persistence", "colleague"],
        }
    )
    assert rec == repo


def test_record_accepts_harness_spelling():
    assert load_from_record({"key": "x", "harness": "codex"}).harness == "codex"


@pytest.mark.parametrize("name", ["culture.yaml", "steward.yaml", "culture-rules.yaml"])
def test_fixtures_load(name):
    assert load_all_from_repo(FIX / name)


def test_missing_suffix_and_missing_file(tmp_path):
    with pytest.raises(ActorConfigError):
        parse_culture_yaml("backend: claude\n")
    with pytest.raises(FileNotFoundError):
        load_from_repo(tmp_path)
    with pytest.raises(ActorConfigError):
        parse_culture_yaml("- just\n- a list\n")


def test_missing_yaml_extra_gives_clear_error(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "yaml":
            raise ImportError("no yaml")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(ActorConfigError, match="culture-rules\\[yaml\\]"):
        parse_culture_yaml("suffix: a\n")
