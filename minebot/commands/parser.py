"""Chat command grammar: !name or !name(arg1, "arg2", 3.5).

Mirrors mindcraft's regex-based parser (src/agent/commands/index.js) but with
no LLM in the loop — commands come straight from a human typing in chat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMMAND_RE = re.compile(
    r"!(\w+)(?:\((-?\d+(?:\.\d+)?|true|false|\"[^\"]*\")"
    r"(?:\s*,\s*(-?\d+(?:\.\d+)?|true|false|\"[^\"]*\"))*\))?"
)
_ARG_RE = re.compile(r'-?\d+(?:\.\d+)?|true|false|"[^"]*"')


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
    return float(token)


def parse_command(message: str) -> ParsedCommand | None:
    match = _COMMAND_RE.search(message)
    if not match:
        return None

    name = match.group(1)
    call_text = match.group(0)
    paren_start = call_text.find("(")
    if paren_start == -1:
        return ParsedCommand(name=name, args=[])

    args_text = call_text[paren_start + 1 : -1]
    args = [_coerce(tok) for tok in _ARG_RE.findall(args_text)]
    return ParsedCommand(name=name, args=args)
