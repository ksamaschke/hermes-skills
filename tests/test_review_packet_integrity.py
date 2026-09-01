"""Regression tests for review packet integrity.

These replay the concrete factory incident that motivated the module: review
card ``t_b60cd631`` consumed six worker runs and produced zero source
inspection, because (1) its candidate had already been approved, and (2) its
invalid packet was fanned out into four leaves that each inherited the defect.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_packet_integrity.py"
spec = importlib.util.spec_from_file_location("review_packet_integrity", SCRIPT)
assert spec is not None and spec.loader is not None
rpi = importlib.util.module_from_spec(spec)
# Register before exec: dataclasses resolves field types via
# sys.modules[cls.__module__], which is absent for a bare spec load.
sys.modules[spec.name] = rpi
spec.loader.exec_module(rpi)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True, capture_output=True, check=True,
    )
    return proc.stdout.strip()


class TempRepo:
    """A throwaway git repo so the git-facing helpers are exercised for real."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        _git(self.path, "init", "-q", "-b", "main")
        _git(self.path, "config", "user.email", "t@example.invalid")
        _git(self.path, "config", "user.name", "T")

    def commit(self, files: dict[str, str], message: str) -> str:
        for rel, content in files.items():
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-q", "-m", message)
        return _git(self.path, "rev-parse", "HEAD")

    def close(self) -> None:
        self._tmp.cleanup()


class TreeIdentityTests(unittest.TestCase):
    """Tree hash, not commit sha, identifies the reviewed artifact."""

    def setUp(self) -> None:
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)

    def test_recovery_commit_keeps_the_reviewed_tree(self) -> None:
        # This is the exact incident shape: work was approved while uncommitted,
        # then a "preserve uncommitted work" commit gave it a NEW sha. Sha-based
        # dedup sees a fresh candidate; tree-based dedup correctly sees the same
        # artifact.
        base = self.repo.commit({"a.txt": "one\n"}, "base")
        approved = self.repo.commit({"a.txt": "one\n", "b.txt": "two\n"}, "work")
        _git(self.repo.path, "reset", "--soft", base)
        recovered = self.repo.commit(
            {"a.txt": "one\n", "b.txt": "two\n"},
            "wip(factory): preserve uncommitted work",
        )
        self.assertNotEqual(approved, recovered, "shas must differ for the test to mean anything")
        self.assertEqual(
            rpi.tree_of(self.repo.path, approved),
            rpi.tree_of(self.repo.path, recovered),
            "identical content must resolve to one tree identity",
        )

    def test_unresolvable_revision_returns_empty(self) -> None:
        self.repo.commit({"a.txt": "one\n"}, "base")
        self.assertEqual(rpi.tree_of(self.repo.path, "deadbeef" * 5), "")
        self.assertEqual(rpi.tree_of(self.repo.path, ""), "")


class DuplicateApprovalTests(unittest.TestCase):
    """Defect 1: never create a review card for an already-approved tree."""

    def setUp(self) -> None:
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)
        self.base = self.repo.commit({"a.txt": "one\n"}, "base")
        self.candidate = self.repo.commit({"a.txt": "one\n", "b.txt": "two\n"}, "work")

    def _runs(self, approved: bool = True, profile: str = "reviewer"):
        outcome = {
            "id": 111,
            "profile": profile,
            "status": "done",
            "outcome": "completed",
            "summary": "Reviewed and approved (round 2, execution lens).",
            "metadata": {"review_outcome": "approved" if approved else "changes_requested"},
        }
        return lambda task_id: [outcome]

    def test_existing_approval_on_same_tree_is_found(self) -> None:
        tasks = [{"id": "t_impl", "body": f"candidate {self.candidate}", "workspace_path": None}]
        found = rpi.approving_review_for_tree(
            repo=self.repo.path,
            candidate_rev=self.candidate,
            tasks=tasks,
            runs_for_task=self._runs(),
            implementer_profiles=("minna-implementer", "default"),
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.reviewer_profile, "reviewer")
        self.assertIn("already carries a terminal APPROVED", found.as_note())

    def test_changes_requested_is_not_an_approval(self) -> None:
        tasks = [{"id": "t_impl", "body": f"candidate {self.candidate}"}]
        self.assertIsNone(
            rpi.approving_review_for_tree(
                repo=self.repo.path,
                candidate_rev=self.candidate,
                tasks=tasks,
                runs_for_task=self._runs(approved=False),
            )
        )

    def test_implementer_self_approval_is_rejected(self) -> None:
        # A self-approval by the implementing profile is not review evidence, so
        # a real review card must still be created.
        tasks = [{"id": "t_impl", "body": f"candidate {self.candidate}"}]
        self.assertIsNone(
            rpi.approving_review_for_tree(
                repo=self.repo.path,
                candidate_rev=self.candidate,
                tasks=tasks,
                runs_for_task=self._runs(profile="minna-implementer"),
                implementer_profiles=("minna-implementer",),
            )
        )

    def test_different_tree_still_needs_review(self) -> None:
        newer = self.repo.commit({"a.txt": "one\n", "b.txt": "CHANGED\n"}, "more work")
        tasks = [{"id": "t_impl", "body": f"candidate {self.candidate}"}]
        self.assertIsNone(
            rpi.approving_review_for_tree(
                repo=self.repo.path,
                candidate_rev=newer,
                tasks=tasks,
                runs_for_task=self._runs(),
            ),
            "an approval of an older tree must never suppress review of new content",
        )

    def test_negated_approval_text_is_not_an_approval(self) -> None:
        run = {
            "id": 5, "profile": "reviewer", "outcome": "completed",
            "summary": "This candidate is not approved; blockers remain.",
            "metadata": {},
        }
        tasks = [{"id": "t_impl", "body": f"candidate {self.candidate}"}]
        self.assertIsNone(
            rpi.approving_review_for_tree(
                repo=self.repo.path,
                candidate_rev=self.candidate,
                tasks=tasks,
                runs_for_task=lambda _t: [run],
            )
        )


