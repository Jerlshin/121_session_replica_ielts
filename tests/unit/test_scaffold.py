"""Phase 0 trivial CI gate: proves the pipeline (checkout, deps, pytest
discovery) runs green on every PR before any real logic exists to test.
"""


def test_scaffold_sanity():
    assert 1 + 1 == 2
