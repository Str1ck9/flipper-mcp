# flipper-mcp

An [MCP](https://modelcontextprotocol.io) server that gives an LLM hands on a
[Flipper Zero](https://flipper.net/) over USB-CDC.

Point Claude (or any MCP-aware agent) at a plugged-in Flipper and ask things
like:

> Scan 433.92 MHz for 15 seconds and tell me what you pick up.

> What's saved in `/ext/subghz` on my Flipper?

> Blink the LED magenta for a second so I know you're online.

## What this is (and isn't)

**Is:** a thin, honest bridge. Every tool wraps a real Flipper CLI command.
No cloud, no proprietary magic. Everything happens on your machine.

**Isn't:** a hacking tool. It doesn't unlock anything the Flipper itself
couldn't unlock.

## Status

| Layer | What | Status |
|---|---|---|
| **L1** | USB-CDC bridge + 18 MCP tools (info, storage, SubGHz/IR/NFC capture, LED, vibro) | ✅ working |
| **L2** | Protocol registry — 6 seed entries, fingerprinter, `scan_and_identify` | ✅ working |
| **L3** | Remote registry sync (fetch new decoders on demand) | ⏳ planned |
| **Pack** | FleetRF — paratransit / fleet RF surfaces (garage, gate, TPMS, lift remotes) | 🌱 seeded, accepting contributions |

## Install

Requires a Flipper Zero and Python 3.11+.

```bash
git clone https://github.com/Str1ck9/flipper-mcp
cd flipper-mcp
uv venv
uv pip install -e .
```

Verify it sees your Flipper:

```bash
source .venv/bin/activate
flipper-smoke
# → connected: /dev/cu.usbmodemflip_XXXX
# → (device_info output)
```

## Wire into Claude Code

```bash
claude mcp add --scope user flipper $(pwd)/.venv/bin/flipper-mcp
```

Restart Claude Code. The `mcp__flipper__*` tools appear in every project.

## Wire into Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "flipper": {
      "command": "/absolute/path/to/flipper-mcp/.venv/bin/flipper-mcp"
    }
  }
}
```

## Tool surface

### Device
| Tool | Purpose |
|---|---|
| `flipper_info` | Firmware, hardware, UID |
| `flipper_help` | List every CLI command the firmware supports |
| `flipper_cli(command, timeout_s)` | Run an arbitrary CLI command (escape hatch) |
| `ps`, `free`, `uptime` | System state |

### Storage
| Tool | Purpose |
|---|---|
| `storage_list(path)` | List a directory (default `/ext`) |
| `storage_read(path)` | Read a file's contents |
| `storage_stat(path)` | File/dir metadata |
| `storage_info(path)` | Free / total space |

### Radio
| Tool | Purpose |
|---|---|
| `subghz_rx(frequency_hz, duration_s)` | Listen for RF signals |
| `subghz_tx_from_file(sub_file_path)` | Transmit a saved `.sub` file |
| `subghz_decode_raw(sub_file_path)` | Decode a raw capture |
| `nfc_detect(duration_s)` | Scan for NFC tags |
| `ir_rx(duration_s)` | Listen for IR |
| `ir_tx(protocol, address, command)` | Transmit an IR command |

### Physical
| Tool | Purpose |
|---|---|
| `led(r, g, b)` | Set the RGB LED (0–255 each) |
| `vibro(on)` | Pulse the motor |

### Registry (L2)
| Tool | Purpose |
|---|---|
| `registry_list(category, pack)` | Browse known protocols |
| `registry_describe(protocol_id)` | Full entry for one protocol |
| `scan_and_identify(frequency_hz, duration_s)` | Capture + fingerprint in one step |
| `ir_scan_and_identify(duration_s)` | Same, for IR |

## The registry

Protocol definitions live in [`flipper_mcp/protocols/`](flipper_mcp/protocols/)
as individual JSON files. Each entry documents the protocol's typical
frequencies, modulation, fingerprint patterns (regexes matched against the
Flipper's decoded-signal output), expected devices, and a plain-English
security note.

Seeded with: Princeton (PT2262/EV1527), KeeLoq (HCS200/301), CAME Atomo,
Nice FLO, NEC IR, generic TPMS. Contributions are welcome — PR a new JSON
file with fingerprint regexes and typical-device metadata.

### Schema

```jsonc
{
  "id": "princeton",
  "name": "Princeton PT2262/PT2260/EV1527",
  "category": "subghz",                        // subghz | ir | nfc | lfrfid | ble
  "typical_frequencies_hz": [315000000, 433920000],
  "modulation": "OOK",
  "typical_bit_rate": 2000,
  "fingerprint": {
    "regex_patterns": ["Princeton", "PT2262", "EV1527"],
    "bit_length_range": [24, 25]
  },
  "decoder": {
    "fields": {
      "key": {"regex": "Key:\\s*(0x[0-9a-fA-F]+)"},
      "bit": {"regex": "Bit:\\s*(\\d+)"}
    }
  },
  "typical_devices": ["garage door openers", "simple remote switches"],
  "security_notes": "Fixed-code. Trivially replayable.",
  "references": ["https://en.wikipedia.org/wiki/..."],
  "packs": ["fleetrf"]
}
```

## Troubleshooting

**"Flipper port is busy — another app has it open"**
Close the **Flipper Lab** tab in Chrome (`lab.flipper.net` uses Web Serial
and takes an exclusive lock), quit qFlipper, or kill any `screen` / `tio`
sessions on the port.

**"No Flipper Zero detected"**
Plug in via USB-C (**data cable**, not charge-only), unlock the Flipper
screen, and confirm the CDC interface is enabled (it is by default).

**A frequency is rejected**
Firmware enforces regional Sub-GHz bands. Check `hardware_region_provisioned`
in `flipper_info` output — mine is `US`, yours may differ.

**Streaming commands return empty output**
Make sure the Flipper isn't locked mid-capture, and that nothing else holds
the port. For weak signals, extend `duration_s` and try known-good
transmitters (car fob at close range).

## Development

```bash
uv pip install -e ".[dev]"
pytest
```

## Acknowledgements

- [navcore.io's Pi.dev Flipper
  article](https://blog.navcore.io/AI-Agents/Giving-a-Pi.dev-Agent-Hands-on-a-Flipper-Zero) —
  the USB-CDC quiet-period trick is theirs, and it works.
- The broader AI-on-Flipper landscape — FlipperClaw, V3SP3R, AgentFlipper,
  Gemini-Flipper, OpenFlip — that set the direction.

## License

MIT. See [LICENSE](LICENSE).
