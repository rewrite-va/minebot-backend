"""ActionRegistry: the single source of truth for everything the bot can
do, dispatched two ways -- dispatch_chat for the !name arg1 arg2 chat
grammar (a human typing in chat), dispatch_tool_call for an LLM's
structured tool-call arguments. Both funnel through the same registered
Action, so a capability only needs to be written once to be usable from
either caller. Replaces the old commands/registry.py (CommandRegistry),
which only supported the chat path and had no way for a handler to report
its outcome except by reaching for the chat bridge itself.
"""

from __future__ import annotations

import logging

from minebot.actions.parser import ParsedCommand, parse_command
from minebot.actions.types import Action, ActionResult

log = logging.getLogger("minebot.actions")


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, action: Action) -> None:
        self._actions[action.name] = action

    def get(self, name: str) -> Action | None:
        return self._actions.get(name)

    def list_actions(self) -> list[Action]:
        return list(self._actions.values())

    async def dispatch_chat(self, message: str, sender: str | None) -> ActionResult | None:
        """Parses `message` for a !command and runs it if recognized.
        Returns None (not a "no reply" ActionResult) if no command was
        recognized at all, so callers can distinguish "not a command" from
        "a command ran and had nothing to say" -- matching
        CommandRegistry.dispatch's old True/False return, but carrying the
        actual result through instead of requiring the handler to send its
        own reply.
        """
        parsed: ParsedCommand | None = parse_command(message)
        if parsed is None:
            return None

        action = self._actions.get(parsed.name)
        if action is None:
            return None

        # The bare space-separated chat grammar has no natural stopping
        # point (see actions/parser.py), so "!follow asd awdawd" happily
        # parses two args even though follow only accepts one -- found
        # live: that crashed with a TypeError (too many positional args),
        # caught below only as a generic "something went wrong". Extra
        # trailing words beyond what the action declares are silently
        # dropped instead -- a human typing extra words after a command
        # almost certainly didn't mean them as a second argument.
        args = parsed.args[: len(action.params)]

        try:
            return await action.handler(sender, *args)
        except Exception:
            log.exception("action handler for !%s raised", parsed.name)
            return ActionResult(message=f"something went wrong running !{parsed.name}")

    async def dispatch_tool_call(self, name: str, sender: str | None, **kwargs) -> ActionResult:
        """LLM-facing: structured kwargs instead of positional chat args,
        and always returns a result (never None) since the caller already
        knows `name` is a real tool it chose to call -- "not a command"
        isn't a possible outcome here the way it is for chat text that
        might not contain a command at all.
        """
        action = self._actions.get(name)
        if action is None:
            return ActionResult(message=f"no such action: {name}")

        try:
            return await action.handler(sender, **kwargs)
        except Exception:
            log.exception("action handler for tool call %r raised", name)
            return ActionResult(message=f"something went wrong running {name}")
