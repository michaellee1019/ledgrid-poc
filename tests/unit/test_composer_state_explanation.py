"""Contracts for the read-only Composer state explanation projection."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class ComposerStateExplanationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        self.slice = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.script = (ROOT / "web/static/js/composer_state_explanation.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web/static/css/composer_slice.css").read_text(encoding="utf-8")

    def test_one_progressive_local_only_panel_has_exact_identity_details(self) -> None:
        self.assertEqual(self.html.count('id="stateExplanation"'), 1)
        self.assertIn('Local adapter only', self.html)
        self.assertIn('does not report wall or receiver health', self.html)
        self.assertIn('composer_state_explanation.js', self.html)
        for detail in ("Draft", "Preview", "Desired", "Observed"):
            self.assertIn(f'id="stateExplanation{detail}"', self.html)
        self.assertIn('id="stateExplanationDetails"', self.html)
        self.assertIn('.state-explanation', self.css)

    def test_projection_is_newest_response_wins_and_has_no_second_mutation_path(self) -> None:
        self.assertIn("composer-state-change", self.slice)
        self.assertIn("window.__composerStateProjection", self.slice)
        self.assertIn("const current=++generation", self.script)
        self.assertIn("if (current === generation) render(snapshot)", self.script)
        self.assertIn("target.focus({preventScroll:false})", self.script)
        for forbidden in ("fetch(", "autosave(", "goLive(", "stopScene(", "recordRecent("):
            self.assertNotIn(forbidden, self.script)

    def test_explanation_covers_local_preview_recovery_and_reconciliation_outcomes(self) -> None:
        for state in ("converged", "stopped", "rejected", "stale", "diverged", "retry"):
            self.assertIn(f"{state}:", self.script)
        for phrase in (
            "recoverable local draft", "local preview is unavailable",
            "rendering a preview", "pending local-adapter observation",
            "local to Composer", "Local adapter only",
        ):
            self.assertIn(phrase, self.script if phrase != "Local adapter only" else self.html)
        self.assertIn("'Unavailable'", self.script)
        self.assertIn("r${value.revision} · ${value.digest}", self.script)

    def test_preview_failure_message_takes_precedence_in_exact_details(self) -> None:
        self.assertIn(
            "snapshot.previewUnavailable ? snapshot.previewMessage : snapshot.localMessage",
            self.script,
        )
        self.assertIn("details.message.textContent = detailMessage(snapshot)", self.script)

    def test_existing_owners_publish_preview_draft_status_and_recovery_snapshots(self) -> None:
        for hook in ("markDraftLocal()", "commitPreview(body)", "function status(payload)", "state.recovery=body.draft"):
            self.assertIn(hook, self.slice)
        self.assertGreaterEqual(self.slice.count("publishComposerExplanation()"), 8)
        self.assertIn("previewUnavailable", self.slice)
        self.assertIn("reconciliation:state.reconciliation", self.slice)


if __name__ == "__main__":
    unittest.main()
