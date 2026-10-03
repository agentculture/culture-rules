"""t34: ignore rules, publish paths, sonar and markdownlint config for the web build."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _ignored(path: str) -> bool:
    done = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, capture_output=True
    )
    assert done.returncode in (0, 1), done.stderr
    return done.returncode == 0


def test_build_outputs_are_gitignored():
    for path in (
        "web/node_modules/react/index.js",
        "node_modules/x/y.js",
        "web/dist/index.html",
        "web/tsconfig.app.tsbuildinfo",
        "culture_rules/web_dist/index.html",
    ):
        assert _ignored(path), path


def test_web_src_lib_is_not_ignored():
    # the generic python `lib/` rule must not swallow the frontend's src/lib
    for path in ("web/src/lib/format.ts", "web/src/lib/deep/x.tsx"):
        assert not _ignored(path), path


def test_publish_workflow_watches_web_and_builds_it_before_uv_build():
    text = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    head = text.split("jobs:")[0]
    assert head.count('"web/**"') == 2  # push + pull_request
    jobs = re.split(r"^  (?=[a-z-]+:\n)", text.split("jobs:\n")[1], flags=re.M)
    builders = [j for j in jobs if "uv build" in j]
    assert len(builders) == 2
    for job in builders:
        assert job.index("npm ci") < job.index("npm run build") < job.index("uv build")
        assert re.search(r"actions/setup-node@[0-9a-f]{40}", job)
        assert job.index("setup-node") < job.index("npm ci")


def test_sonar_analyses_web_src_and_excludes_build_output():
    props = (ROOT / "sonar-project.properties").read_text(encoding="utf-8")
    sources = re.search(r"^sonar\.sources=(.*)$", props, re.M).group(1).split(",")
    assert "culture_rules" in sources and "web/src" in sources
    exclusions = re.search(r"^sonar\.exclusions=(.*)$", props, re.M).group(1)
    for pattern in ("web/dist/**", "web/node_modules/**", "culture_rules/web_dist/**"):
        assert pattern in exclusions


def test_markdownlint_ignores_web_build_output():
    cfg = (ROOT / ".markdownlint-cli2.yaml").read_text(encoding="utf-8")
    for pattern in ("web/dist/**", "web/node_modules/**", "culture_rules/web_dist/**"):
        assert f'"{pattern}"' in cfg
