"""Tests for the remote registry sync path.

Uses a local HTTP server and a tmp user cache so these tests never reach the
network and never touch the real user cache. Verifies:
  - index fetch + parse
  - install validates SHA-256
  - install rejects id mismatch
  - cache overlay: user-installed entry overrides bundled same-id entry
  - uninstall works
"""

from __future__ import annotations

import hashlib
import http.server
import json
import socketserver
import threading
from pathlib import Path

import pytest

from flipper_mcp import registry as reg_mod
from flipper_mcp.registry import (
    Registry,
    RegistryError,
    fetch_index,
    install_from_entry,
    uninstall_from_cache,
    RemoteIndex,
    RemoteProtocolEntry,
)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Redirect user_cache_dir() into a tmp dir for the duration of a test."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # Force reset so subsequent get_registry() calls reload.
    reg_mod.reset_registry()
    yield tmp_path / "flipper-mcp" / "protocols"
    reg_mod.reset_registry()


@pytest.fixture
def http_server(tmp_path):
    """Serve `tmp_path` over HTTP on localhost; yields base URL."""
    directory = tmp_path / "www"
    directory.mkdir()

    handler_cls = http.server.SimpleHTTPRequestHandler

    class QuietHandler(handler_cls):
        def log_message(self, *_):
            pass

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

    httpd = socketserver.TCPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield directory, f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _write_protocol(directory: Path, data: dict) -> tuple[Path, str]:
    """Write a protocol JSON file and return (path, sha256)."""
    body = json.dumps(data).encode("utf-8")
    path = directory / f"{data['id']}.json"
    path.write_bytes(body)
    return path, hashlib.sha256(body).hexdigest()


SAMPLE_PROTOCOL = {
    "id": "test-remote-proto",
    "name": "Test Remote Protocol",
    "category": "subghz",
    "typical_frequencies_hz": [433920000],
    "fingerprint": {"regex_patterns": ["TestRemote"]},
    "decoder": {"fields": {"key": {"regex": "K:\\s*(0x[0-9a-f]+)"}}},
    "typical_devices": ["unit tests"],
    "packs": ["test"],
}


# -- basic index fetch ------------------------------------------------------


def test_fetch_index_reads_valid_json(http_server):
    directory, base_url = http_server
    _, sha = _write_protocol(directory, SAMPLE_PROTOCOL)
    index = {
        "schema_version": 1,
        "name": "test-registry",
        "description": "unit-test registry",
        "protocols": [
            {
                "id": "test-remote-proto",
                "url": f"{base_url}/test-remote-proto.json",
                "sha256": sha,
                "version": 1,
                "name": "Test Remote Protocol",
                "category": "subghz",
                "packs": ["test"],
            }
        ],
    }
    (directory / "index.json").write_text(json.dumps(index))

    result = fetch_index(f"{base_url}/index.json")
    assert isinstance(result, RemoteIndex)
    assert result.name == "test-registry"
    assert len(result.protocols) == 1
    assert result.protocols[0].id == "test-remote-proto"


def test_fetch_index_rejects_malformed(http_server):
    directory, base_url = http_server
    (directory / "bad.json").write_text('{"schema_version": "not-a-number"}')
    with pytest.raises(RegistryError):
        fetch_index(f"{base_url}/bad.json")


# -- install ----------------------------------------------------------------


def test_install_writes_file_and_verifies_sha256(http_server, isolated_cache, tmp_path):
    directory, base_url = http_server
    _, sha = _write_protocol(directory, SAMPLE_PROTOCOL)
    entry = RemoteProtocolEntry(
        id="test-remote-proto",
        url=f"{base_url}/test-remote-proto.json",
        sha256=sha,
    )
    path = install_from_entry(entry)
    assert path.exists()
    assert path.name == "test-remote-proto.json"
    assert path.parent == isolated_cache


def test_install_rejects_sha256_mismatch(http_server, isolated_cache):
    directory, base_url = http_server
    _write_protocol(directory, SAMPLE_PROTOCOL)
    entry = RemoteProtocolEntry(
        id="test-remote-proto",
        url=f"{base_url}/test-remote-proto.json",
        sha256="0" * 64,  # wrong
    )
    with pytest.raises(RegistryError, match="SHA-256 mismatch"):
        install_from_entry(entry)


def test_install_rejects_id_mismatch(http_server, isolated_cache):
    directory, base_url = http_server
    # File advertises id 'test-remote-proto' but entry says something else
    _write_protocol(directory, SAMPLE_PROTOCOL)
    entry = RemoteProtocolEntry(
        id="wrong-id",
        url=f"{base_url}/test-remote-proto.json",
    )
    with pytest.raises(RegistryError, match="does not match payload id"):
        install_from_entry(entry)


def test_install_rejects_schema_violation(http_server, isolated_cache):
    directory, base_url = http_server
    bad = {"id": "broken", "category": "subghz"}  # missing required 'name'
    (directory / "broken.json").write_text(json.dumps(bad))
    entry = RemoteProtocolEntry(id="broken", url=f"{base_url}/broken.json")
    with pytest.raises(RegistryError, match="failed validation"):
        install_from_entry(entry)


# -- cache overlay ----------------------------------------------------------


def test_user_cache_overrides_bundled(http_server, isolated_cache):
    """Drop a protocol into the user cache with the same id as a bundled one;
    Registry.load() should return the overridden version."""
    override = {
        **SAMPLE_PROTOCOL,
        "id": "princeton",  # shadow bundled
        "name": "USER-OVERRIDE princeton",
    }
    isolated_cache.mkdir(parents=True, exist_ok=True)
    (isolated_cache / "princeton.json").write_text(json.dumps(override))

    r = Registry.load(include_user_cache=True)
    assert r.get("princeton").name == "USER-OVERRIDE princeton"

    r_bundled_only = Registry.load_bundled()
    assert r_bundled_only.get("princeton").name != "USER-OVERRIDE princeton"


def test_uninstall_removes_cached_file(isolated_cache):
    isolated_cache.mkdir(parents=True, exist_ok=True)
    (isolated_cache / "temporary.json").write_text(
        json.dumps({**SAMPLE_PROTOCOL, "id": "temporary"})
    )
    assert uninstall_from_cache("temporary") is True
    assert not (isolated_cache / "temporary.json").exists()


def test_uninstall_returns_false_for_missing(isolated_cache):
    assert uninstall_from_cache("never-existed") is False
