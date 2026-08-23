"""Pytest-collectable validation for the public skill package."""
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "kanban-implementation-workflow" / "SKILL.md"
POLICY = ROOT / "examples" / "project-policy.yaml"
PUBLIC_TEXT_FILES = [
    ROOT / "README.md",
    SKILL,
    POLICY,
    ROOT / "docs" / "policy-resolution.md",
    ROOT / "docs" / "profile-roles.md",
    ROOT / "docs" / "tracker-adapters.md",
    ROOT / "docs" / "reviewer-reliability.md",
    ROOT / "LICENSE",
    ROOT / "tests" / "test_skill_frontmatter.py",
    ROOT / "requirements-dev.txt",
    ROOT / ".gitignore",
    ROOT / ".github" / "workflows" / "ci.yml",
]


def _frontmatter():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    prefix, separator, remainder = text.partition("\n---\n")
    assert separator, "missing frontmatter terminator"
    return text, prefix[4:], remainder


def test_skill_frontmatter_and_layout():
    text, frontmatter, _body = _frontmatter()
    data = yaml.safe_load(frontmatter)
    assert data["name"] == SKILL.parent.name
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(data["version"]))
    assert data["license"] == "MIT"
    assert set(data["platforms"]) >= {"linux", "macos", "windows"}

    description = data["description"]
    assert len(description) <= 60
    assert description.endswith(".")
    assert "forgejo" in description.lower()
    assert "github" in description.lower()

    assert len(text) <= 100_000
    for required_path in PUBLIC_TEXT_FILES:
        assert required_path.exists(), required_path


def test_policy_parses_and_declares_roles_safety_and_deployment():
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    assert policy["tracker"]["kind"] in {"forgejo", "github"}
    assert policy["tracker"]["dependency_source"] in {"body_marker", "native", "sub_issues"}
    if policy["tracker"]["kind"] == "forgejo":
        assert policy["tracker"]["dependency_source"] != "sub_issues"
    assert set(policy["profiles"]) >= {
        "orchestrator",
        "implementer",
        "reviewer",
        "qa_ui",
        "release_operator",
    }
    assert policy["deployment"]["mode"] in {"gitops_only", "direct_allowed", "release_only", "unspecified"}
    assert policy["deployment"]["direct_cluster_mutation"] == "forbidden"
    assert policy["safety"]["protected_paths"] == []
    if policy["verification"]["required_for_ui_changes"]:
        assert policy["verification"]["ui_smoke"]


def test_public_files_have_no_machine_or_secret_identifiers():
    # Build sensitive strings instead of embedding the exact forbidden values in
    # this test, so the test file can be included in the scan safely.
    forbidden = [
        "HERMES_" + "CUSTOM_",
        "/" + "Users/",
        "/" + "home/",
        "C:" + "\\" + "Users",
        r"![A-Za-z0-9]{16,}:[A-Za-z0-9.-]+\.[a-z]{2,}",
        r"\b(?:gh[pousr]|glpat)_[A-Za-z0-9]{20,}\b",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_TEXT_FILES)
    assert "home" + "lab/" not in combined
    assert "git" + ".intelligenttools.ai" not in combined
    for value in forbidden[:4]:
        assert value not in combined, value

    # The regex checks are applied to content rather than treated as literals.
    assert not re.search(forbidden[4], combined)
    assert not re.search(forbidden[5], combined)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8").lower()
    assert "forgejo" in skill and "github" in skill
    assert "tea issues list" in skill
    assert "gh api" in skill
    assert "--paginate" in skill and "--page" in skill


if __name__ == "__main__":
    test_skill_frontmatter_and_layout()
    test_policy_parses_and_declares_roles_safety_and_deployment()
    test_public_files_have_no_machine_or_secret_identifiers()
    print("skill package validation: OK")
