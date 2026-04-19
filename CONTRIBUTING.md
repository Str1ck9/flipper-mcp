# Contributing to flipper-mcp

Contributions welcome. Two distinct paths depending on what you want to add:

1. **Protocol entries** — add a radio / IR / NFC / RFID protocol to the
   registry. No Python needed.
2. **Code** — fix bugs, improve the bridge, add MCP tools.

## Adding a protocol entry

This is the easiest and highest-ROI contribution. Every new protocol entry
makes the registry identify something it couldn't before.

### 1. Capture a sample first

You need ground truth — output the Flipper produces when it sees the
protocol. Typical workflow:

```bash
flipper-smoke --cmd "subghz rx 433920000 0"
# press the remote / fob / transmitter you want to document
# Ctrl-C after a few presses
```

Save the raw output. The decoded lines are what your fingerprint regex needs
to match.

### 2. Copy an existing entry as a template

Good starting points: [`princeton.json`](flipper_mcp/protocols/princeton.json)
for fixed-code SubGHz, [`keeloq.json`](flipper_mcp/protocols/keeloq.json) for
rolling-code, [`nec-ir.json`](flipper_mcp/protocols/nec-ir.json) for IR.

### 3. Fill in the fields

```jsonc
{
  "id": "kebab-case-unique-id",
  "name": "Human-readable name with manufacturer",
  "category": "subghz",          // subghz | ir | nfc | lfrfid | ble
  "typical_frequencies_hz": [433920000],
  "modulation": "OOK",           // OOK | ASK | FSK | AM_OOK | PWM | ...
  "typical_bit_rate": 2000,      // optional
  "fingerprint": {
    "regex_patterns": [
      "ExactProtocolNameFromFlipperOutput"
    ],
    "bit_length_range": [24, 25] // optional, for disambiguation
  },
  "decoder": {
    "fields": {
      "key": {"regex": "Key:\\s*(0x[0-9a-fA-F]+)"},
      "bit": {"regex": "Bit:\\s*(\\d+)"}
    }
  },
  "typical_devices": [
    "what uses this in the real world, 3-5 examples"
  ],
  "security_notes": "Plain-English: is it secure? is it replayable? known attacks?",
  "references": [
    "URL to protocol spec, Wikipedia, or reverse-engineering writeup"
  ],
  "packs": ["fleetrf", "garage"]  // tag into relevant verticals
}
```

### 4. Validate locally

```bash
flipper-registry validate flipper_mcp/protocols/your-new-protocol.json
```

### 5. Add a test

In [`tests/test_registry.py`](tests/test_registry.py), add a sample of
expected Flipper output and a test that fingerprints it:

```python
YOUR_SAMPLE = """
Decoded: YourProtocol
Key: 0x12345678
Bit: 24
"""

def test_fingerprint_your_protocol():
    r = get_registry()
    matches = r.fingerprint(YOUR_SAMPLE, category="subghz")
    hit = next((m for m in matches if m.protocol.id == "your-protocol-id"), None)
    assert hit is not None
    assert hit.fields.get("key") == "0x12345678"
```

### 6. Run the suite and open a PR

```bash
pytest
```

Include in your PR:
- The JSON file
- A test case exercising the fingerprint
- A short capture snippet in the PR description (raw Flipper output you
  used for ground truth). Helpful for reviewers; no secrets please — strip
  any serial numbers or rolling-code counters from real hardware you don't
  own the rights to.

## Pack conventions

Packs group protocols by vertical use-case. Current packs:

| Pack | Scope |
|---|---|
| `garage` | Garage/gate openers (residential + commercial) |
| `access-control` | Access control: fobs, badges, gate/door systems |
| `fleetrf` | Paratransit and fleet RF surfaces — TPMS, lift remotes, fuel-island keys, depot fobs |

Propose new packs in your PR description if none of the existing ones fit.

## Code contributions

Standard workflow:

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
```

- Keep the bridge surface narrow. Each new MCP tool should map to a single
  Flipper CLI command or a tight, reusable workflow.
- Don't add cloud dependencies. This tool runs 100% locally by design.
- Don't silently retry or hide errors — an agent needs actionable error
  messages, not generic "something went wrong."

## License

By contributing you agree your changes are MIT-licensed under the same terms
as the repo.
