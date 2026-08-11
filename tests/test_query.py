"""Unit tests for QueryResultTracker -- the fast-pathed reply tracker for
`{"type": "query", ...}` requests (see minebot/bridge/query.py's own
docstring). Real send/round-trip behavior (actions.query/assert_state) is
exercised in tests/test_testing_actions.py against a fake bridge instead.
"""

from __future__ import annotations

import asyncio

import pytest

from minebot.bridge.client import ModEvent
from minebot.bridge.query import QueryResultTracker


@pytest.mark.asyncio
async def test_wait_for_next_resolves_on_matching_event():
    tracker = QueryResultTracker()
    wait_task = asyncio.ensure_future(tracker.wait_for_next(timeout=1.0))
    await asyncio.sleep(0)  # let wait_for_next register its own pending future first

    tracker.handle_event(ModEvent(type="query_result", data={"arg": "legs", "result": "IDLE"}))

    event = await wait_task
    assert event.data == {"arg": "legs", "result": "IDLE"}


@pytest.mark.asyncio
async def test_handle_event_ignores_other_event_types():
    tracker = QueryResultTracker()
    wait_task = asyncio.ensure_future(tracker.wait_for_next(timeout=0.2))
    await asyncio.sleep(0)

    tracker.handle_event(ModEvent(type="position", data={"x": 0.0}))

    with pytest.raises(asyncio.TimeoutError):
        await wait_task


@pytest.mark.asyncio
async def test_wait_for_next_times_out_with_no_event():
    tracker = QueryResultTracker()

    with pytest.raises(asyncio.TimeoutError):
        await tracker.wait_for_next(timeout=0.1)
