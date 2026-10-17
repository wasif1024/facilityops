"""Operator commands. Run from the repository root:

    python -m backend.app.cli grant-role --external-id <entra-object-id> --role admin
    python -m backend.app.cli grant-role --provider development --external-id local-developer --role admin
    python -m backend.app.cli list-users
    python -m backend.app.cli remove-demo-accounts [--force]
    python -m backend.app.cli add-demo-accounts
    python -m backend.app.cli purge-expired
    python -m backend.app.cli seed-world

``grant-role`` is how the first administrator is created; after that, use the admin API.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from backend.app.core.config import get_settings
from backend.app.db.session import SessionLocal
from backend.app.domain.rbac import IdentityProvider
from backend.app.services.demo_accounts import add_demo_accounts, remove_demo_accounts
from backend.app.services.retention import purge_expired
from backend.app.services.world import load_world
from backend.app.services.users import UserDirectory, UserDirectoryError


async def _grant_role(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        try:
            access = await UserDirectory(session).grant_role(
                provider=IdentityProvider(args.provider),
                external_id=args.external_id,
                role=args.role,
                display_name=args.display_name,
            )
        except UserDirectoryError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    print(f"{access.user.id}  {args.provider}:{args.external_id}  roles={sorted(access.roles)}")
    return 0


async def _list_users(_: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        users, total = await UserDirectory(session).list_users(limit=100)
    for access in users:
        user = access.user
        print(
            f"{user.id}  {user.identity_provider}:{user.external_id}  "
            f"{user.status}  roles={sorted(access.roles)}"
        )
    print(f"{total} user(s)")
    return 0


async def _remove_demo(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        try:
            removed = await remove_demo_accounts(session, force=args.force)
        except UserDirectoryError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    print(f"removed demo accounts: {', '.join(removed)}" if removed else "no demo accounts to remove")
    return 0


async def _add_demo(_: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        added = await add_demo_accounts(session)
    print("demo accounts ready: " + ", ".join(added))
    return 0


async def _purge(_: argparse.Namespace) -> int:
    settings = get_settings()
    async with SessionLocal() as session:
        report = await purge_expired(session, settings)
    print(
        f"retention: model content {settings.model_content_retention_days} days, "
        f"audit {settings.audit_retention_days} days"
    )
    for table, count in report.deleted.items():
        print(f"  {table}: {count} row(s) deleted")
    return 0


async def _seed_world(_: argparse.Namespace) -> int:
    if get_settings().app_env == "production":
        print("error: the synthetic world is never loaded in production", file=sys.stderr)
        return 1
    async with SessionLocal() as session:
        counts = await load_world(session)
    print("synthetic world loaded: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    grant = commands.add_parser("grant-role", help="Create the user if needed and add a role")
    grant.add_argument(
        "--provider",
        choices=[provider.value for provider in IdentityProvider],
        default=IdentityProvider.ENTRA.value,
    )
    grant.add_argument("--external-id", required=True, help="Entra object id (oid) or dev user")
    grant.add_argument("--role", required=True)
    grant.add_argument("--display-name")
    grant.set_defaults(handler=_grant_role)

    listing = commands.add_parser("list-users", help="List users and their roles")
    listing.set_defaults(handler=_list_users)

    remove = commands.add_parser(
        "remove-demo-accounts", help="Delete the demo accounts (alice, fm, tech, local-developer)"
    )
    remove.add_argument(
        "--force", action="store_true", help="Remove even if no other administrator remains"
    )
    remove.set_defaults(handler=_remove_demo)

    add = commands.add_parser("add-demo-accounts", help="Create the demo accounts again")
    add.set_defaults(handler=_add_demo)

    purge = commands.add_parser(
        "purge-expired", help="Delete model content and audit records past their retention period"
    )
    purge.set_defaults(handler=_purge)

    world = commands.add_parser(
        "seed-world", help="Load the synthetic buildings, assets, history, parts, vendors and technicians"
    )
    world.set_defaults(handler=_seed_world)

    args = parser.parse_args(argv)
    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
