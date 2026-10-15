import pytest

from zyroguard.durations import (
    Duration, DurationError, clamp_for_telegram, format_action, format_duration, parse_action, parse_duration,
)


@pytest.mark.parametrize("raw,seconds", [
    ("4m", 240), ("3h", 3 * 3600), ("6d", 6 * 86400), ("5w", 5 * 604800), ("1d12h", 36 * 3600),
    ("30s", 30), ("2H", 7200), (" 1w 1d ", 8 * 86400),
])
def test_parse(raw, seconds):
    assert parse_duration(raw).seconds == seconds


@pytest.mark.parametrize("raw", ["0", "forever", "perm", "permanent", "never"])
def test_permanent(raw):
    assert parse_duration(raw).permanent


@pytest.mark.parametrize("raw", ["", "abc", "5", "5x", "h5", "1d1", "-3h"])
def test_invalid(raw):
    with pytest.raises(DurationError):
        parse_duration(raw)


def test_format_roundtrip():
    assert format_duration(36 * 3600) == "1d12h"
    assert format_duration(None) == "forever"
    assert format_duration(90) == "1m30s"
    assert str(parse_duration("2w3d")) == "2w3d"


def test_clamp():
    d, note = clamp_for_telegram(Duration(10))
    assert d.permanent and "30 s" in note
    d, note = clamp_for_telegram(Duration(400 * 86400))
    assert d.permanent and "366" in note
    d, note = clamp_for_telegram(Duration(3600))
    assert d.seconds == 3600 and note is None


def test_parse_action():
    assert parse_action(["ban"]) == ("ban", Duration(None))
    assert parse_action(["mute", "2h"]) == ("mute", Duration(7200))
    assert parse_action(["tban", "7d"]) == ("ban", Duration(7 * 86400))
    assert parse_action(["kick"]) == ("kick", None)
    assert parse_action(["off"]) == ("none", None)
    with pytest.raises(DurationError):
        parse_action(["tmute"])
    with pytest.raises(DurationError):
        parse_action(["kick", "2h"])
    with pytest.raises(DurationError):
        parse_action(["explode"])


def test_format_action():
    assert format_action("mute", Duration(86400)) == "mute 1d"
    assert format_action("ban", None) == "ban forever"
    assert format_action("kick", None) == "kick"
