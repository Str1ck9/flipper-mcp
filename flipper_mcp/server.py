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
from .registry import (
    bundled_protocols,
    classify_file,
    fetch_index,
    get_registry,
    install_from_entry,
    installed_protocols,
    reset_registry,
    uninstall_from_cache,
    user_cache_dir,
)
from .ui import (
    close_any_running_app,
    hold_key,
    infrared_learn_start_ui,
    nfc_read_start_ui,
    press_key,
    run_sequence,
    subghz_read_start_ui,
)

mcp = FastMCP("flipper")

# Per-user default radio device for SubGHz commands.
#   0 = internal CC1101 (default for everyone without an add-on)
#   1 = external CC1101 module plugged into the GPIO header
# Override on a per-user basis by exporting FLIPPER_DEFAULT_DEVICE=1.
_DEFAULT_DEVICE = int(os.environ.get("FLIPPER_DEFAULT_DEVICE", "0"))

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
def subghz_rx(
    frequency_hz: int,
    duration_s: float = 10.0,
    device: Optional[int] = None,
) -> str:
    """Listen on SubGHz for `duration_s` seconds and return captured signals.

    frequency_hz: center frequency in hertz. Flipper rounds to the nearest
    valid channel (e.g. 433920000 -> 433919830).
      315 MHz -> 315000000   (older US car fobs, garage doors)
      433.92 MHz -> 433920000 (most key fobs, weather stations, TPMS EU)
      868 MHz -> 868000000   (EU ISM, smart meters, some alarm systems)
      915 MHz -> 915000000   (US ISM, LoRa US, industrial telemetry)

    device: 0 = internal CC1101, 1 = external CC1101 module. If unset,
    falls back to the FLIPPER_DEFAULT_DEVICE env var (default 0).

    The Flipper's CC1101 is restricted to regional bands — check
    `hardware_region_provisioned` in `flipper_info` if a frequency is rejected.
    """
    dev = device if device is not None else _DEFAULT_DEVICE
    return _get_bridge().stream(f"subghz rx {frequency_hz} {dev}", duration_s)


@mcp.tool()
def subghz_tx_from_file(sub_file_path: str) -> str:
    """Transmit a saved .sub file from the Flipper SD card.

    Example path: /ext/subghz/garage.sub
    """
    return _get_bridge().send(f"subghz tx_from_file {sub_file_path}", timeout=30.0)


