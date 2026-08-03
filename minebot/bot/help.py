"""!help -- lists every registered command, or describes one specific
command by name. Pure introspection over ActionRegistry.list_actions(),
no mod-side work needed: the same Action metadata this reads is also what
the (still-unbuilt) LLM tool-schema adapter will eventually consume, so
this is effectively a preview of that data in chat-readable form.
"""

from __future__ import annotations

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult


def _usage(action: Action) -> str:
    if not action.params:
        return f"!{action.name}"
    parts = [param.name if param.required else f"[{param.name}]" for param in action.params]
    return f"!{action.name} " + " ".join(parts)


def _describe(action: Action) -> str:
    line = f"{_usage(action)} -- {action.description}" if action.description else _usage(action)
    if action.params:
        param_bits = ", ".join(_describe_param(p) for p in action.params)
        line += f" ({param_bits})"
    return line


def _describe_param(param: ActionParam) -> str:
    suffix = "" if param.required else ", optional"
    return f"{param.name}: {param.description}{suffix}" if param.description else f"{param.name}{suffix}"


def make_help_action(registry: ActionRegistry) -> Action:
    async def handler(sender: str | None, command: str | None = None) -> ActionResult:
        if command is None:
            names = sorted(action.name for action in registry.list_actions())
            return ActionResult(message="available commands: " + ", ".join(f"!{name}" for name in names))

        action = registry.get(command.lstrip("!"))
        if action is None:
            return ActionResult(message=f"no such command: !{command}")
        return ActionResult(message=_describe(action))

    return Action(
        name="help",
        description="List all available commands, or describe one specific command.",
        handler=handler,
        params=[
            ActionParam("command", "string", "Name of a specific command to describe.", required=False),
        ],
    )


def register_help_action(registry: ActionRegistry) -> None:
    registry.register(make_help_action(registry))
