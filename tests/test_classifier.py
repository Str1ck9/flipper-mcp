"""Tests for the Flipper save-file header classifier."""

from __future__ import annotations

from flipper_mcp.registry import classify_file


SUBGHZ_RAW = """Filetype: Flipper SubGhz RAW File
Version: 1
Frequency: 315000000
Preset: FuriHalSubGhzPresetOok270Async
Protocol: RAW
RAW_Data: 1604 -14506 197 -132 195 -96
"""

SUBGHZ_PARSED = """Filetype: Flipper SubGhz Key File
Version: 1
Frequency: 433920000
Preset: FuriHalSubGhzPresetOok650Async
Protocol: Princeton
Bit: 24
Key: 00 00 00 00 00 12 34 56
TE: 400
"""

IR_PARSED = """Filetype: IR signals file
Version: 1
#
name: Power
type: parsed
protocol: NECext
address: EA C7 00 00
command: 17 E8 00 00
"""

IR_RAW = """Filetype: IR signals file
Version: 1
#
name: Power
type: raw
frequency: 38000
duty_cycle: 0.33
data: 9024 4512 564 564 564 1692
"""

NFC_FILE = """Filetype: Flipper NFC device
Version: 4
Device type: NTAG/Ultralight
UID: 04 1D 7D AE 66 26 81
ATQA: 00 44
SAK: 00
"""

RFID_FILE = """Filetype: Flipper RFID key
Version: 1
Key type: EM4100
Data: 12 34 56 78 90
"""

IBUTTON_FILE = """Filetype: Flipper iButton key
Version: 2
Key type: Dallas
Data: 01 02 03 04 05 06 07 08
"""


def test_subghz_raw_detected():
    m = classify_file(SUBGHZ_RAW)
    assert m["kind"] == "subghz_raw"
    assert m["frequency_hz"] == 315000000
    assert m["preset"] == "FuriHalSubGhzPresetOok270Async"
    assert m["protocol_named"] == "RAW"


def test_subghz_parsed_detected():
    m = classify_file(SUBGHZ_PARSED)
    assert m["kind"] == "subghz_parsed"
    assert m["frequency_hz"] == 433920000
    assert m["protocol_named"] == "Princeton"


def test_ir_parsed_detected():
    m = classify_file(IR_PARSED)
    assert m["kind"] == "ir"
    assert m["protocol_named"] == "NECext"
    assert m["ir_signal_type"] == "parsed"


def test_ir_raw_detected():
    m = classify_file(IR_RAW)
    assert m["kind"] == "ir"
    assert m["ir_signal_type"] == "raw"
    assert m["frequency_hz"] == 38000


def test_nfc_detected():
    m = classify_file(NFC_FILE)
    assert m["kind"] == "nfc"
    assert m["device_type"] == "NTAG/Ultralight"
    assert m["uid"] == "04 1D 7D AE 66 26 81"


def test_rfid_detected():
    m = classify_file(RFID_FILE)
    assert m["kind"] == "lfrfid"
    assert m["key_type"] == "EM4100"
    assert m["data"] == "12 34 56 78 90"


def test_ibutton_detected():
    m = classify_file(IBUTTON_FILE)
    assert m["kind"] == "ibutton"
    assert m["key_type"] == "Dallas"


def test_unknown_file_returns_unknown_kind():
    m = classify_file("this is just some random text\nwithout a header\n")
    assert m["kind"] == "unknown"


def test_missing_filetype_with_known_structure():
    # Even if the body looks like a Flipper file, no Filetype header = unknown.
    m = classify_file("Frequency: 315000000\nProtocol: Princeton\n")
    assert m["kind"] == "unknown"


def test_bad_frequency_returns_none():
    # Malformed Frequency value shouldn't raise.
    content = "Filetype: Flipper SubGhz RAW File\nFrequency: not_a_number\n"
    m = classify_file(content)
    assert m["kind"] == "subghz_raw"
    assert m["frequency_hz"] is None
