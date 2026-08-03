"""Chat command grammar: !name, or !name arg1 arg2 (bare space-separated
args, quotes optional) -- players naturally type this form
("!follow yayaeue"), not mindcraft's parenthesized !name("arg1", 2) form.

Used to also accept the parenthesized form (mirroring mindcraft's
regex-based parser, src/agent/commands/index.js) alongside the bare one,
but a chat-typing human never uses it and it only added grammar surface
to maintain -- dropped in favor of bare-only. Quoted tokens ("multi word")
are still recognized within the bare form, for a single arg that needs to
contain spaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMMAND_RE = re.compile(r"!(\w+)")

# A single bare arg token: a quoted string, a number, true/false, or a
# plain word -- deliberately excludes "!" so a run of bare args stops at
# the next command instead of swallowing it.
_BARE_ARG_RE = re.compile(r'"[^"]*"|-?\d+(?:\.\d+)?|true|false|[^\s!]+')


@dataclass
class ParsedCommand:
    name: str
    args: list[str | float | bool]


def _coerce(token: str) -> str | float | bool:
    if token == "true":
        return True
    if token == "false":
        return False
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    try:
        return float(token)
    except ValueError:
        return token  # bare word, e.g. a player name -- pass through as a string


def parse_command(message: str) -> ParsedCommand | None:
    match = _COMMAND_RE.search(message)
    if not match:
        return None

    name = match.group(1)

    # Bare space-separated args right after the command name. Consumes
    # every simple token up to the next "!" or end of message, so free-form
    # chat tacked onto the end ("!follow yayaeue please come here") is
    # captured as extra args too, not silently dropped -- a handler that
    # doesn't expect them will just error on arg count (ActionRegistry
    # truncates to the handler's declared params), which is more honest
    # than silently ignoring the player's actual input.
    rest = message[match.end() :]
    bare_match = re.match(r"\s+(.*)", rest)
    if not bare_match:
        return ParsedCommand(name=name, args=[])

    remainder = bare_match.group(1)
    stop = remainder.find("!")
    if stop != -1:
        remainder = remainder[:stop]
    args = [_coerce(tok) for tok in _BARE_ARG_RE.findall(remainder)]
    return ParsedCommand(name=name, args=args)
