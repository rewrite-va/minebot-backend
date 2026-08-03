"""Chat command grammar: !name, !name(arg1, "arg2", 3.5), or !name arg1 arg2
(bare space-separated args, no parens/quotes needed) -- players naturally
type the third form ("!follow yayaeue"), and it used to silently parse as
zero-arg !follow, discarding the name entirely (found live: !follow
yayaeue followed the chat sender instead, since the parenthesized-only
grammar just never saw "yayaeue" as an argument at all).

The parenthesized form mirrors mindcraft's regex-based parser
(src/agent/commands/index.js); the bare form is this project's own
addition. Moved here (from the old commands/ package) since this is now
specifically ActionRegistry.dispatch_chat's grammar, not a
general-purpose command framework of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMMAND_RE = re.compile(
    r"!(\w+)(?:\((-?\d+(?:\.\d+)?|true|false|\"[^\"]*\")"
    r"(?:\s*,\s*(-?\d+(?:\.\d+)?|true|false|\"[^\"]*\"))*\))?"
)
_ARG_RE = re.compile(r'-?\d+(?:\.\d+)?|true|false|"[^"]*"')

# A single bare arg token for the space-separated form: a quoted string, a
# number, true/false, or a plain word -- deliberately excludes "!" so a
# run of bare args stops at the next command instead of swallowing it.
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
    call_text = match.group(0)
    paren_start = call_text.find("(")
    if paren_start != -1:
        args_text = call_text[paren_start + 1 : -1]
        args = [_coerce(tok) for tok in _ARG_RE.findall(args_text)]
        return ParsedCommand(name=name, args=args)

    # No parens -- look for bare space-separated args right after the
    # command name (e.g. "!follow yayaeue"). Consumes every simple token
    # up to the next "!" or end of message, so free-form chat tacked onto
    # the end ("!follow yayaeue please come here") is captured as extra
    # args too, not silently dropped -- a handler that doesn't expect
    # them will just error on arg count, which is more honest than
    # silently ignoring the player's actual input the way the old
    # parens-only grammar did.
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
