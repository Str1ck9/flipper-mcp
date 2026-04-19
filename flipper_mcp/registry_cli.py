"""``flipper-registry`` — command-line manager for the protocol registry.

Complements the MCP tools with a shell-usable surface for humans:

    flipper-registry status
    flipper-registry list [--category subghz] [--pack garage]
    flipper-registry index <URL>
    flipper-registry install <URL> <protocol-id>
    flipper-registry remove <protocol-id>
    flipper-registry describe <protocol-id>
    flipper-registry validate <file.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .registry import (
    Protocol,
    Registry,
    RegistryError,
    bundled_protocols,
    fetch_index,
    install_from_entry,
    installed_protocols,
    uninstall_from_cache,
    user_cache_dir,
)


# -- helpers ----------------------------------------------------------------


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=str))


# -- subcommand handlers ----------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    _print_json(
        {
            "bundled_count": len(bundled_protocols()),
            "bundled": bundled_protocols(),
            "user_installed_count": len(installed_protocols()),
            "user_installed": installed_protocols(),
            "cache_dir": str(user_cache_dir()),
        }
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    registry = Registry.load()
    protocols = registry.list(category=args.category, pack=args.pack)
    _print_json(
        [
            {
                "id": p.id,
                "name": p.name,
                "category": p.category,
                "typical_frequencies_hz": p.typical_frequencies_hz,
                "packs": p.packs,
            }
            for p in protocols
        ]
    )
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    registry = Registry.load()
    proto = registry.get(args.protocol_id)
    if proto is None:
        print(f"error: unknown protocol '{args.protocol_id}'", file=sys.stderr)
        return 1
    _print_json(proto.model_dump())
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    idx = fetch_index(args.url)
    have_bundled = set(bundled_protocols())
    have_installed = set(installed_protocols())
    _print_json(
        {
            "index_url": args.url,
            "name": idx.name,
            "schema_version": idx.schema_version,
            "description": idx.description,
            "protocol_count": len(idx.protocols),
            "protocols": [
                {
                    "id": e.id,
                    "name": e.name,
                    "category": e.category,
                    "packs": e.packs,
                    "bundled": e.id in have_bundled,
                    "installed": e.id in have_installed,
                }
                for e in idx.protocols
            ],
        }
    )
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    idx = fetch_index(args.url)
    entry = next((e for e in idx.protocols if e.id == args.protocol_id), None)
    if entry is None:
        print(
            f"error: '{args.protocol_id}' not found in index {args.url}",
            file=sys.stderr,
        )
        return 1
    path = install_from_entry(entry)
    print(f"installed {entry.id} -> {path}")
    if entry.sha256:
        print(f"  sha256 verified: {entry.sha256[:16]}...")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    if uninstall_from_cache(args.protocol_id):
        print(f"removed {args.protocol_id}")
        return 0
    print(
        f"error: '{args.protocol_id}' not in user cache "
        "(bundled protocols cannot be removed)",
        file=sys.stderr,
    )
    return 1


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        Protocol(**data)
    except json.JSONDecodeError as e:
        print(f"invalid JSON: {e}", file=sys.stderr)
        return 1
    except ValidationError as e:
        print(f"schema error:\n{e}", file=sys.stderr)
        return 1
    print(f"{path.name}: OK")
    return 0


# -- entry point -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flipper-registry",
        description="Manage the flipper-mcp protocol registry cache.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show bundled vs installed protocols")

    p_list = sub.add_parser("list", help="List all loaded protocols")
    p_list.add_argument("--category", help="subghz | ir | nfc | lfrfid | ble")
    p_list.add_argument("--pack", help="e.g. garage, access-control")

    p_describe = sub.add_parser("describe", help="Show full entry for one protocol")
    p_describe.add_argument("protocol_id")

    p_index = sub.add_parser(
        "index", help="Fetch a remote registry index and list its entries"
    )
    p_index.add_argument("url")

    p_install = sub.add_parser(
        "install", help="Download and install a protocol from a remote index"
    )
    p_install.add_argument("url")
    p_install.add_argument("protocol_id")

    p_remove = sub.add_parser(
        "remove", help="Remove a user-installed protocol from the cache"
    )
    p_remove.add_argument("protocol_id")

    p_validate = sub.add_parser(
        "validate",
        help="Validate a local JSON file against the Protocol schema",
    )
    p_validate.add_argument("file")

    args = parser.parse_args(argv)
    handler = {
        "status": cmd_status,
        "list": cmd_list,
        "describe": cmd_describe,
        "index": cmd_index,
        "install": cmd_install,
        "remove": cmd_remove,
        "validate": cmd_validate,
    }[args.command]

    try:
        return handler(args)
    except RegistryError as e:
        print(f"registry error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
