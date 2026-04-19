"""Tests for the protocol registry loader, filters, and fingerprinter."""

from __future__ import annotations

from flipper_mcp.registry import Registry, get_registry


# Synthetic samples that mimic Flipper CLI output for each protocol. These
# are intentionally permissive — real captures will have more noise around
# them — but they exercise the regex patterns.

PRINCETON_SAMPLE = """
RSSI: -65.00
Decoded: Princeton
Key: 0x00A5B4F0
Bit: 24
Te: 350us
"""

KEELOQ_SAMPLE = """
RSSI: -72.00
Decoded: KeeLoq
Key: 0xC0FFEE11DEADBEEF
Fix: 0xDEADBEEF
Hop: 0xC0FFEE11
Sn: 0x0BADF00D
Cnt: 0x0042
Btn: 0x02
"""

NEC_IR_SAMPLE = """
Protocol: NEC
Address: 0x04 0x00 0x00 0x00
Command: 0x15 0x00 0x00 0x00
"""

TPMS_SAMPLE = """
TPMS decoded
Sensor ID: 0x123ABCDE
Pressure: 2.4 bar
Temperature: 28 C
Battery: OK
"""


def test_load_bundled_has_seeds():
    r = Registry.load_bundled()
    ids = {p.id for p in r.all()}
    assert {"princeton", "keeloq", "came-atomo", "nice-flo", "nec-ir", "tpms-generic"} <= ids


def test_get_returns_none_for_unknown():
    r = Registry.load_bundled()
    assert r.get("nothing-like-this") is None


def test_filter_by_category():
    r = Registry.load_bundled()
    subghz = r.list(category="subghz")
    ir = r.list(category="ir")
    assert all(p.category == "subghz" for p in subghz)
    assert all(p.category == "ir" for p in ir)
    assert len(subghz) >= 4
    assert len(ir) >= 1


def test_filter_by_pack():
    r = Registry.load_bundled()
    fleet = r.list(pack="fleetrf")
    assert len(fleet) >= 2
    assert any(p.id == "tpms-generic" for p in fleet)
    assert any(p.id == "princeton" for p in fleet)


def test_fingerprint_princeton_extracts_fields():
    r = get_registry()
    matches = r.fingerprint(PRINCETON_SAMPLE, category="subghz")
    hit = next((m for m in matches if m.protocol.id == "princeton"), None)
    assert hit is not None
    assert hit.matched_pattern == "Princeton"
    assert hit.fields.get("key") == "0x00A5B4F0"
    assert hit.fields.get("bit") == "24"
    assert hit.fields.get("te") == "350"


def test_fingerprint_keeloq_extracts_fields():
    r = get_registry()
    matches = r.fingerprint(KEELOQ_SAMPLE, category="subghz")
    hit = next((m for m in matches if m.protocol.id == "keeloq"), None)
    assert hit is not None
    assert hit.fields.get("fix") == "0xDEADBEEF"
    assert hit.fields.get("hop") == "0xC0FFEE11"
    assert hit.fields.get("sn") == "0x0BADF00D"
    assert hit.fields.get("cnt") == "0x0042"


def test_fingerprint_nec_ir():
    r = get_registry()
    matches = r.fingerprint(NEC_IR_SAMPLE, category="ir")
    ids = [m.protocol.id for m in matches]
    assert "nec-ir" in ids


def test_fingerprint_tpms():
    r = get_registry()
    matches = r.fingerprint(TPMS_SAMPLE, category="subghz")
    hit = next((m for m in matches if m.protocol.id == "tpms-generic"), None)
    assert hit is not None
    assert hit.fields.get("sensor_id") == "0x123ABCDE"
    assert hit.fields.get("pressure") == "2.4"


def test_fingerprint_category_scoping():
    """A SubGHz sample should not match IR protocols even if text accidentally overlaps."""
    r = get_registry()
    matches = r.fingerprint(PRINCETON_SAMPLE, category="ir")
    assert not any(m.protocol.id == "princeton" for m in matches)


def test_fingerprint_no_match_on_empty_input():
    r = get_registry()
    assert r.fingerprint("", category="subghz") == []
