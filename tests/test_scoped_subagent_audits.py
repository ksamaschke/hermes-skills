"""Validation for the scoped subagent audit skill."""
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "scoped-subagent-audits" / "SKILL.md"


def test_scoped_audit_skill_frontmatter_and_policy():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    prefix, separator, body = text.partition("\n---\n")
    assert separator
    data = yaml.safe_load(prefix[4:])
    assert data["name"] == "scoped-subagent-audits"
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(data["version"]))
    assert data["license"] == "MIT"
    assert set(data["platforms"]) >= {"linux", "macos", "windows"}
    assert len(data["description"]) <= 60
    assert data["description"].endswith(".")
    assert body.strip()
    assert "explicit scoped working set" in text
    assert "900 seconds" in text
    assert "1,200 seconds" in text
    assert "max_retries=1" in text
    assert "skills list" in text
    assert "timeout is a failed/incomplete audit" in text
    assert "parent" in text.lower() and "verify" in text.lower()
    assert len(text) <= 100_000
