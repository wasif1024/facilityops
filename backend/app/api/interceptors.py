"""Connect interceptors applied to every RPC, outermost first:

1. ``ErrorBoundaryInterceptor`` turns unexpected exceptions into a generic ``internal``
   error. Connect would otherwise return ``str(exception)`` to the client, which can
   contain SQL parameters or request text.
2. ``AuthInterceptor`` authenticates, resolves the user's permissions, enforces the
   RPC's policy (deny by default) and exposes the principal to the handler.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from backend.app.api import auth
from backend.app.api.policy import required_permissions
from backend.app.core.config import Settings

logger = logging.getLogger(__name__)

Handler = Callable[[Any, RequestContext], Awaitable[Any]]


class ErrorBoundaryInterceptor:
    async def intercept_unary(self, call_next: Handler, request: Any, ctx: RequestContext, /) -> Any:
        try:
            return await call_next(request, ctx)
        except ConnectError:
            raise
        except Exception:
            method = ctx.method
            # Log the type and traceback server-side; never echo the message to the client.
            logger.exception("Unhandled error in %s/%s", method.service_name, method.name)
            raise ConnectError(Code.INTERNAL, "Internal error") from None


class AuthInterceptor:
    def __init__(
        self,
        settings: Settings,
        resolve_principal: Callable[[auth.Identity], Awaitable[auth.Principal]],
    ) -> None:
        self._settings = settings
        self._resolve_principal = resolve_principal

    async def intercept_unary(self, call_next: Handler, request: Any, ctx: RequestContext, /) -> Any:
        method = ctx.method
        required = required_permissions(method.service_name, method.name)
        if required is None:
            raise ConnectError(Code.PERMISSION_DENIED, "No access policy for this RPC")

        identity = await auth.authenticate(ctx.request_headers, self._settings)
        principal = await self._resolve_principal(identity)
        if not required.issubset(principal.permissions):
            raise ConnectError(Code.PERMISSION_DENIED, "Insufficient permissions")

        token = auth._current_principal.set(principal)
        try:
            return await call_next(request, ctx)
        finally:
            auth._current_principal.reset(token)
