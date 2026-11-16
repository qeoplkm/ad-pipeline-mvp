import pytest

from adpipe.util import fmt_short, fmt_ts


@pytest.mark.parametrize("seconds, expected", [
    (0, "00:00:00.000"),
    (-1.0, "00:00:00.000"),
    (12.3456, "00:00:12.346"),
    (59.9996, "00:01:00.000"),
    (119.99951, "00:02:00.000"),
    (3599.9999, "01:00:00.000"),
    (3725.5, "01:02:05.500"),
])
def test_fmt_ts_rolls_over_instead_of_printing_60_seconds(seconds, expected):
    assert fmt_ts(seconds) == expected


@pytest.mark.parametrize("seconds, expected", [
    (59.96, "00:01:00.0"),
    (61.24, "00:01:01.2"),
])
def test_fmt_ts_tenths(seconds, expected):
    assert fmt_ts(seconds, ms=False) == expected


@pytest.mark.parametrize("seconds, expected", [
    (0, "00:00.0"),
    (5.04, "00:05.0"),
    (59.97, "01:00.0"),
    (119.95, "02:00.0"),
    (125.36, "02:05.4"),
    (3600.0, "60:00.0"),
])
def test_fmt_short_rolls_over_instead_of_printing_60_seconds(seconds, expected):
    assert fmt_short(seconds) == expected
