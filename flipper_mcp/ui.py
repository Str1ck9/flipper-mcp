"""Higher-level UI automation helpers.

The Flipper's CLI exposes a synthetic-input primitive (``input send <key>
<type>``) which lets an agent drive the on-device UI the same way a human
would with the button pad. Building on top of that, this module adds:

- Key alias normalization (``u`` -> ``up``, ``l`` -> ``left``, etc.)
- ``press_key`` / ``hold_key`` helpers that wrap single-button actions
- ``run_sequence`` to execute a mini-DSL like ``"ok,down,long:ok,wait:0.5,back"``
- Workflow macros for launching on-device apps and navigating into common
  reading modes (Sub-GHz Read, NFC Read, Infrared Learn) via ``loader open``
  plus a deterministic chain of button presses

All of these produce structured dicts so the agent can see what it asked
for and what the CLI echoed back (usually nothing — ``input send`` is
silent on success).
"""

from __future__ import annotations

import time

from .bridge import FlipperBridge

# -- key normalization -----------------------------------------------------

_CANONICAL_KEYS = {"up", "down", "left", "right", "ok", "back"}

_KEY_ALIASES = {
    "u": "up",
    "d": "down",
    "l": "left",
    "r": "right",
    "center": "ok",
    "enter": "ok",
    "return": "ok",
    "b": "back",
    "esc": "back",
    "escape": "back",
}


class UIError(ValueError):
    pass


def normalize_key(key: str) -> str:
    """Return the canonical Flipper input-key name for an alias.

    Accepts any of ``up|down|left|right|ok|back`` or the common shorthand
    forms (``u``, ``d``, ``enter``, ``esc``, ...).
    """
    k = key.strip().lower()
    if k in _CANONICAL_KEYS:
        return k
    if k in _KEY_ALIASES:
        return _KEY_ALIASES[k]
    raise UIError(
        f"Unknown Flipper input key: {key!r}. Use one of {sorted(_CANONICAL_KEYS)}."
    )


# -- single-key helpers ----------------------------------------------------


def press_key(bridge: FlipperBridge, key: str, press_type: str = "short") -> str:
    """Send a single synthetic keypress through the Flipper CLI.

    press_type: one of ``short`` | ``long`` | ``press`` | ``release``.
    ``short`` is a full press-and-release (the common case).
    """
    canonical = normalize_key(key)
    if press_type not in {"short", "long", "press", "release"}:
        raise UIError(
            f"Unknown press type: {press_type!r}. "
            "Use 'short' | 'long' | 'press' | 'release'."
        )
    return bridge.send(f"input send {canonical} {press_type}")


def hold_key(bridge: FlipperBridge, key: str, duration_s: float) -> str:
    """Hold a key down for ``duration_s`` seconds, then release.

    Uses explicit ``press`` + ``release`` events around a wall-clock sleep,
    so the firmware receives a genuine "hold" rather than a ``long`` event
    (which has fixed firmware-defined timing).
    """
    canonical = normalize_key(key)
    bridge.send(f"input send {canonical} press")
    time.sleep(max(0.0, duration_s))
    return bridge.send(f"input send {canonical} release")


# -- sequence DSL ----------------------------------------------------------


def parse_sequence(seq: str) -> list[tuple[str, object]]:
    """Parse a mini-DSL sequence string into (op, arg) tuples.

    Syntax (comma-separated steps):
      - ``ok``                 short press OK
      - ``long:ok``            long press OK
      - ``press:down``         press-only (no release) event for Down
      - ``release:down``       release-only event
      - ``wait:0.5``           wall-clock delay in seconds
    """
    out: list[tuple[str, object]] = []
    for raw in seq.split(","):
        tok = raw.strip()
        if not tok:
            continue
        if ":" in tok:
            prefix, rest = tok.split(":", 1)
            prefix = prefix.strip().lower()
            rest = rest.strip()
            if prefix == "wait":
                try:
                    out.append(("wait", float(rest)))
                except ValueError as e:
                    raise UIError(f"wait: expected a number, got {rest!r}") from e
                continue
            if prefix in {"short", "long", "press", "release"}:
                out.append(("key", (normalize_key(rest), prefix)))
                continue
            raise UIError(f"Unknown sequence prefix: {prefix!r} in {tok!r}")
        out.append(("key", (normalize_key(tok), "short")))
    return out


