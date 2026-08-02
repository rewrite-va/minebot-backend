"""Command name -> handler registry, mirroring mindcraft's commandMap pattern
(src/agent/commands/index.js) minus the LLM-facing description/param-schema
metadata, which existed there only to be shown to a language model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from minebot.commands.parser import ParsedCommand, parse_command


@dataclass
class Command:
    name: str
    handler: Callable[..., Awaitable[None]]


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, name: str, handler: Callable[..., Awaitable[None]]) -> None:
        self._commands[name] = Command(name, handler)

    async def dispatch(self, message: str, *context_args) -> bool:
        """Parses `message` for a !command and runs it if recognized.
        Returns True if a recognized command was found and dispatched.
        """
        parsed: ParsedCommand | None = parse_command(message)
        if parsed is None:
            return False

        command = self._commands.get(parsed.name)
        if command is None:
            return False

        await command.handler(*context_args, *parsed.args)
        return True
