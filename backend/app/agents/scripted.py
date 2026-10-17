"""A scripted chat client: the incident team without a model (scenario harness, tests).

It plays back a list of replies (text, JSON, or a tool call) and records what it was sent. Tool
calls are executed for real by Agent Framework's function-invocation layer, so a scripted run
exercises the actual tools, budgets, Magentic orchestration, middleware and decision trail; only
the model's words are fixed. Costs nothing and is deterministic, so it runs on every pull request.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
)

_USAGE = {"input_token_count": 200, "output_token_count": 50, "total_token_count": 250}


class ScriptedChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Replies, in order: a str or dict (sent as text/JSON) or {"call": "<tool>", "args": {...}}."""

    def __init__(self, replies: Sequence[Any], *, usage: dict[str, int] | None = _USAGE) -> None:
        super().__init__()
        self._replies = list(replies)
        # None: responses carry no usage at all (tests the budget's fallback).
        self._usage = usage
        self.sent: list[str] = []

    @property
    def remaining(self) -> int:
        return len(self._replies)

    def _inner_get_response(self, *, messages: Sequence[Message], stream: bool, options: Any, **kwargs: Any):
        response = self._next(messages)
        if not stream:

            async def immediate() -> ChatResponse:
                return response

            return immediate()

        async def updates():
            for update in response.to_updates():
                yield update

        return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

    def _next(self, messages: Sequence[Message]) -> ChatResponse:
        self.sent.append(messages[-1].text or "" if messages else "")
        if not self._replies:
            raise RuntimeError("The scripted model ran out of replies")
        reply = self._replies.pop(0)
        if isinstance(reply, dict) and "call" in reply:
            content = Content.from_function_call(
                call_id=f"call-{len(self.sent)}", name=reply["call"], arguments=reply.get("args", {})
            )
        else:
            content = Content.from_text(reply if isinstance(reply, str) else json.dumps(reply))
        usage = dict(self._usage) if self._usage is not None else None
        return ChatResponse(messages=[Message("assistant", [content])], usage_details=usage)


def progress(next_speaker: str, instruction: str, *, done: bool = False, stuck: bool = False) -> dict[str, Any]:
    """A progress-ledger reply in the framework's schema, for scripting the manager."""
    return {
        "is_request_satisfied": {"reason": "scripted", "answer": done},
        "is_in_loop": {"reason": "scripted", "answer": stuck},
        "is_progress_being_made": {"reason": "scripted", "answer": not stuck},
        "next_speaker": {"reason": "scripted", "answer": next_speaker},
        "instruction_or_question": {"reason": "scripted", "answer": instruction},
    }
