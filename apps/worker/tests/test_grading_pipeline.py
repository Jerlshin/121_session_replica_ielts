"""Chain-wiring test for grade_exam_session (Spec 03 §2.2, Spec 04 §2
Phase 5-7) — no real Postgres/S3/broker needed here, this only proves the
DAG is assembled correctly: finalize_media -> transcribe_full_session ->
a chord fanning the four Phase 6 feature-extraction tasks out into
synthesize_band_scores, every stage an immutable signature (see
pipelines/grading_pipeline.py's docstring for why immutability matters).
Each individual task gets its own real-infra integration test under
tests/integration/.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipelines.grading_pipeline import grade_exam_session  # noqa: E402

SESSION_ID = "11111111-1111-1111-1111-111111111111"


def test_grade_exam_session_builds_chain_with_group_and_chord():
    with patch("pipelines.grading_pipeline.chain") as mock_chain:
        mock_chain.return_value.apply_async = MagicMock()

        grade_exam_session(SESSION_ID)

        assert mock_chain.call_count == 1
        finalize_sig, transcribe_sig, chord_sig = mock_chain.call_args[0]

        assert finalize_sig.task == "tasks.media.finalize_media"
        assert finalize_sig.immutable is True
        assert finalize_sig.args == (SESSION_ID,)

        assert transcribe_sig.task == "tasks.asr.transcribe_full_session"
        assert transcribe_sig.immutable is True
        assert transcribe_sig.args == (SESSION_ID,)

        # chord(group(E1-E4), synthesize_band_scores) — the callback body.
        assert chord_sig.body.task == "tasks.scoring.synthesize_band_scores"
        assert chord_sig.body.immutable is True
        assert chord_sig.body.args == (SESSION_ID,)

        group_tasks = chord_sig.tasks
        group_task_names = {sig.task for sig in group_tasks}
        assert group_task_names == {
            "tasks.nlp.compute_fluency_metrics",
            "tasks.nlp.compute_lexical_metrics",
            "tasks.nlp.compute_grammar_metrics",
            "tasks.pronunciation.compute_pronunciation_scores",
        }
        for sig in group_tasks:
            assert sig.immutable is True
            assert sig.args == (SESSION_ID,)

        mock_chain.return_value.apply_async.assert_called_once_with()
