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
    expected = {
        "princeton",
        "keeloq",
        "came-atomo",
        "nice-flo",
        "nec-ir",
        "tpms-generic",
        "hormann-hsm",
        "faac-slh",
        "security-plus-2",
        "ht12",
        "samsung32-ir",
        "sony-ir",
        "em4100",
    }
    assert expected <= ids


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
    garage = r.list(pack="garage")
    assert len(garage) >= 4
    assert any(p.id == "princeton" for p in garage)
    assert any(p.id == "keeloq" for p in garage)


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


# -- expanded protocol fingerprinting ---------------------------------------


def test_fingerprint_hormann():
    r = get_registry()
    sample = "Decoded: Hormann HSM4\nKey: 0xABCDEF\nSn: 0x123\nCnt: 0x5"
    hit = next(
        (
            m
            for m in r.fingerprint(sample, category="subghz")
            if m.protocol.id == "hormann-hsm"
        ),
        None,
    )
    assert hit is not None
    assert hit.fields.get("key") == "0xABCDEF"


def test_fingerprint_security_plus_2():
    r = get_registry()
    sample = "Decoded: Security+ 2.0\nFix: 0xCAFE\nHop: 0xBEEF\nSn: 0x1234\nCnt: 0x0001"
    hit = next(
        (
            m
            for m in r.fingerprint(sample, category="subghz")
            if m.protocol.id == "security-plus-2"
        ),
        None,
    )
    assert hit is not None
    assert hit.fields.get("fix") == "0xCAFE"


def test_fingerprint_samsung_ir():
    r = get_registry()
    sample = "Protocol: Samsung32\nAddress: 0x07 0x07\nCommand: 0x02"
    ids = [m.protocol.id for m in r.fingerprint(sample, category="ir")]
    assert "samsung32-ir" in ids


def test_fingerprint_sony_ir():
    r = get_registry()
    sample = "Protocol: SIRC\nAddress: 0x01\nCommand: 0x15"
    ids = [m.protocol.id for m in r.fingerprint(sample, category="ir")]
    assert "sony-ir" in ids


def test_fingerprint_em4100():
    r = get_registry()
    sample = "Read EM4100\nID: 0A B1 C2 D3 E4"
    hit = next(
        (
            m
            for m in r.fingerprint(sample, category="lfrfid")
            if m.protocol.id == "em4100"
        ),
        None,
    )
    assert hit is not None
    assert "0A B1 C2 D3 E4" in hit.fields.get("uid", "")


def test_fingerprint_ht12():
    r = get_registry()
    sample = "Decoded: HT12E\nAddress: 0x3F\nData: 0x05\nKey: 0x3F05"
    hit = next(
        (
            m
            for m in r.fingerprint(sample, category="subghz")
            if m.protocol.id == "ht12"
        ),
        None,
    )
    assert hit is not None
