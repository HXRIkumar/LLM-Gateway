"""Admin CLI.

Currently exposes ``conduit keys create`` to mint the first gateway key. The
plaintext is printed to stdout exactly once (like generating any secret); it is
never persisted or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from conduit.config import Settings
from conduit.infra.db.engine import create_db_engine
from conduit.infra.db.session import create_sessionmaker
from conduit.services.keys import KeyService


async def _create_key() -> int:
    settings = Settings()
    engine = create_db_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            service = KeyService(session)
            org = await service.get_or_create_default_org()
            issued = await service.issue(org.id)
    finally:
        await engine.dispose()

    print(f"Created API key (org {issued.org_id}).")
    print("Store it now — it will not be shown again:\n")
    print(f"  {issued.plaintext}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="conduit", description="Conduit admin CLI.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    keys = subcommands.add_parser("keys", help="Manage API keys.")
    keys_sub = keys.add_subparsers(dest="keys_command", required=True)
    keys_sub.add_parser("create", help="Mint a new API key.")

    args = parser.parse_args(argv)

    if args.command == "keys" and args.keys_command == "create":
        return asyncio.run(_create_key())

    parser.error("unknown command")  # pragma: no cover - argparse handles this
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
