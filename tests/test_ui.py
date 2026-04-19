"""Tests for the UI automation helpers in ``flipper_mcp.ui``.

All tests run offline — we never open a real serial port. A minimal fake
bridge records every command that would have been sent so we can assert on
the generated CLI traffic.
"""

from __future__ import annotations

import time

import pytest

from flipper_mcp import ui


class FakeBridge:
    """Records every ``send`` call instead of opening a serial port."""

    def __init__(self, reply: str = "") -> None:
        self.calls: list[str] = []
        self._reply = reply

    def send(self, cmd: str, timeout: float = 10.0, quiet_ms: int = 300) -> str:
        self.calls.append(cmd)
        return self._reply


# -- normalize_key ---------------------------------------------------------


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("up", "up"),
        ("down", "down"),
        ("left", "left"),
        ("right", "right"),
        ("ok", "ok"),
        ("back", "back"),
        ("u", "up"),
        ("d", "down"),
        ("l", "left"),
        ("r", "right"),
        ("center", "ok"),
        ("enter", "ok"),
        ("return", "ok"),
        ("b", "back"),
        ("esc", "back"),
        ("escape", "back"),
        ("  UP  ", "up"),
        ("Ok", "ok"),
    ],
)
def test_normalize_key_accepts_aliases(alias, expected):
    assert ui.normalize_key(alias) == expected


def test_normalize_key_rejects_unknown():
    with pytest.raises(ui.UIError):
        ui.normalize_key("power")


# -- press_key -------------------------------------------------------------


def test_press_key_default_type():
    b = FakeBridge()
    ui.press_key(b, "ok")
    assert b.calls == ["input send ok short"]


def test_press_key_long():
    b = FakeBridge()
    ui.press_key(b, "down", press_type="long")
    assert b.calls == ["input send down long"]


def test_press_key_rejects_bad_type():
    b = FakeBridge()
    with pytest.raises(ui.UIError):
        ui.press_key(b, "ok", press_type="smash")


def test_press_key_normalizes_alias():
    b = FakeBridge()
    ui.press_key(b, "enter")
    assert b.calls == ["input send ok short"]


# -- hold_key --------------------------------------------------------------


def test_hold_key_emits_press_and_release(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    b = FakeBridge()
    ui.hold_key(b, "ok", duration_s=0.5)
    assert b.calls == ["input send ok press", "input send ok release"]
    assert slept == [0.5]


def test_hold_key_clamps_negative_duration(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    b = FakeBridge()
    ui.hold_key(b, "ok", duration_s=-1.0)
    assert slept == [0.0]


# -- parse_sequence --------------------------------------------------------


def test_parse_sequence_simple_keys():
    parsed = ui.parse_sequence("ok,down,down")
    assert parsed == [
        ("key", ("ok", "short")),
        ("key", ("down", "short")),
        ("key", ("down", "short")),
    ]


def test_parse_sequence_with_types_and_wait():
    parsed = ui.parse_sequence("long:ok,wait:0.3,back")
    assert parsed == [
        ("key", ("ok", "long")),
        ("wait", 0.3),
        ("key", ("back", "short")),
    ]


def test_parse_sequence_skips_empty_tokens():
    parsed = ui.parse_sequence("ok, , down,")
    assert parsed == [
        ("key", ("ok", "short")),
        ("key", ("down", "short")),
    ]


def test_parse_sequence_rejects_unknown_prefix():
    with pytest.raises(ui.UIError):
        ui.parse_sequence("bounce:ok")


def test_parse_sequence_rejects_bad_wait():
    with pytest.raises(ui.UIError):
        ui.parse_sequence("wait:fast")


# -- run_sequence ----------------------------------------------------------


def test_run_sequence_drives_bridge(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    b = FakeBridge(reply="")
    out = ui.run_sequence(b, "ok,wait:0.2,long:down", step_delay_s=0.01)
    assert b.calls == [
        "input send ok short",
        "input send down long",
    ]
    # wait:0.2 + two step_delay_s=0.01
    assert slept == [0.01, 0.2, 0.01]
    assert "waited 0.2s" in out


# -- workflow macros -------------------------------------------------------


def test_subghz_read_start_ui_issues_expected_cli(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    b = FakeBridge()
    result = ui.subghz_read_start_ui(b)
    assert b.calls[0] == "loader open Sub-GHz"
    assert b.calls[1] == "input send ok short"
    assert b.calls[-1] == "loader info"
    assert result["app"] == "Sub-GHz"


def test_nfc_read_start_ui_issues_expected_cli(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    b = FakeBridge()
    ui.nfc_read_start_ui(b)
    assert b.calls == [
        "loader open NFC",
        "input send ok short",
        "loader info",
    ]


def test_infrared_learn_start_ui_navigates_down_then_ok(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    b = FakeBridge()
    ui.infrared_learn_start_ui(b)
    assert b.calls[:3] == [
        "loader open Infrared",
        "input send down short",
        "input send ok short",
    ]


def test_close_any_running_app_records_before_and_after(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    b = FakeBridge()
    result = ui.close_any_running_app(b)
    assert b.calls == [
        "loader info",
        "loader close",
        "loader info",
    ]
    assert set(result.keys()) == {"before", "close_result", "after"}
