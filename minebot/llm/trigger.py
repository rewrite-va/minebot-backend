"""Decides whether a chat message is worth an LLM call -- deliberately
narrow for now: only messages that mention the bot's own name trigger it,
same as how a human player only reliably notices being addressed by name
in a busy chat. Everything else (ordinary chat between other players) is
ignored, so the bot doesn't burn an LLM call on every line of unrelated
conversation.

A real "whisper/direct message to the bot" signal would be an even
stronger trigger than a name mention, but the control channel doesn't
carry that distinction yet -- minebot-mod's CHAT/GAME forwarding
(MinebotMod.java) collapses every message into the same {"type":"chat"}
shape with no message-type flag. Worth adding on the mod side if this
trigger proves too broad/narrow in practice (see FINDINGS.md).
"""

from __future__ import annotations


def should_trigger_llm(text: str, sender: str | None, bot_name: str) -> bool:
    if sender is None:
        return False  # system/game messages, not something to reply to
    return bot_name.lower() in text.lower()
