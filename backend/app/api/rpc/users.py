from __future__ import annotations

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from protobuf.wkt import Struct
from pydantic import EmailStr, TypeAdapter, ValidationError

from backend.app.api.auth import current_principal
from backend.app.api.container import Container
from backend.app.api.rpc.convert import (
    from_proto_enum,
    page,
    parse_uuid,
    text,
    timestamp,
    to_proto_enum,
)
from backend.app.domain.rbac import IdentityProvider, UserStatus
from backend.app.gen.facilityops.v1 import account_pb, users_pb as pb
from backend.app.services.users import (
    UserAccess,
    UserAlreadyExists,
    UserDirectory,
    UserDirectoryError,
    UserNotFound,
)


def to_proto_user(access: UserAccess) -> pb.User:
    user = access.user
    return pb.User(
        id=str(user.id),
        identity_provider=to_proto_enum(pb.IdentityProvider, user.identity_provider),
        external_id=user.external_id,
        display_name=text(user.display_name),
        email=text(user.email),
        status=to_proto_enum(pb.UserStatus, user.status),
        roles=sorted(access.roles),
        created_at=timestamp(user.created_at),
        last_login_at=timestamp(user.last_login_at),
    )


_EMAIL = TypeAdapter(EmailStr)


def _status(member: pb.UserStatus) -> UserStatus | None:
    value = from_proto_enum(member)
    return UserStatus(value) if value else None


class AccountRpc:
    async def get_me(
        self, request: account_pb.GetMeRequest, ctx: RequestContext
    ) -> account_pb.GetMeResponse:
        principal = current_principal()
        return account_pb.GetMeResponse(
            user_id=str(principal.user_id),
            display_name=text(principal.display_name),
            roles=sorted(principal.roles),
            permissions=sorted(principal.permissions),
        )


class UserAdminRpc:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def list_users(self, request: pb.ListUsersRequest, ctx: RequestContext) -> pb.ListUsersResponse:
        limit, offset = page(request.limit, request.offset, default=50, maximum=100)
        async with self._c.session_factory() as session:
            users, total = await UserDirectory(session).list_users(
                role=request.role or None,
                status=_status(request.status),
                search=request.search[:100] or None,
                limit=limit,
                offset=offset,
            )
        return pb.ListUsersResponse(users=[to_proto_user(u) for u in users], total=total)

    async def get_user(self, request: pb.GetUserRequest, ctx: RequestContext) -> pb.GetUserResponse:
        user_id = parse_uuid(request.user_id, "user_id")
        async with self._c.session_factory() as session:
            try:
                access = await UserDirectory(session).get(user_id)
            except UserNotFound as error:
                raise ConnectError(Code.NOT_FOUND, str(error)) from error
        return pb.GetUserResponse(user=to_proto_user(access))

    async def create_user(
        self, request: pb.CreateUserRequest, ctx: RequestContext
    ) -> pb.CreateUserResponse:
        external_id = request.external_id.strip()
        if not external_id or len(external_id) > 128:
            raise ConnectError(Code.INVALID_ARGUMENT, "external_id must be 1 to 128 characters")
        if request.email:
            try:
                _EMAIL.validate_python(request.email)
            except ValidationError:
                raise ConnectError(Code.INVALID_ARGUMENT, "email is not valid") from None
        provider = from_proto_enum(request.identity_provider) or IdentityProvider.ENTRA.value
        async with self._c.session_factory() as session:
            try:
                access = await UserDirectory(session).create(
                    actor_id=current_principal().user_id,
                    provider=IdentityProvider(provider),
                    external_id=external_id,
                    display_name=request.display_name[:200] or None,
                    email=request.email or None,
                    roles=list(request.roles),
                )
            except UserAlreadyExists as error:
                raise ConnectError(Code.ALREADY_EXISTS, str(error)) from error
            except UserDirectoryError as error:
                raise ConnectError(Code.INVALID_ARGUMENT, str(error)) from error
        return pb.CreateUserResponse(user=to_proto_user(access))

    async def update_user(
        self, request: pb.UpdateUserRequest, ctx: RequestContext
    ) -> pb.UpdateUserResponse:
        user_id = parse_uuid(request.user_id, "user_id")
        display_name = request.display_name[:200] if request.has_field("display_name") else None
        async with self._c.session_factory() as session:
            try:
                access = await UserDirectory(session).update(
                    actor_id=current_principal().user_id,
                    user_id=user_id,
                    display_name=display_name,
                    status=_status(request.status),
                )
            except UserNotFound as error:
                raise ConnectError(Code.NOT_FOUND, str(error)) from error
            except UserDirectoryError as error:
                raise ConnectError(Code.FAILED_PRECONDITION, str(error)) from error
        return pb.UpdateUserResponse(user=to_proto_user(access))

    async def set_user_roles(
        self, request: pb.SetUserRolesRequest, ctx: RequestContext
    ) -> pb.SetUserRolesResponse:
        user_id = parse_uuid(request.user_id, "user_id")
        async with self._c.session_factory() as session:
            try:
                access = await UserDirectory(session).set_roles(
                    actor_id=current_principal().user_id, user_id=user_id, roles=list(request.roles)
                )
            except UserNotFound as error:
                raise ConnectError(Code.NOT_FOUND, str(error)) from error
            except UserDirectoryError as error:
                raise ConnectError(Code.FAILED_PRECONDITION, str(error)) from error
        return pb.SetUserRolesResponse(user=to_proto_user(access))

    async def list_roles(self, request: pb.ListRolesRequest, ctx: RequestContext) -> pb.ListRolesResponse:
        async with self._c.session_factory() as session:
            roles = await UserDirectory(session).roles()
        return pb.ListRolesResponse(
            roles=[
                pb.Role(
                    id=role.id,
                    display_name=role.display_name,
                    description=role.description,
                    is_system=role.is_system,
                    permissions=list(role.permissions),
                )
                for role in roles
            ]
        )

    async def list_audit_events(
        self, request: pb.ListAuditEventsRequest, ctx: RequestContext
    ) -> pb.ListAuditEventsResponse:
        limit, offset = page(request.limit, request.offset, default=50, maximum=100)
        async with self._c.session_factory() as session:
            events, total = await UserDirectory(session).audit_events(
                action=request.action[:64] or None, limit=limit, offset=offset
            )
        return pb.ListAuditEventsResponse(
            events=[
                pb.AuditEvent(
                    id=str(event.id),
                    actor_user_id=text(event.actor_user_id),
                    action=event.action,
                    target_type=event.target_type,
                    target_id=text(event.target_id),
                    details=Struct.from_python(event.details or {}),
                    created_at=timestamp(event.created_at),
                )
                for event in events
            ],
            total=total,
        )
