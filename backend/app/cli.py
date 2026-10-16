"""Operator commands. Run from the repository root:

    python -m backend.app.cli grant-role --external-id <entra-object-id> --role admin
    python -m backend.app.cli grant-role --provider development --external-id local-developer --role admin
    python -m backend.app.cli list-users

``grant-role`` is how the first administrator is created; after that, use the admin API.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from backend.app.db.session import SessionLocal
from backend.app.domain.rbac import IdentityProvider
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

    args = parser.parse_args(argv)
    return asyncio.run(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
