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

import hashlib
import importlib.resources
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError


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
        """Load only the protocols bundled with the package."""
        protos: list[Protocol] = []
        pkg = importlib.resources.files("flipper_mcp") / "protocols"
        for entry in pkg.iterdir():
            if entry.name.endswith(".json"):
                data = json.loads(entry.read_text(encoding="utf-8"))
                protos.append(Protocol(**data))
        return cls(protos)

    @classmethod
    def load(cls, include_user_cache: bool = True) -> "Registry":
        """Load bundled protocols, overlaid with the user cache.

        User-installed protocols with the same ``id`` as a bundled entry
        override the bundled version. This is how remote-fetched updates
        supersede the shipped defaults.
        """
        protos: dict[str, Protocol] = {}
        pkg = importlib.resources.files("flipper_mcp") / "protocols"
        for entry in pkg.iterdir():
            if entry.name.endswith(".json"):
                data = json.loads(entry.read_text(encoding="utf-8"))
                p = Protocol(**data)
                protos[p.id] = p
        if include_user_cache:
            cache = user_cache_dir()
            if cache.is_dir():
                for entry in cache.iterdir():
                    if entry.name.endswith(".json"):
                        data = json.loads(entry.read_text(encoding="utf-8"))
                        p = Protocol(**data)
                        protos[p.id] = p
        return cls(list(protos.values()))

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

    # Flipper's subghz listener prints a preamble of keystore-load lines and
    # a "Listening at frequency:" heartbeat before any real signals arrive.
    # We strip these before fingerprinting so that protocol names appearing
    # as filenames (e.g. "keeloq_mfcodes") don't cause false positives.
    _PREAMBLE_PATTERNS = (
        re.compile(r"^Load_keystore\s+\S+\s+\S+$", re.MULTILINE),
        re.compile(r"^Listening at frequency:.*$", re.MULTILINE),
        re.compile(r"^Packets received\s+\d+\s*$", re.MULTILINE),
    )

    @classmethod
    def _strip_preamble(cls, text: str) -> str:
        for pat in cls._PREAMBLE_PATTERNS:
            text = pat.sub("", text)
        return text

    def fingerprint(self, text: str, category: Optional[str] = None) -> list[Match]:
        """Return matches for every protocol whose patterns hit in ``text``.

        Matching is case-insensitive + multi-line. Each protocol matches at
        most once (first pattern wins), and decoder fields are extracted
        from the same text. Boilerplate from the Flipper's CLI (keystore
        loads, "Listening at..." heartbeat, "Packets received N" trailer)
        is stripped before matching.
        """
        cleaned = self._strip_preamble(text)

        matches: list[Match] = []
        for proto in self.list(category=category):
            hit_pattern: Optional[str] = None
            for pattern in proto.fingerprint.regex_patterns:
                if re.search(pattern, cleaned, flags=re.IGNORECASE | re.MULTILINE):
                    hit_pattern = pattern
                    break
            if hit_pattern is None:
                continue

            fields: dict[str, str] = {}
            for fname, fdef in proto.decoder.fields.items():
                m = re.search(fdef.regex, cleaned)
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


# Singleton — loaded on first call, resettable after mutating the cache.
_registry: Optional[Registry] = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry.load()
    return _registry


def reset_registry() -> None:
    """Force the next get_registry() call to reload from disk."""
    global _registry
    _registry = None


# ---------------------------------------------------------------------------
# Remote registry sync (L3) — fetch and install signed protocol JSON from a
# trusted index URL into a per-user cache. Signature verification is stubbed
# (sha256 only for now); the structure is ready for minisign/GPG later.
# ---------------------------------------------------------------------------


class RemoteProtocolEntry(BaseModel):
    id: str
    url: str
    sha256: Optional[str] = None
    version: int = 1
    name: Optional[str] = None
    category: Optional[str] = None
    packs: list[str] = Field(default_factory=list)


class RemoteIndex(BaseModel):
    schema_version: int = 1
    name: str = "flipper-rf-registry"
    description: Optional[str] = None
    protocols: list[RemoteProtocolEntry] = Field(default_factory=list)


class RegistryError(RuntimeError):
    pass


def user_cache_dir() -> Path:
    """Per-user cache path for installed protocols.

    Honors XDG_DATA_HOME on Linux / macOS. Falls back to ``~/.local/share``.
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "flipper-mcp" / "protocols"


def fetch_index(url: str, timeout: float = 15.0) -> RemoteIndex:
    """Download and parse a remote registry index."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "flipper-mcp/0.3 (+registry-sync)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return RemoteIndex(**data)
    except ValidationError as e:
        raise RegistryError(f"Malformed registry index at {url}: {e}") from e


def install_from_entry(entry: RemoteProtocolEntry, timeout: float = 15.0) -> Path:
    """Download one protocol, verify sha256 if present, install into cache."""
    req = urllib.request.Request(
        entry.url,
        headers={"User-Agent": "flipper-mcp/0.3 (+registry-sync)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read()

    if entry.sha256:
        actual = hashlib.sha256(body).hexdigest().lower()
        expected = entry.sha256.lower()
        if actual != expected:
            raise RegistryError(
                f"SHA-256 mismatch for {entry.id}: expected {expected}, got {actual}"
            )

    try:
        proto_data = json.loads(body.decode("utf-8"))
        Protocol(**proto_data)  # validate schema
    except (ValidationError, json.JSONDecodeError) as e:
        raise RegistryError(
            f"Downloaded protocol {entry.id} failed validation: {e}"
        ) from e

    if proto_data.get("id") != entry.id:
        raise RegistryError(
            f"Entry id {entry.id!r} does not match payload id {proto_data.get('id')!r}"
        )

    cache = user_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{entry.id}.json"
    path.write_bytes(body)
    return path


def uninstall_from_cache(protocol_id: str) -> bool:
    """Remove a cached protocol. Returns True if a file was removed."""
    path = user_cache_dir() / f"{protocol_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def installed_protocols() -> list[str]:
    """List protocol IDs currently installed in the user cache."""
    cache = user_cache_dir()
    if not cache.is_dir():
        return []
    return sorted(
        entry.name[:-5] for entry in cache.iterdir() if entry.name.endswith(".json")
    )


def bundled_protocols() -> list[str]:
    """List protocol IDs shipped with the package."""
    pkg = importlib.resources.files("flipper_mcp") / "protocols"
    return sorted(
        entry.name[:-5] for entry in pkg.iterdir() if entry.name.endswith(".json")
    )
