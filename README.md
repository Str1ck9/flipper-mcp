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

[![CI](https://github.com/Str1ck9/flipper-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Str1ck9/flipper-mcp/actions/workflows/ci.yml)

## Status

| Layer | What | Status |
|---|---|---|
| **L1** | USB-CDC bridge + MCP tools (info, storage, SubGHz/IR/NFC capture, LED, vibro) | ✅ working, validated on real hardware |
| **L2** | Protocol registry — 13 bundled entries, fingerprinter, `scan_and_identify` | ✅ working |
| **L3** | Remote registry sync — fetch signed protocol JSON on demand into a user cache | ✅ working |
| **CLI** | `flipper-registry` for cache management and JSON validation | ✅ working |

**Bundled protocols (13):** Princeton, KeeLoq, CAME Atomo, Nice FLO, Hormann
HSM, FAAC SLH, Security+ 2.0, HT12, TPMS (generic), NEC IR, Samsung32 IR,
Sony SIRC, EM4100.

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
| `scan_and_identify(frequency_hz, duration_s, device)` | Capture + fingerprint in one step |
| `ir_scan_and_identify(duration_s)` | Same, for IR |

### Remote registry sync (L3)
| Tool | Purpose |
|---|---|
| `registry_status` | Show bundled + installed protocols, cache location |
| `registry_fetch_index(index_url)` | List protocols available in a remote index |
| `registry_install(index_url, protocol_id)` | Download, SHA-256 verify, install into user cache |
| `registry_remove(protocol_id)` | Remove a user-installed protocol |

### CLI

The same surface is exposed as a shell command for humans:

```bash
flipper-registry status
flipper-registry list --pack garage
flipper-registry describe princeton
flipper-registry index https://example.com/index.json
flipper-registry install https://example.com/index.json <protocol-id>
flipper-registry remove <protocol-id>
flipper-registry validate my-new-protocol.json
```

## The registry

Protocol definitions live in [`flipper_mcp/protocols/`](flipper_mcp/protocols/)
as individual JSON files. Each entry documents the protocol's typical
frequencies, modulation, fingerprint patterns (regexes matched against the
Flipper's decoded-signal output), expected devices, and a plain-English
security note.

Ships with 13 entries covering common SubGHz / IR / RFID protocols.
Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
schema, sample capture workflow, and PR checklist.

### Remote index format

`registry_fetch_index` / `flipper-registry index <URL>` expects a JSON file
shaped like:

```jsonc
{
  "schema_version": 1,
  "name": "my-flipper-registry",
  "description": "Community-curated extra protocols",
  "protocols": [
    {
      "id": "my-new-protocol",
      "url": "https://example.com/my-new-protocol.json",
      "sha256": "aabbcc...",         // optional; enforced if present
      "version": 1,
      "name": "My New Protocol",
      "category": "subghz",
      "packs": ["garage"]
    }
  ]
}
```

Install path (one entry): `$XDG_DATA_HOME/flipper-mcp/protocols/<id>.json`
or `~/.local/share/flipper-mcp/protocols/<id>.json`. User-cached entries
override bundled entries with the same `id`.

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
  "packs": ["garage"]
}
```

## External CC1101 module

If you have an external CC1101 plugged into the GPIO header (better range, a
second antenna path), pass `device=1` to `subghz_rx` / `scan_and_identify`
for a single call, or export an env var to make it the default:

```bash
export FLIPPER_DEFAULT_DEVICE=1
```

Unset or `0` uses the Flipper's internal CC1101.

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
