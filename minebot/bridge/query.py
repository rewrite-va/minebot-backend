"""Tracks the mod's `query_result` events -- the reply to a `{"type":
"query", "arg": ...}` request (see minebot-mod's MinebotMod.handleQuery).
Read-only introspection: lets Python ask "what is the mod's own live state
right now" (which PlayerIntention/Legs/Hands/Head state is currently
active) instead of only ever inferring it indirectly from broadcast
events -- built specifically so the in-game test harness can assert
against real final state machine state (see minebot/testing/actions.py's
own assert_state), not just position/timing-based proxies for it.

Fast-pathed the same way SelfPositionTracker/EntityTracker are (see
run_loop.py's own _read_events) rather than routed through the general
event queue -- a query is a request/response exchange a caller is
actively awaiting the answer to, so it must never sit behind whatever
chat command happens to be running.
"""

from __future__ import annotations

import asyncio

from minebot.bridge.client import ModEvent


class QueryResultTracker:
    def __init__(self) -> None:
        # Replaced (not appended to) on every query_result event, then
        # immediately handed to whichever pending `wait_for_next` call(s)
        # are currently waiting -- a query is always "ask, then wait for
        # the VERY NEXT reply", never "read whatever the last reply
        # happened to be" (which could be stale, from an earlier
        # unrelated query if the caller forgot to await its own).
        self._next_result: asyncio.Future[ModEvent] | None = None

    def handle_event(self, event: ModEvent) -> None:
        if event.type != "query_result":
            return
        if self._next_result is not None and not self._next_result.done():
            self._next_result.set_result(event)

    async def wait_for_next(self, timeout: float) -> ModEvent:
        """Waits for the next `query_result` event to arrive and returns
        it -- callers should `send_query` first, then immediately await
        this (see actions.query's own docstring for the full round trip).
        Raises `asyncio.TimeoutError` if none arrives within `timeout`,
        matching every other self-timing-out primitive in this repo's own
        testing helpers (see minebot/testing/actions.py's own docstring).
        """
        self._next_result = asyncio.get_event_loop().create_future()
        return await asyncio.wait_for(self._next_result, timeout=timeout)
