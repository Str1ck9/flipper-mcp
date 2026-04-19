"""MCP server exposing a Flipper Zero's CLI as tools.

Tool surface is deliberately narrow in v0 — enough for an agent to:
  * identify the device and discover capabilities
  * list / read files on the SD card
  * listen on SubGHz / IR / NFC for a bounded duration
  * run arbitrary CLI commands as an escape hatch

Connection is lazy: the serial port is opened on the first tool call, not at
process startup. This keeps `mcp inspector` / launch-and-idle cheap and lets
the server run without a Flipper attached.
"""

from __future__ import annotations

import atexit
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .bridge import FlipperBridge
from .registry import get_registry

mcp = FastMCP("flipper")

_bridge: Optional[FlipperBridge] = None


def _get_bridge() -> FlipperBridge:
    global _bridge
    if _bridge is None:
        port = os.environ.get("FLIPPER_PORT")
        _bridge = FlipperBridge(port=port)
    return _bridge


def _close_bridge() -> None:
    global _bridge
    if _bridge is not None:
        _bridge.close()
        _bridge = None


atexit.register(_close_bridge)


# -- discovery / info ------------------------------------------------------


@mcp.tool()
def flipper_info() -> str:
    """Show device info: firmware version, name, hardware, UID, battery."""
    return _get_bridge().send("device_info")


@mcp.tool()
def flipper_help() -> str:
    """List every CLI command the Flipper supports. Useful for exploration."""
    return _get_bridge().send("help")


@mcp.tool()
def flipper_cli(command: str, timeout_s: float = 10.0) -> str:
    """Run an arbitrary Flipper CLI command.

    Escape hatch for commands without a dedicated tool. For streaming commands
    (subghz rx, ir rx, nfc detect) prefer the dedicated tool — it handles
    the Ctrl-C for you.
    """
    return _get_bridge().send(command, timeout=timeout_s)


# -- storage ----------------------------------------------------------------


@mcp.tool()
def storage_list(path: str = "/ext") -> str:
    """List files and directories on the Flipper. Default: /ext (SD root).

    Common paths:
      /ext           SD card root
      /ext/subghz    captured SubGHz signals
      /ext/nfc       saved NFC tags
      /ext/infrared  learned IR remotes
      /ext/apps      installed apps
    """
    return _get_bridge().send(f"storage list {path}")


@mcp.tool()
def storage_info(path: str = "/ext") -> str:
    """Show free/total space for the given storage mount (/int or /ext)."""
    return _get_bridge().send(f"storage info {path}")


@mcp.tool()
def storage_read(path: str) -> str:
    """Read a file's text contents from the Flipper filesystem."""
    return _get_bridge().send(f"storage read {path}", timeout=30.0)


@mcp.tool()
def storage_stat(path: str) -> str:
    """Show metadata for a single file or directory on the Flipper."""
    return _get_bridge().send(f"storage stat {path}")


# -- SubGHz radio -----------------------------------------------------------


@mcp.tool()
def subghz_rx(frequency_hz: int, duration_s: float = 10.0) -> str:
    """Listen on SubGHz for `duration_s` seconds and return captured signals.

    frequency_hz: center frequency in hertz.
      315 MHz -> 315000000   (older US car fobs, garage doors)
      433.92 MHz -> 433920000 (most key fobs, weather stations, TPMS EU)
      868 MHz -> 868000000   (EU ISM, smart meters, some alarm systems)
      915 MHz -> 915000000   (US ISM, LoRa US, industrial telemetry)

    The Flipper's CC1101 is restricted to regional bands — check your
    firmware's allowed ranges if a frequency is rejected.
    """
    return _get_bridge().stream(f"subghz rx {frequency_hz}", duration_s)


@mcp.tool()
def subghz_tx_from_file(sub_file_path: str) -> str:
    """Transmit a saved .sub file from the Flipper SD card.

    Example path: /ext/subghz/garage.sub
    """
    return _get_bridge().send(
        f"subghz tx_from_file {sub_file_path}", timeout=30.0
    )


@mcp.tool()
def subghz_decode_raw(sub_file_path: str) -> str:
    """Attempt to decode a raw .sub capture into a known protocol."""
    return _get_bridge().send(
        f"subghz decode_raw {sub_file_path}", timeout=30.0
    )


# -- NFC / RFID -------------------------------------------------------------


@mcp.tool()
def nfc_detect(duration_s: float = 5.0) -> str:
    """Scan for NFC tags nearby for `duration_s` seconds."""
    return _get_bridge().stream("nfc detect", duration_s)


# -- Infrared ---------------------------------------------------------------


@mcp.tool()
def ir_rx(duration_s: float = 10.0) -> str:
    """Listen for IR remote signals for `duration_s` seconds.

    Point the target remote at the top of the Flipper and press buttons.
    """
    return _get_bridge().stream("ir rx", duration_s)


