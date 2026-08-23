#!/usr/bin/env python3
"""Lightweight validation for the published skill package."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
skill = root / "skills" / "kanban-implementation-workflow" / "SKILL.md"
text = skill.read_text(encoding="utf-8")
assert text.startswith("---\n")
match = re.search(r"\n---\n", text[4:])
assert match, "missing frontmatter terminator"
frontmatter = text[4 : 4 + match.start()]
for required in ("name:", "description:", "version:", "author:", "license:", "platforms:", "metadata:"):
    assert required in frontmatter, f"missing {required}"
description = next(line.split(":", 1)[1].strip().strip('"') for line in frontmatter.splitlines() if line.startswith("description:"))
assert len(description) <= 60, description
assert description.endswith("."), description
assert "homelab" not in text.lower() or "project policy" in text.lower()
assert "HERMES_CUSTOM_HOMELAB_API_KEY" not in text
assert "/Users/" not in text
assert (root / "README.md").exists()
assert (root / "examples" / "project-policy.yaml").exists()
assert (root / "docs" / "policy-resolution.md").exists()
print("skill package validation: OK")
