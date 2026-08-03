"""Decides whether a chat message is worth an LLM call -- deliberately
narrow for now: only messages that mention the bot's own name, or one of
a configurable list of trigger words/phrases (MINEBOT_TRIGGER_WORDS,
e.g. a nickname, or a catch-all like "hey bot"), trigger it. Same idea as
how a human player only reliably notices being addressed by name (or a
name they answer to) in a busy chat. Everything else (ordinary chat
between other players) is ignored, so the bot doesn't burn an LLM call on
every line of unrelated conversation.

A real "whisper/direct message to the bot" signal would be an even
stronger trigger than a name/word mention, but the control channel
doesn't carry that distinction yet -- minebot-mod's CHAT/GAME forwarding
(MinebotMod.java) collapses every message into the same {"type":"chat"}
shape with no message-type flag. Worth adding on the mod side if this
trigger proves too broad/narrow in practice (see FINDINGS.md).
"""

from __future__ import annotations

from collections.abc import Sequence


def should_trigger_llm(text: str, sender: str | None, bot_name: str, trigger_words: Sequence[str] = ()) -> bool:
    if sender is None:
        return False  # system/game messages, not something to reply to

    text_lower = text.lower()
    if bot_name.lower() in text_lower:
        return True
    return any(word.lower() in text_lower for word in trigger_words if word)