class HunkRangeTests(unittest.TestCase):
    """Defect 3a: manifests must carry real per-path hunk ranges."""

    def setUp(self) -> None:
        self.repo = TempRepo()
        self.addCleanup(self.repo.close)

    def test_ranges_are_derived_from_real_diff(self) -> None:
        base = self.repo.commit({"src/x.rs": "\n".join(f"line{i}" for i in range(1, 21)) + "\n"}, "base")
        lines = [f"line{i}" for i in range(1, 21)]
        lines.insert(4, "INSERTED")
        candidate = self.repo.commit({"src/x.rs": "\n".join(lines) + "\n"}, "change")

        paths = rpi.changed_paths_with_hunks(self.repo.path, base, candidate)
        self.assertEqual([p.path for p in paths], ["src/x.rs"])
        self.assertTrue(paths[0].ranges, "a changed path must carry at least one range")
        rendered = paths[0].render()
        self.assertRegex(rendered, r"^src/x\.rs:\d+-\d+")

    def test_deletion_only_hunk_still_yields_a_range(self) -> None:
        base = self.repo.commit({"d.txt": "a\nb\nc\n"}, "base")
        candidate = self.repo.commit({"d.txt": "a\nc\n"}, "delete b")
        paths = rpi.changed_paths_with_hunks(self.repo.path, base, candidate)
        self.assertEqual(len(paths), 1)
        self.assertTrue(
            paths[0].ranges,
            "a pure-deletion hunk must not drop the path's ranges",
        )

    def test_rendered_form_matches_validator_expectations(self) -> None:
        # The validator accepts `path:1-20,40-55`; anything else is rejected as
        # a non-file path, which is what an unranged manifest produced.
        cp = rpi.ChangedPath("crates/minna-server/src/ops.rs", ("61-67", "76-183"))
        self.assertEqual(
            cp.render(), "crates/minna-server/src/ops.rs:61-67,76-183"
        )


class GateEvidenceTests(unittest.TestCase):
    """Defect 3b: gate evidence is measured or declared absent, never asserted."""

    def test_the_literal_incident_string_is_unverifiable(self) -> None:
        self.assertTrue(rpi.is_unverifiable_gate_citation("all gate commands green"))

    def test_empty_citation_is_unverifiable(self) -> None:
        self.assertTrue(rpi.is_unverifiable_gate_citation(""))
        self.assertTrue(rpi.is_unverifiable_gate_citation("   "))

    def test_command_with_exit_code_is_verifiable(self) -> None:
        self.assertFalse(
            rpi.is_unverifiable_gate_citation(
                "cargo test -p minna-tools — exit 0 @ 771f4e247ffa (78 passed)"
            )
        )

    def test_command_without_exit_code_is_unverifiable(self) -> None:
        self.assertTrue(rpi.is_unverifiable_gate_citation("cargo test -p minna-tools passed"))

    def test_absent_gate_is_declared_not_asserted(self) -> None:
        text = rpi.render_gate_evidence([], gate_declared=False, candidate="771f4e247ffa0000")
        self.assertIn("GATE ABSENT", text)
        self.assertFalse(rpi.is_unverifiable_gate_citation(text) is False and "green" in text.lower())
        self.assertNotIn("all gate commands green", text)
        self.assertIn("do not run a full project gate", text.lower())

    def test_measured_evidence_renders_command_exit_and_commit(self) -> None:
        ev = [
            rpi.GateEvidence("cargo test -p minna-tools", 0, "771f4e247ffad5b1", "78 passed"),
            rpi.GateEvidence("npx vitest run", 0, "771f4e247ffad5b1", "75 passed"),
        ]
        text = rpi.render_gate_evidence(ev, gate_declared=True, candidate="771f4e247ffad5b1")
        self.assertIn("cargo test -p minna-tools", text)
        self.assertIn("exit 0", text)
        self.assertIn("771f4e247ffa", text)
        self.assertNotIn("all gate commands green", text)

    def test_stale_evidence_is_flagged(self) -> None:
        ev = [rpi.GateEvidence("cargo test", 0, "aaaaaaaaaaaa1111", "ok")]
        text = rpi.render_gate_evidence(ev, gate_declared=True, candidate="771f4e247ffad5b1")
        self.assertIn("stale", text.lower())

    def test_untracked_workflow_is_not_a_declared_gate(self) -> None:
        # An untracked gate.yml in someone's checkout is not carried by the
        # candidate commit; citing it would be another unverifiable claim.
        repo = TempRepo()
        self.addCleanup(repo.close)
        rev = repo.commit({"README.md": "x\n"}, "base")
        (repo.path / ".forgejo" / "workflows").mkdir(parents=True)
        (repo.path / ".forgejo" / "workflows" / "gate.yml").write_text("name: gate\n")
        self.assertFalse(rpi.repo_declares_gate(repo.path, rev))

    def test_committed_workflow_is_a_declared_gate(self) -> None:
        repo = TempRepo()
        self.addCleanup(repo.close)
        rev = repo.commit({".forgejo/workflows/gate.yml": "name: gate\n"}, "ci")
        self.assertTrue(rpi.repo_declares_gate(repo.path, rev))


