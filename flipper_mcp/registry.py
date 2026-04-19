"""Protocol registry — the L2 layer.

Each bundled JSON file under ``flipper_mcp/protocols/`` describes one
radio / IR / NFC protocol: typical frequencies, modulation, a small set of
regex fingerprints used to detect the protocol in Flipper CLI output, an
optional decoder block that extracts structured fields (key, bit length,
etc.), and free-text metadata (typical devices, security notes).

The registry is loaded once at first use and cached. Fingerprinting is a
simple ``re.search`` over the raw CLI output — intentionally permissive so
that mild firmware-version differences in output wording don't cause misses.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field


class Fingerprint(BaseModel):
    regex_patterns: list[str] = Field(default_factory=list)
    bit_length_range: Optional[list[int]] = None


class DecoderField(BaseModel):
    regex: str


class Decoder(BaseModel):
    fields: dict[str, DecoderField] = Field(default_factory=dict)


class Protocol(BaseModel):
    """One protocol definition in the registry."""

    id: str
    name: str
    category: str  # subghz | ir | nfc | lfrfid | ble
    typical_frequencies_hz: list[int] = Field(default_factory=list)
    modulation: Optional[str] = None
    typical_bit_rate: Optional[int] = None
    fingerprint: Fingerprint = Field(default_factory=Fingerprint)
    decoder: Decoder = Field(default_factory=Decoder)
    typical_devices: list[str] = Field(default_factory=list)
    security_notes: Optional[str] = None
    references: list[str] = Field(default_factory=list)
    packs: list[str] = Field(default_factory=list)


@dataclass
class Match:
    """A fingerprint hit: which protocol matched, why, and what got decoded."""

    protocol: Protocol
    matched_pattern: str
    fields: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "protocol_id": self.protocol.id,
            "protocol_name": self.protocol.name,
            "category": self.protocol.category,
            "matched_pattern": self.matched_pattern,
            "decoded_fields": self.fields,
            "typical_devices": self.protocol.typical_devices,
            "security_notes": self.protocol.security_notes,
            "references": self.protocol.references,
        }


class Registry:
    """In-memory collection of Protocol entries with filter + fingerprint APIs."""

    def __init__(self, protocols: list[Protocol]) -> None:
        self._by_id: dict[str, Protocol] = {p.id: p for p in protocols}

    # -- loading -----------------------------------------------------------

    @classmethod
    def load_bundled(cls) -> "Registry":
        """Load every protocol JSON bundled with the package."""
        protos: list[Protocol] = []
        pkg = importlib.resources.files("flipper_mcp") / "protocols"
        for entry in pkg.iterdir():
            if entry.name.endswith(".json"):
                data = json.loads(entry.read_text(encoding="utf-8"))
                protos.append(Protocol(**data))
        return cls(protos)

    # -- querying ----------------------------------------------------------

    def all(self) -> list[Protocol]:
        return list(self._by_id.values())

    def list(
        self,
        category: Optional[str] = None,
        pack: Optional[str] = None,
    ) -> list[Protocol]:
        result = self.all()
        if category:
            result = [p for p in result if p.category == category]
        if pack:
            result = [p for p in result if pack in p.packs]
        return sorted(result, key=lambda p: (p.category, p.id))

    def get(self, protocol_id: str) -> Optional[Protocol]:
        return self._by_id.get(protocol_id)

    # -- fingerprinting ----------------------------------------------------

    def fingerprint(
        self, text: str, category: Optional[str] = None
    ) -> list[Match]:
        """Return matches for every protocol whose patterns hit in ``text``.

        Matching is case-insensitive + multi-line. Each protocol matches at
        most once (first pattern wins), and decoder fields are extracted
        from the same text.
        """
        matches: list[Match] = []
        for proto in self.list(category=category):
            hit_pattern: Optional[str] = None
            for pattern in proto.fingerprint.regex_patterns:
                if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
                    hit_pattern = pattern
                    break
            if hit_pattern is None:
                continue

            fields: dict[str, str] = {}
            for fname, fdef in proto.decoder.fields.items():
                m = re.search(fdef.regex, text)
                if m and m.groups():
                    fields[fname] = m.group(1).strip()

            matches.append(
                Match(
                    protocol=proto,
                    matched_pattern=hit_pattern,
                    fields=fields,
                )
            )
        return matches


# Singleton — loaded on first call.
_registry: Optional[Registry] = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry.load_bundled()
    return _registry