@mcp.tool()
def subghz_decode_raw(sub_file_path: str, duration_s: float = 15.0) -> str:
    """Replay a raw .sub capture through the decoder and report matches.

    ``subghz decode_raw`` is a streaming command on the Flipper: it plays the
    saved capture back in real time through every registered decoder and
    prints any hits. That means this tool takes at least as long as the
    original capture (bounded here by ``duration_s``, default 15s).
    """
    return _get_bridge().stream(
        f"subghz decode_raw {sub_file_path}", duration=duration_s
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
    address / command: accept either space-separated bytes as saved in .ir
    files ("EA C7 00 00") or pre-concatenated hex ("EAC70000"). Spaces are
    stripped automatically so the CLI receives single-arg hex strings.

    NOTE: requires the firmware's ``cli_ir.fal`` plugin to be loaded. If
    this returns "failed to load external command", refresh the firmware
    via qFlipper to update the external CLI plugins.
    """
    addr = address.replace(" ", "").lstrip("0x").lstrip("0X")
    cmd = command.replace(" ", "").lstrip("0x").lstrip("0X")
    return _get_bridge().send(f"ir tx {protocol} {addr} {cmd}")


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


# -- storage mutation (v0.4) -----------------------------------------------


@mcp.tool()
def storage_write(path: str, content: str) -> str:
    """Write text content to a file on the Flipper's SD card.

    Uses ``storage write <path>`` which reads until Ctrl-C. Best for small
    text files (config, ducky scripts, notes). Binary content is not
    supported via this path — use SD card mount for that.
    """
    return _get_bridge().write_file(path, content)


@mcp.tool()
def storage_remove(path: str) -> str:
    """Delete a file or empty directory on the Flipper."""
    return _get_bridge().send(f"storage remove {path}")


@mcp.tool()
def storage_mkdir(path: str) -> str:
    """Create a new directory on the Flipper."""
    return _get_bridge().send(f"storage mkdir {path}")


@mcp.tool()
def storage_rename(old_path: str, new_path: str) -> str:
    """Rename or move a file/directory on the Flipper."""
    return _get_bridge().send(f"storage rename {old_path} {new_path}")


@mcp.tool()
def storage_md5(path: str) -> str:
    """Compute the MD5 checksum of a file on the Flipper (firmware-side)."""
    return _get_bridge().send(f"storage md5 {path}")


# -- SubGHz raw capture (v0.4) ---------------------------------------------


@mcp.tool()
def subghz_rx_raw(frequency_hz: int, duration_s: float = 10.0) -> str:
    """Capture raw pulse timings on a SubGHz frequency, no decoding.

    Unlike ``subghz_rx`` (which only emits when a known protocol decodes),
    ``rx_raw`` dumps pulse durations continuously. Useful for signals the
    firmware can't decode — proprietary RKE, unusual framing, etc. The
    output is a stream of ``+<us> -<us>`` pairs representing pulse / gap
    durations in microseconds.

    Note: the ``subghz rx_raw`` CLI command does not accept a device arg,
    so this always uses the internal CC1101.
    """
    return _get_bridge().stream(f"subghz rx_raw {frequency_hz}", duration_s)


@mcp.tool()
def subghz_chat(
    frequency_hz: int = 433920000,
    duration_s: float = 30.0,
    device: Optional[int] = None,
) -> str:
    """Listen on SubGHz chat mode — receive broadcast messages from other Flippers."""
    dev = device if device is not None else _DEFAULT_DEVICE
    return _get_bridge().stream(f"subghz chat {frequency_hz} {dev}", duration_s)


# -- RFID / iButton (v0.4) -------------------------------------------------


@mcp.tool()
def rfid_read(duration_s: float = 10.0) -> str:
    """Scan for 125 kHz LF-RFID cards for `duration_s` seconds.

    Requires ``cli_rfid.fal`` plugin loaded on the firmware.
    """
    return _get_bridge().stream("rfid read", duration_s)


@mcp.tool()
def ibutton_read(duration_s: float = 10.0) -> str:
    """Scan for iButton / 1-Wire keys for `duration_s` seconds."""
    return _get_bridge().stream("ikey read", duration_s)


# -- GPIO (v0.4) -----------------------------------------------------------


@mcp.tool()
def gpio_read(pin: str) -> str:
    """Read the current digital state of a GPIO pin.

    pin: one of PA4, PA6, PA7, PB2, PB3, PC0, PC1, PC3 (Flipper naming).
    """
    return _get_bridge().send(f"gpio get {pin}")


@mcp.tool()
def gpio_write(pin: str, value: int) -> str:
    """Set a GPIO pin high (1) or low (0). Pin is configured as output."""
    return _get_bridge().send(f"gpio set {pin} {int(bool(value))}")


@mcp.tool()
def gpio_mode(pin: str, mode: str) -> str:
    """Configure a GPIO pin's direction/mode.

    mode: typically 'input', 'output', or firmware-specific string.
    """
    return _get_bridge().send(f"gpio mode {pin} {mode}")


# -- Loader / app control (v0.4) -------------------------------------------


@mcp.tool()
def loader_open(app_path_or_id: str) -> str:
    """Launch an app on the Flipper by path or built-in ID.

    Examples:
      loader_open("Sub-GHz")                      # built-in Sub-GHz app
      loader_open("/ext/apps/Infrared/flipper_xremote.fap")  # external .fap

    Closes any currently running app first if needed. This is how an agent
    can kick off the on-device Read app for the user without them touching
    the Flipper.
    """
    return _get_bridge().send(f"loader open {app_path_or_id}", timeout=15.0)


@mcp.tool()
def loader_close() -> str:
    """Close the currently running app on the Flipper."""
    return _get_bridge().send("loader close")


@mcp.tool()
def loader_info() -> str:
    """Show what app is currently running on the Flipper."""
    return _get_bridge().send("loader info")


@mcp.tool()
def loader_list() -> str:
    """List all built-in apps launchable via ``loader open``."""
    return _get_bridge().send("loader list")


# -- Recovery + introspection (v0.4) ---------------------------------------


@mcp.tool()
def flipper_interrupt() -> str:
    """Send a Ctrl-C to the Flipper CLI to recover from a stuck streaming command.

    Use this if you suspect an earlier ``flipper_cli`` call left the Flipper
    in a listening state (no terminating Ctrl-C was sent).
    """
    return _get_bridge().interrupt()


@mcp.tool()
def flipper_file_inspect(path: str) -> dict:
    """Read a file on the Flipper and classify it by its Filetype header.

    Recognizes Flipper save files (SubGHz raw/parsed, NFC, IR, LF-RFID,
    iButton) and returns structured metadata — frequency, preset, protocol
    name, UID, etc. For parsed-protocol files, also runs the registry
    fingerprinter on the body.

    This is the fastest way to tell the agent what's in a saved capture
    without requiring the Flipper firmware to re-decode it.
    """
    content = _get_bridge().send(f"storage read {path}", timeout=30.0)
    meta = classify_file(content)
    meta["path"] = path

    # If the file names a protocol, also fingerprint the content in case
    # our registry can add security notes / typical_devices metadata.
    category_map = {
        "subghz_parsed": "subghz",
        "subghz_raw": "subghz",
        "ir": "ir",
        "nfc": "nfc",
        "lfrfid": "lfrfid",
        "ibutton": "ibutton",
    }
    reg_cat = category_map.get(meta.get("kind", ""))
    if reg_cat is not None:
        matches = get_registry().fingerprint(content, category=reg_cat)
        meta["registry_matches"] = [m.to_dict() for m in matches]
    return meta


@mcp.tool()
def list_installed_apps() -> dict:
    """Enumerate installed FAP apps under ``/ext/apps/*/*.fap``.

    Returns apps grouped by category directory. Useful for agent
    discovery — e.g. "what Sub-GHz tools does this Flipper have available?"
    """
    b = _get_bridge()
    categories = b.send("storage list /ext/apps")
    result: dict = {"categories": {}, "total_apps": 0}

    for line in categories.splitlines():
        line = line.strip()
        if not line.startswith("[D]"):
            continue
        cat = line.split(maxsplit=1)[1].strip()
        if cat.startswith("."):
            continue
        listing = b.send(f"storage list /ext/apps/{cat}")
        faps: list[str] = []
        for inner in listing.splitlines():
            inner = inner.strip()
            if inner.startswith("[F]"):
                name = inner.split(maxsplit=1)[1].strip().split()[0]
                if name.endswith(".fap"):
                    faps.append(name)
        if faps:
            result["categories"][cat] = sorted(faps)
            result["total_apps"] += len(faps)
    return result


# -- UI automation (v0.5) --------------------------------------------------


@mcp.tool()
def flipper_press_key(key: str, press_type: str = "short") -> str:
    """Send a synthetic keypress to the Flipper UI.

    key: one of up, down, left, right, ok, back (aliases like ``u``, ``enter``,
    ``esc`` accepted).
    press_type: short | long | press | release. Default ``short`` is a full
    press-and-release, matching a normal button tap.
    """
    return press_key(_get_bridge(), key, press_type=press_type)


@mcp.tool()
def flipper_hold_key(key: str, duration_s: float) -> str:
    """Press and hold a key for ``duration_s`` seconds, then release.

    Uses explicit press/release events so the hold duration is wall-clock
    accurate rather than firmware-preset.
    """
    return hold_key(_get_bridge(), key, duration_s)


@mcp.tool()
def flipper_input_sequence(sequence: str, step_delay_s: float = 0.05) -> list[str]:
    """Run a mini-DSL sequence of keypresses / waits against the Flipper UI.

    Syntax (comma-separated steps):
      ``ok`` — short press OK
      ``long:ok`` — long press OK
      ``press:down`` — press-only event for Down (without release)
      ``release:down`` — release-only event
      ``wait:0.5`` — wall-clock delay of 0.5 seconds

    Example: ``"ok,down,down,long:ok,wait:0.3,back"`` navigates two items
    down, holds OK, waits 300 ms, then backs out.
    """
    return run_sequence(_get_bridge(), sequence, step_delay_s=step_delay_s)


# -- workflow macros (v0.5) ------------------------------------------------


@mcp.tool()
def workflow_subghz_read_start() -> dict:
    """Launch the Sub-GHz app and navigate into Read mode in one call.

    Hands-free equivalent of: tap Sub-GHz → tap OK on "Read". The Flipper
    remembers its last-used frequency, so this enters Read at whatever
    frequency was configured last (use the app's arrow keys to change).
    """
    return subghz_read_start_ui(_get_bridge())


@mcp.tool()
def workflow_nfc_read_start() -> dict:
    """Launch the NFC app and navigate into Read mode in one call."""
    return nfc_read_start_ui(_get_bridge())


@mcp.tool()
def workflow_infrared_learn_start() -> dict:
    """Launch the Infrared app and navigate into Learn New Remote."""
    return infrared_learn_start_ui(_get_bridge())


@mcp.tool()
def workflow_close_app() -> dict:
    """Close any app currently running on the Flipper; return to main screen.

    Useful as a prologue before direct-radio CLI commands (``subghz rx``,
    ``led``, ``vibro``) which fail with "app open" while any app owns
    the UI / radio.
    """
    return close_any_running_app(_get_bridge())


# -- universal IR database (v0.5) ------------------------------------------
#
# Flipper firmware ships a universal remote database covering common
# categories (TV, AC, audio, fan) with named buttons like Power, VolUp,
# Mute, etc. These tools wrap ``ir universal send <category> <button>``
# so the agent can drive common electronics without needing saved codes.
#
# Requires ``cli_ir.fal`` plugin loaded — same "failed to load external
# command" caveat as ir_tx applies.


_UNIVERSAL_CATEGORIES = {"tv", "audio", "ac", "fan"}


@mcp.tool()
def ir_universal_list(category: str) -> str:
    """List the buttons available for a universal-remote category.

    category: tv | audio | ac | fan.
    """
    cat = category.lower().strip()
    if cat not in _UNIVERSAL_CATEGORIES:
        return (
            f"error: unknown category {category!r}. "
            f"Valid: {sorted(_UNIVERSAL_CATEGORIES)}."
        )
    return _get_bridge().send(f"ir universal list {cat}")


@mcp.tool()
def ir_universal_send(category: str, button: str) -> str:
    """Transmit a universal-remote IR button from the firmware's built-in DB.

    category: tv | audio | ac | fan.
    button: a button name as returned by ``ir_universal_list`` — typical
    values include ``Power``, ``VolUp``, ``VolDown``, ``Mute``, ``Ch+``,
    ``Ch-``, ``Play``, ``Pause``. Case-sensitive on most firmwares.

    Example: turn off any TV in range:
        ir_universal_send(category="tv", button="Power")
    """
    cat = category.lower().strip()
    if cat not in _UNIVERSAL_CATEGORIES:
        return (
            f"error: unknown category {category!r}. "
            f"Valid: {sorted(_UNIVERSAL_CATEGORIES)}."
        )
    return _get_bridge().send(f"ir universal send {cat} {button}")


# -- registry (L2) ---------------------------------------------------------


@mcp.tool()
def registry_list(
    category: Optional[str] = None,
    pack: Optional[str] = None,
) -> list[dict]:
    """List known protocols in the bundled registry.

    category: filter to one of subghz | ir | nfc | lfrfid | ble
    pack: filter to a vertical pack, e.g. "garage", "access-control"
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
    frequency_hz: int,
    duration_s: float = 15.0,
    device: Optional[int] = None,
) -> dict:
    """Listen on SubGHz, then fingerprint the capture against the registry.

    One-shot workflow: runs ``subghz rx``, collects output for ``duration_s``,
    stops the scan, then matches the output against every known SubGHz
    protocol. Returns both the raw text (for human review) and the
    structured matches with decoded fields and metadata.

    device: 0 = internal CC1101, 1 = external CC1101 module. If unset,
    falls back to the FLIPPER_DEFAULT_DEVICE env var (default 0).

    Examples:
      frequency_hz=433920000 duration_s=15   # common key fobs / garage remotes
      frequency_hz=315000000 duration_s=15   # older US garage / gate remotes
      frequency_hz=868000000 duration_s=15   # EU ISM (smart meters, alarms)
    """
    dev = device if device is not None else _DEFAULT_DEVICE
    raw = _get_bridge().stream(f"subghz rx {frequency_hz} {dev}", duration_s)
    matches = get_registry().fingerprint(raw, category="subghz")
    return {
        "frequency_hz": frequency_hz,
        "duration_s": duration_s,
        "device": dev,
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


# -- remote registry sync (L3 proper) --------------------------------------


@mcp.tool()
def registry_status() -> dict:
    """Show registry state: bundled protocols, user-installed, cache location."""
    return {
        "bundled_count": len(bundled_protocols()),
        "bundled": bundled_protocols(),
        "user_installed_count": len(installed_protocols()),
        "user_installed": installed_protocols(),
        "cache_dir": str(user_cache_dir()),
        "total_loaded": len(get_registry().all()),
    }


@mcp.tool()
def registry_fetch_index(index_url: str) -> list[dict]:
    """Fetch a remote registry index and list what's available.

    index_url: HTTPS URL to a JSON index file following the flipper-rf-registry
    schema (see CONTRIBUTING.md). Each entry in the returned list flags whether
    the protocol is already bundled or installed.
    """
    idx = fetch_index(index_url)
    have_bundled = set(bundled_protocols())
    have_installed = set(installed_protocols())
    return [
        {
            "id": e.id,
            "name": e.name,
            "category": e.category,
            "url": e.url,
            "sha256": e.sha256,
            "version": e.version,
            "packs": e.packs,
            "bundled": e.id in have_bundled,
            "installed": e.id in have_installed,
        }
        for e in idx.protocols
    ]


@mcp.tool()
def registry_install(index_url: str, protocol_id: str) -> dict:
    """Install one protocol from a remote index into the user cache.

    Downloads the JSON payload, verifies the SHA-256 checksum if present,
    validates the schema, and drops it into ``user_cache_dir``. The new
    protocol becomes visible to ``registry_list`` and ``scan_and_identify``
    on the next call.
    """
    idx = fetch_index(index_url)
    entry = next((e for e in idx.protocols if e.id == protocol_id), None)
    if entry is None:
        return {"error": f"Protocol '{protocol_id}' not found in index {index_url}"}
    path = install_from_entry(entry)
    reset_registry()
    return {
        "installed": protocol_id,
        "path": str(path),
        "sha256_verified": bool(entry.sha256),
        "source_index": index_url,
    }


@mcp.tool()
def registry_remove(protocol_id: str) -> dict:
    """Remove a user-installed protocol from the cache.

    Bundled protocols (shipped with the package) cannot be removed — this only
    affects the user cache. Reloads the registry so the change takes effect
    immediately.
    """
    removed = uninstall_from_cache(protocol_id)
    if removed:
        reset_registry()
        return {"removed": protocol_id}
    return {
        "error": (
            f"'{protocol_id}' is not in the user cache. "
            "Bundled-only protocols cannot be removed."
        )
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
