"""calibration_metrics.py unit tests (Spec 04 §2 Phase 9) — pure functions,
no I/O, hand-computed expected values plus the degenerate edges each
function is documented to guard against.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import calibration_metrics as m  # noqa: E402


def test_mae_and_rmse_on_perfect_agreement():
    preds = golds = [6.0, 6.5, 7.0, 5.5]
    assert m.mean_absolute_error(preds, golds) == 0.0
    assert m.root_mean_squared_error(preds, golds) == 0.0


def test_mae_and_rmse_hand_computed():
    preds = [6.0, 7.0, 5.0, 8.0]
    golds = [6.5, 6.5, 5.5, 7.5]
    # |diffs| = 0.5, 0.5, 0.5, 0.5 -> MAE = 0.5, RMSE = 0.5
    assert m.mean_absolute_error(preds, golds) == 0.5
    assert m.root_mean_squared_error(preds, golds) == 0.5


def test_mae_rmse_empty_sequences_are_zero_not_nan():
    assert m.mean_absolute_error([], []) == 0.0
    assert m.root_mean_squared_error([], []) == 0.0


def test_length_mismatch_raises_value_error():
    with pytest.raises(ValueError):
        m.mean_absolute_error([1.0], [1.0, 2.0])


def test_agreement_rate_tolerances():
    preds = [6.0, 7.0, 5.0, 8.0]
    golds = [6.0, 6.5, 5.5, 6.0]
    # diffs: 0.0, 0.5, 0.5, 2.0
    assert m.agreement_rate(preds, golds, tolerance=0.0) == 0.25
    assert m.agreement_rate(preds, golds, tolerance=0.5) == 0.75
    assert m.agreement_rate(preds, golds, tolerance=1.0) == 0.75
    assert m.agreement_rate(preds, golds, tolerance=2.0) == 1.0


def test_pearson_correlation_perfect_positive():
    preds = [4.0, 5.0, 6.0, 7.0]
    golds = [4.0, 5.0, 6.0, 7.0]
    assert m.pearson_correlation(preds, golds) == pytest_approx(1.0)


def test_pearson_correlation_perfect_negative():
    preds = [4.0, 5.0, 6.0, 7.0]
    golds = [7.0, 6.0, 5.0, 4.0]
    assert m.pearson_correlation(preds, golds) == pytest_approx(-1.0)


def test_pearson_correlation_none_on_zero_variance():
    assert m.pearson_correlation([5.0, 5.0, 5.0], [4.0, 6.0, 5.0]) is None


def test_pearson_correlation_none_on_fewer_than_two_points():
    assert m.pearson_correlation([5.0], [5.0]) is None


def test_spearman_correlation_perfect_monotonic_but_nonlinear():
    preds = [1.0, 2.0, 4.0, 8.0]  # perfectly rank-ordered, not linear
    golds = [1.0, 2.0, 3.0, 4.0]
    assert m.spearman_correlation(preds, golds) == pytest_approx(1.0)


def test_spearman_correlation_handles_ties_via_average_rank():
    preds = [5.0, 5.0, 6.0, 7.0]
    golds = [5.0, 5.5, 6.0, 7.0]
    result = m.spearman_correlation(preds, golds)
    assert result is not None
    assert 0.9 <= result <= 1.0


def test_qwk_perfect_agreement_is_one():
    preds = golds = [4.0, 5.5, 6.0, 7.5, 9.0]
    assert m.quadratic_weighted_kappa(preds, golds) == 1.0


def test_qwk_returns_none_for_degenerate_single_class_corpus():
    assert m.quadratic_weighted_kappa([5.0, 5.0], [5.0, 5.0]) is None


def test_qwk_returns_none_for_empty_corpus():
    assert m.quadratic_weighted_kappa([], []) is None


def test_qwk_penalizes_large_disagreement_more_than_small():
    close = m.quadratic_weighted_kappa([6.0, 6.5, 7.0, 5.5], [6.5, 6.5, 7.0, 5.5])
    far = m.quadratic_weighted_kappa([9.0, 6.5, 7.0, 5.5], [4.0, 6.5, 7.0, 5.5])
    assert close is not None and far is not None
    assert close > far


def pytest_approx(expected: float, tol: float = 1e-6):
    return pytest.approx(expected, abs=tol)