def run_sequence(
    bridge: FlipperBridge,
    sequence: str,
    step_delay_s: float = 0.05,
) -> list[str]:
    """Execute a sequence-DSL string against the Flipper.

    ``step_delay_s`` pauses between each step so the firmware's event loop
    can process the previous event before the next arrives. 50ms is enough
    in practice for stock firmware.
    """
    steps = parse_sequence(sequence)
    results: list[str] = []
    for op, arg in steps:
        if op == "wait":
            time.sleep(float(arg))
            results.append(f"waited {arg}s")
            continue
        # op == "key"
        key, ptype = arg  # type: ignore[misc]
        results.append(bridge.send(f"input send {key} {ptype}"))
        time.sleep(step_delay_s)
    return results


# -- workflow macros -------------------------------------------------------
#
# Each macro launches a known on-device app, waits for it to render, and
# then navigates into a specific submode via `input send`. These encode
# the empirically-validated button chains — the kind of thing we worked
# out by hand during v0.4 testing.
#
# All macros are "best effort": if a firmware's menu order has shifted
# they may land on the wrong submode. The agent can always verify via
# ``loader_info`` and any domain probe (``subghz rx`` will error with
# "app open" while the radio is claimed).


def _launch_and_wait(
    bridge: FlipperBridge,
    app_id: str,
    launch_settle_s: float = 0.4,
) -> str:
    raw = bridge.send(f"loader open {app_id}", timeout=10.0)
    time.sleep(launch_settle_s)
    return raw


def subghz_read_start_ui(
    bridge: FlipperBridge,
) -> dict:
    """Launch Sub-GHz → navigate to Read (first menu item) → start listening.

    Returns a struct describing the steps taken and the final ``loader_info``
    state. The Flipper remembers its last-used frequency, so this enters
    Read at whatever frequency was configured last.
    """
    launch = _launch_and_wait(bridge, "Sub-GHz")
    seq_out = run_sequence(bridge, "ok")
    info = bridge.send("loader info")
    return {
        "app": "Sub-GHz",
        "loader_open": launch,
        "input_sequence_results": seq_out,
        "loader_info": info,
        "note": "Entered Read via 'ok' on the default-highlighted first menu item.",
    }


def nfc_read_start_ui(bridge: FlipperBridge) -> dict:
    """Launch NFC → navigate to Read (first menu item)."""
    launch = _launch_and_wait(bridge, "NFC")
    seq_out = run_sequence(bridge, "ok")
    info = bridge.send("loader info")
    return {
        "app": "NFC",
        "loader_open": launch,
        "input_sequence_results": seq_out,
        "loader_info": info,
    }


def infrared_learn_start_ui(bridge: FlipperBridge) -> dict:
    """Launch Infrared → navigate to Learn New Remote."""
    launch = _launch_and_wait(bridge, "Infrared")
    # Infrared app main menu: Universal Remotes, Learn New Remote, Saved.
    # "Learn" is typically the 2nd item; pattern: down, ok.
    seq_out = run_sequence(bridge, "down,ok")
    info = bridge.send("loader info")
    return {
        "app": "Infrared",
        "loader_open": launch,
        "input_sequence_results": seq_out,
        "loader_info": info,
        "note": (
            "Navigated 'down, ok' to reach Learn New Remote. If your "
            "firmware's menu order differs, the agent can inspect "
            "loader_info and re-navigate via input_sequence."
        ),
    }


def close_any_running_app(bridge: FlipperBridge) -> dict:
    """Safely close whatever app is running on the Flipper.

    Useful as a prologue to workflow macros that assume a clean state, or
    as an epilogue that restores the main screen so LED / vibro / direct
    radio commands can run again.
    """
    before = bridge.send("loader info")
    closed = bridge.send("loader close")
    after = bridge.send("loader info")
    return {"before": before, "close_result": closed, "after": after}
