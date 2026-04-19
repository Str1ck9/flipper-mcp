"""Serial bridge to a Flipper Zero over USB-CDC.

Design notes:
  - Flipper exposes a line-oriented CLI over /dev/cu.usbmodemflip_* on macOS
    and /dev/serial/by-id/*Flipper* on Linux. Default framing is just CRLF.
  - The CLI echoes input and paints ANSI colors/escape codes. Both are
    stripped before returning output to the caller.
  - We don't rely solely on the `>: ` prompt to detect command completion.
    Some commands (subghz rx, ir rx, nfc detect) stream until interrupted,
    and others print the prompt mid-output. Instead we use a quiet-period
    detector: the command is "done" when the serial buffer has been silent
    for `quiet_ms` milliseconds. navcore's pi-flipper-hid uses the same
    trick and it's empirically robust.
  - Reads happen on a background thread that appends to a shared bytearray
    under a lock. This avoids blocking the main thread and lets us peek at
    buffer size for the quiet-period check.
"""

from __future__ import annotations

import argparse
import glob
import os
import platform
import re
import sys
import threading
import time
from typing import Optional

import serial

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[=>]|\x1b\][^\x07]*\x07")
PROMPT = ">:"


class FlipperError(RuntimeError):
    pass


class FlipperBridge:
    """Thin wrapper around a Flipper Zero's USB-CDC CLI.

    Use ``send(cmd)`` for short one-shot commands that return a prompt.
    Use ``stream(cmd, duration)`` for commands that run continuously and
    need a Ctrl-C to stop (subghz rx, ir rx, nfc detect, etc).
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 115200,
        read_timeout: float = 0.05,
    ) -> None:
        self.port = port or self._auto_detect()
        try:
            self._ser = serial.Serial(
                self.port,
                baudrate=baudrate,
                timeout=read_timeout,
                write_timeout=2.0,
            )
        except serial.SerialException as e:
            # Errno 16 (EBUSY) on macOS almost always means Chrome's Web Serial
            # API (lab.flipper.net) or qFlipper has the port open. Give the
            # caller an actionable fix, not a stack trace.
            if "Resource busy" in str(e) or "Errno 16" in str(e):
                raise FlipperError(
                    f"Flipper port {self.port} is busy — another app has it open. "
                    "Common culprits: the 'Flipper Lab' tab in Chrome "
                    "(lab.flipper.net), qFlipper, or an open `screen`/`tio` "
                    "session. Close it and retry."
                ) from e
            raise FlipperError(f"Could not open {self.port}: {e}") from e
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = threading.Thread(
            target=self._read_loop, name="flipper-reader", daemon=True
        )
        self._reader.start()
        self._handshake()

    # -- discovery ----------------------------------------------------------

    @staticmethod
    def _auto_detect() -> str:
        sysname = platform.system()
        if sysname == "Darwin":
            candidates = sorted(glob.glob("/dev/cu.usbmodemflip_*"))
        elif sysname == "Linux":
            candidates = sorted(glob.glob("/dev/serial/by-id/*Flipper*"))
        else:
            candidates = []
        if not candidates:
            raise FlipperError(
                f"No Flipper Zero detected on {sysname}. "
                "Plug in via USB-C, confirm the device is unlocked, "
                "or set FLIPPER_PORT to an explicit device path."
            )
        return candidates[0]

    # -- reader thread ------------------------------------------------------

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._ser.read(4096)
            except serial.SerialException:
                break
            except OSError:
                break
            if data:
                with self._lock:
                    self._buf.extend(data)

    def _drain(self) -> None:
        with self._lock:
            self._buf.clear()

    def _snapshot(self) -> bytes:
        with self._lock:
            data = bytes(self._buf)
            self._buf.clear()
        return data

    def _peek_size(self) -> int:
        with self._lock:
            return len(self._buf)

    # -- writing ------------------------------------------------------------

    def _write(self, payload: str) -> None:
        self._ser.write(payload.encode("utf-8"))
        self._ser.flush()

    # -- waiting ------------------------------------------------------------

    def _wait_quiet(self, timeout: float, quiet_ms: int) -> None:
        """Block until the buffer has been silent for `quiet_ms`, or timeout."""
        quiet_s = quiet_ms / 1000.0
        deadline = time.monotonic() + timeout
        last_size = self._peek_size()
        last_change = time.monotonic()

        while time.monotonic() < deadline:
            time.sleep(0.02)
            size = self._peek_size()
            now = time.monotonic()
            if size != last_size:
                last_size = size
                last_change = now
            elif size > 0 and (now - last_change) >= quiet_s:
                return
        # Timed out — return whatever we have

    # -- cleanup ------------------------------------------------------------

    @staticmethod
    def _clean(raw: bytes, cmd: Optional[str] = None) -> str:
        text = raw.decode("utf-8", errors="replace")
        text = ANSI_RE.sub("", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Strip a leading echo of the command we just sent.
        if cmd:
            stripped_cmd = cmd.strip()
            lines = text.split("\n", 1)
            if lines and lines[0].strip() == stripped_cmd:
                text = lines[1] if len(lines) > 1 else ""

        # Strip trailing prompt (">:" with optional whitespace).
        text = text.rstrip()
        while text.endswith(PROMPT):
            text = text[: -len(PROMPT)].rstrip()
        return text

    # -- lifecycle ----------------------------------------------------------

    def _handshake(self) -> None:
        # macOS opens USB-CDC with DTR asserted, which triggers the Flipper's
        # welcome banner (ASCII dolphin + firmware string + prompt). If we
        # drain too early the banner leaks into the first command's output.
        # Wait for the line to go quiet, *then* drain.
        self._write("\r\n")
        self._wait_quiet(timeout=3.0, quiet_ms=500)
        self._drain()

    def close(self) -> None:
        self._stop.set()
        try:
            self._ser.close()
        except Exception:
            pass

    def __enter__(self) -> "FlipperBridge":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API ---------------------------------------------------------

    def send(self, cmd: str, timeout: float = 10.0, quiet_ms: int = 300) -> str:
        """Run a one-shot command, return cleaned output once it settles."""
        self._drain()
        self._write(cmd + "\r\n")
        self._wait_quiet(timeout, quiet_ms)
        return self._clean(self._snapshot(), cmd)

    def stream(
        self,
        cmd: str,
        duration: float,
        post_timeout: float = 5.0,
        quiet_ms: int = 300,
    ) -> str:
        """Run a streaming command for `duration` seconds, Ctrl-C, return output."""
        self._drain()
        self._write(cmd + "\r\n")
        time.sleep(duration)
        self._write("\x03")  # Ctrl-C
        self._wait_quiet(post_timeout, quiet_ms)
        return self._clean(self._snapshot(), cmd)


# ---------------------------------------------------------------------------
# Smoke test entry point — `flipper-smoke` or `python -m flipper_mcp.bridge`.
# ---------------------------------------------------------------------------


def smoke() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the Flipper USB bridge.")
    parser.add_argument(
        "--port",
        default=os.environ.get("FLIPPER_PORT"),
        help="Explicit serial device path. Default: auto-detect.",
    )
    parser.add_argument(
        "--cmd",
        default="device_info",
        help="CLI command to run. Default: device_info.",
    )
    args = parser.parse_args()

    try:
        bridge = FlipperBridge(port=args.port)
    except FlipperError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"connected: {bridge.port}")
    try:
        output = bridge.send(args.cmd)
    finally:
        bridge.close()

    print(f"--- output of `{args.cmd}` ---")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(smoke())