class SplitDecisionTests(unittest.TestCase):
    """Defect 2: an invalid packet is repaired, never fanned out."""

    # The exact validator output for the incident packet.
    INCIDENT_ERRORS = [
        "exact_scope contains non-file paths: crates/minna-server/src/ops.rs",
        "exact_scope must contain at most 5 files",
        "missing implementer_profile",
        "gate evidence is unverifiable: 'all gate commands green'",
    ]

    def test_invalid_packet_must_not_be_split(self) -> None:
        decision = rpi.split_decision(self.INCIDENT_ERRORS)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "repair")
        self.assertIn("splitting would copy the same defect", decision.reason)

    def test_size_only_packet_may_be_split(self) -> None:
        decision = rpi.split_decision(["exact_scope must contain at most 5 files"])
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action, "split")

    def test_valid_packet_has_nothing_to_split(self) -> None:
        decision = rpi.split_decision([])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "repair")

    def test_missing_field_alone_is_repair_not_split(self) -> None:
        decision = rpi.split_decision(["missing candidate_commit"])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "repair")

    def test_block_reason_is_tagged_for_the_board(self) -> None:
        decision = rpi.split_decision(self.INCIDENT_ERRORS)
        self.assertTrue(decision.as_block_reason().startswith("[packet-repair]"))


class InheritedDefectTests(unittest.TestCase):
    """The four leaves each inherited the parent's defect; catch that."""

    PARENT_ERRORS = [
        "exact_scope entries carry no hunk range",
        "gate evidence is unverifiable: 'all gate commands green'",
    ]

    # Verbatim shape of the decomposer's child bodies.
    CHILD_BODY = (
        "Perform a read-only adversarial pre-merge review of PR #238, candidate "
        "commit 771f4e247ffad5b172f8c230f79b8e63b91c4920 against base dev. Review "
        "only the merge-base delta in crates/minna-server/src/handlers.rs, "
        "crates/minna-server/src/ops.rs. Gate: all gate commands green."
    )

    def test_child_inherits_parent_defects(self) -> None:
        carried = rpi.inherited_packet_defects(self.PARENT_ERRORS, self.CHILD_BODY)
        self.assertEqual(len(carried), 2, f"expected both defects to be carried, got {carried}")

    def test_repaired_child_carries_nothing(self) -> None:
        repaired = (
            "exact_scope:\n"
            "- crates/minna-server/src/ops.rs:61-67,76-183\n"
            "gate: `cargo test -p minna-tools` exit 0 @ 771f4e247ffa"
        )
        self.assertEqual(rpi.inherited_packet_defects(self.PARENT_ERRORS, repaired), [])

    def test_replaying_the_incident_produces_zero_valid_leaves(self) -> None:
        """End-to-end replay: the incident packet yields no dispatchable leaf."""
        errors = SplitDecisionTests.INCIDENT_ERRORS
        decision = rpi.split_decision(errors)
        self.assertFalse(decision.allowed, "the incident packet must never fan out")

        # And had leaves already been created, each is caught as inherited-invalid.
        leaves = ["t_7e5683e2", "t_3d8095d5", "t_1446f504", "t_b824c851"]
        blocked = [
            leaf for leaf in leaves
            if rpi.inherited_packet_defects(self.PARENT_ERRORS, self.CHILD_BODY)
        ]
        self.assertEqual(len(blocked), 4, "every inherited-invalid leaf must be caught")


if __name__ == "__main__":
    unittest.main()
