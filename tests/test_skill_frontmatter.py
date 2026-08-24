"""Pytest-collectable validation for the public skill package."""
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATHS = sorted((ROOT / "skills").glob("*/SKILL.md"))
CORE_SKILL = ROOT / "skills" / "kanban-implementation-workflow" / "SKILL.md"
EXPECTED_SKILL_FILES = {
    ROOT / "skills" / "kanban-implementation-workflow" / "SKILL.md",
    ROOT / "skills" / "kanban-factory-operations" / "SKILL.md",
    ROOT / "skills" / "kanban-progress-evidence" / "SKILL.md",
}
POLICY = ROOT / "examples" / "project-policy.yaml"
PUBLIC_TEXT_FILES = [
    ROOT / "README.md",
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
    *SKILL_PATHS,
    *(path for skill_path in SKILL_PATHS for path in skill_path.parent.rglob("*.md")),
]


def _frontmatter(path: Path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    prefix, separator, remainder = text.partition("\n---\n")
    assert separator, f"missing frontmatter terminator: {path}"
    return text, yaml.safe_load(prefix[4:]), remainder


def test_skill_frontmatter_and_layout():
    assert set(SKILL_PATHS) == EXPECTED_SKILL_FILES
    names = {path.parent.name for path in SKILL_PATHS}

    for path in SKILL_PATHS:
        text, data, _body = _frontmatter(path)
        assert data["name"] == path.parent.name
        assert re.fullmatch(r"\d+\.\d+\.\d+", str(data["version"]))
        assert data["license"] == "MIT"
        assert set(data["platforms"]) >= {"linux", "macos", "windows"}

        description = data["description"]
        assert len(description) <= 60, path
        assert description.endswith("."), path

        related = data.get("metadata", {}).get("hermes", {}).get("related_skills", [])
        assert set(related) <= names, path
        assert len(text) <= 100_000, path

    core_text = CORE_SKILL.read_text(encoding="utf-8").lower()
    assert "forgejo" in core_text
    assert "github" in core_text


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
    assert policy["kanban"]["max_in_progress_per_profile"] == 2
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

    assert not re.search(forbidden[4], combined)
    assert not re.search(forbidden[5], combined)

    skill = CORE_SKILL.read_text(encoding="utf-8").lower()
    assert "tea issues list" in skill
    assert "gh api" in skill
    assert "--paginate" in skill and "--page" in skill


if __name__ == "__main__":
    test_skill_frontmatter_and_layout()
    test_policy_parses_and_declares_roles_safety_and_deployment()
    test_public_files_have_no_machine_or_secret_identifiers()
    print("skill package validation: OK")
