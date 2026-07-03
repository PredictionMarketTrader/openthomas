import random

from openthomas.forecast.calibration import PlattScaler, brier_score, calibration_table


def test_brier_perfect_and_coinflip():
    assert brier_score([(1.0, 1), (0.0, 0)]) == 0.0
    assert abs(brier_score([(0.5, 1), (0.5, 0)]) - 0.25) < 1e-9


def test_small_sample_shrinks_toward_half():
    scaler = PlattScaler.fit([(0.9, 1)] * 5)
    assert 0.5 < scaler.apply(0.9) < 0.9  # shrunk, not trusted at face value


def test_fit_corrects_overconfidence():
    rng = random.Random(42)
    # A forecaster that says 0.9/0.1 when the truth is 0.7/0.3.
    pairs = []
    for _ in range(300):
        if rng.random() < 0.5:
            pairs.append((0.9, 1 if rng.random() < 0.7 else 0))
        else:
            pairs.append((0.1, 1 if rng.random() < 0.3 else 0))
    scaler = PlattScaler.fit(pairs)
    assert scaler.apply(0.9) < 0.85  # pulled toward the observed 0.7
    assert scaler.apply(0.1) > 0.15
    assert brier_score([(scaler.apply(p), y) for p, y in pairs]) <= brier_score(pairs)


def test_calibration_table_buckets():
    rows = calibration_table([(0.05, 0), (0.95, 1), (0.95, 1)])
    assert rows[0]["n"] == 1 and rows[0]["observed"] == 0
    assert rows[9]["n"] == 2 and rows[9]["observed"] == 1