@mcp.tool()
def ir_tx(protocol: str, address: str, command: str) -> str:
    """Transmit an IR signal using a known protocol.

    protocol: NEC, NECext, Samsung32, RC5, RC6, Sony, etc.
    address / command: hex strings, e.g. "0x04 0x00 0x00 0x00" for address.
    """
    return _get_bridge().send(f"ir tx {protocol} {address} {command}")


# -- system -----------------------------------------------------------------


@mcp.tool()
def ps() -> str:
    """List running apps / tasks on the Flipper."""
    return _get_bridge().send("ps")


@mcp.tool()
def free() -> str:
    """Show free RAM on the Flipper."""
    return _get_bridge().send("free")


@mcp.tool()
def uptime() -> str:
    """Show how long the Flipper has been running since last boot."""
    return _get_bridge().send("uptime")


@mcp.tool()
def led(r: int = 0, g: int = 0, b: int = 0) -> str:
    """Set the RGB LED on the Flipper. Each channel: 0-255.

    Handy for giving the agent a visible heartbeat during long scans.
    """
    return _get_bridge().send(f"led r {r} g {g} b {b}")


@mcp.tool()
def vibro(on: bool) -> str:
    """Pulse the vibration motor on or off."""
    return _get_bridge().send(f"vibro {1 if on else 0}")


# -- registry (L2) ---------------------------------------------------------


@mcp.tool()
def registry_list(
    category: Optional[str] = None,
    pack: Optional[str] = None,
) -> list[dict]:
    """List known protocols in the bundled registry.

    category: filter to one of subghz | ir | nfc | lfrfid | ble
    pack: filter to a vertical pack, e.g. "fleetrf", "garage", "access-control"
    """
    return [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "typical_frequencies_hz": p.typical_frequencies_hz,
            "typical_devices": p.typical_devices,
            "packs": p.packs,
        }
        for p in get_registry().list(category=category, pack=pack)
    ]


@mcp.tool()
def registry_describe(protocol_id: str) -> dict:
    """Return the full registry entry for one protocol id."""
    proto = get_registry().get(protocol_id)
    if proto is None:
        return {
            "error": f"Unknown protocol '{protocol_id}'. "
            "Call registry_list() to see what's available."
        }
    return proto.model_dump()


@mcp.tool()
def scan_and_identify(
    frequency_hz: int, duration_s: float = 15.0
) -> dict:
    """Listen on SubGHz, then fingerprint the capture against the registry.

    One-shot workflow: runs ``subghz rx``, collects output for ``duration_s``,
    stops the scan, then matches the output against every known SubGHz
    protocol. Returns both the raw text (for human review) and the
    structured matches with decoded fields and metadata.

    Examples:
      frequency_hz=433920000 duration_s=15   # common key fobs / garage remotes
      frequency_hz=315000000 duration_s=15   # older US garage / gate remotes
      frequency_hz=868000000 duration_s=15   # EU ISM (smart meters, alarms)
    """
    raw = _get_bridge().stream(f"subghz rx {frequency_hz}", duration_s)
    matches = get_registry().fingerprint(raw, category="subghz")
    return {
        "frequency_hz": frequency_hz,
        "duration_s": duration_s,
        "match_count": len(matches),
        "matches": [m.to_dict() for m in matches],
        "raw_output": raw,
    }


@mcp.tool()
def ir_scan_and_identify(duration_s: float = 10.0) -> dict:
    """Listen for IR, then fingerprint the capture against the registry."""
    raw = _get_bridge().stream("ir rx", duration_s)
    matches = get_registry().fingerprint(raw, category="ir")
    return {
        "duration_s": duration_s,
        "match_count": len(matches),
        "matches": [m.to_dict() for m in matches],
        "raw_output": raw,
    }


# -- prompts (L3 hint) -----------------------------------------------------


@mcp.prompt()
def rf_triage(frequency_hz: int = 433920000, duration_s: float = 15.0) -> str:
    """Kick off a structured RF triage workflow on a given frequency.

    Produces a prompt that walks the agent through scan → identify →
    explain, with the expected narrative structure for a report.
    """
    return (
        f"You have access to a Flipper Zero via the `flipper` MCP server. "
        f"Perform an RF triage at {frequency_hz} Hz for {duration_s} seconds:\n\n"
        "1. Call `scan_and_identify` with those parameters.\n"
        "2. For every protocol match returned, summarize:\n"
        "   - what it is (one sentence, non-technical)\n"
        "   - what devices typically use it\n"
        "   - any security notes worth knowing\n"
        "3. If the raw output contains signals that DIDN'T match any known "
        "protocol, call them out separately with RSSI / timing info if "
        "available — these are candidates for registry contributions.\n"
        "4. End with a one-line verdict: is the frequency quiet, busy with "
        "known benign traffic, or worth a longer capture."
    )


# ---------------------------------------------------------------------------


def run() -> None:
    """Entry point for the `flipper-mcp` script — runs on stdio."""
    mcp.run()


if __name__ == "__main__":
    run()
