"""Core vocabulary for the action layer: a single declarative description
of "a thing the bot can do", usable both as a chat command (!name(args))
and as an LLM tool call -- one Action definition, two callers, instead of
the LLM path needing its own separate copy of every capability.

Modeled on mindcraft's actionsList/queryList (src/agent/commands/
actions.js) -- name/description/params as data, `perform` as the handler
-- adapted to Python's type system and this project's existing async
handler convention (every handler's first argument is `sender`, matching
CommandRegistry.dispatch's contract, so migrating an existing handler into
an Action doesn't change its signature).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

ParamType = Literal["string", "int", "float", "bool"]


@dataclass(frozen=True)
class ActionParam:
    name: str
    type: ParamType
    description: str
    required: bool = True


@dataclass(frozen=True)
class ActionResult:
    """What running an action produced. `message` is the bot's own chat
    reply if it has one to make ("I don't have any bread") -- None means
    the action succeeded (or failed) silently, with nothing worth saying.

    Deliberately not a success/failure struct: callers (chat dispatch, an
    LLM tool loop) don't need a separate boolean to branch on, they just
    need to know whether there's something to say and, for the LLM case,
    what happened in plain language it can reason over -- `message` alone
    covers both "here's your reply" and "here's what went wrong" without
    forcing every handler to invent an error code scheme.
    """

    message: str | None = None


ActionHandler = Callable[..., Awaitable[ActionResult]]


@dataclass(frozen=True)
class Action:
    name: str
    description: str
    handler: ActionHandler
    params: list[ActionParam] = field(default_factory=list)
