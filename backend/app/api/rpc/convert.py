"""Small helpers shared by the RPC handlers: ids, paging, timestamps and enum mapping."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from typing import TypeVar
from uuid import UUID

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from protobuf.wkt import Timestamp

E = TypeVar("E", bound=Enum)


def text(value: object | None) -> str:
    """Proto3 strings have no null; absent values become ""."""
    return "" if value is None else str(value)


def timestamp(value: datetime | None) -> Timestamp | None:
    return Timestamp.from_datetime(value) if value is not None else None


def parse_uuid(value: str, field: str, *, required: bool = True) -> UUID | None:
    if not value:
        if required:
            raise ConnectError(Code.INVALID_ARGUMENT, f"{field} is required")
        return None
    try:
        return UUID(value)
    except ValueError as error:
        raise ConnectError(Code.INVALID_ARGUMENT, f"{field} must be a UUID") from error


def page(limit: int, offset: int, *, default: int, maximum: int) -> tuple[int, int]:
    if limit == 0:
        limit = default
    if not 1 <= limit <= maximum:
        raise ConnectError(Code.INVALID_ARGUMENT, f"limit must be between 1 and {maximum}")
    if offset < 0:
        raise ConnectError(Code.INVALID_ARGUMENT, "offset must not be negative")
    return limit, offset


def window_days(days: int) -> int:
    if days == 0:
        return 7
    if not 1 <= days <= 90:
        raise ConnectError(Code.INVALID_ARGUMENT, "days must be between 1 and 90")
    return days


def to_proto_enum(proto_enum: type[E], value: str | None) -> E:
    """Domain value "human_review" -> proto member HUMAN_REVIEW; None -> UNSPECIFIED."""
    if not value:
        return proto_enum["UNSPECIFIED"]
    return proto_enum[value.upper()]


def from_proto_enum(member: Enum) -> str | None:
    """Proto member HUMAN_REVIEW -> "human_review"; UNSPECIFIED -> None."""
    return None if member.name == "UNSPECIFIED" else member.name.lower()


def viewer():
    """The caller as the operations service sees them: an id and permissions."""
    from backend.app.api.auth import current_principal
    from backend.app.services.operations import Viewer

    principal = current_principal()
    return Viewer(user_id=principal.user_id, permissions=principal.permissions)


@contextmanager
def operation_errors() -> Iterator[None]:
    """Map operations-service errors to Connect codes. Their messages are safe to show."""
    from backend.app.services import operations as ops

    try:
        yield
    except ops.NotFound as error:
        raise ConnectError(Code.NOT_FOUND, str(error)) from error
    except ops.Forbidden as error:
        raise ConnectError(Code.PERMISSION_DENIED, str(error)) from error
    except ops.InvalidTransition as error:
        raise ConnectError(Code.FAILED_PRECONDITION, str(error)) from error
    except ops.OperationError as error:
        raise ConnectError(Code.UNAVAILABLE, str(error)) from error
