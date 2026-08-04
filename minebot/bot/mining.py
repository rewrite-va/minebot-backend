"""Mining/digging actions -- !dig and !collect, mirroring movement.py's
shape: chat-command-to-command translation only, all the real work (block
breaking, entity killing, dig-through-obstacles pathfinding) happens inside
the mod. See PENDING.md's "Mining / digging" section for the gap analysis
this came out of, and FINDINGS.md for the wire protocol/architecture this
follows (same fire-and-forget-command-answered-by-later-event shape !find
already established).

!collect's counting loop lives entirely here now, not mod-side -- the mod's
own `collect` command is a single atomic attempt (find nearest match, walk
to it, break/kill it once, report done), with no notion of a requested
count at all. This backend decides how many times to ask and when a given
attempt actually produced something real, watching InventoryTracker's own
counts directly rather than trusting the mod's "destroyed/killed" signal
as proof of possession (that signal fires the instant a block/entity is
gone, before any drop has necessarily spawned yet, let alone been picked
up -- see MiningController.collect's own docstring for the live report
that surfaced this gap).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bridge.client import ModBridge
from minebot.bridge.inventory import InventoryTracker
from minebot.timing import log_timing, now

log = logging.getLogger("minebot.mining")

DIG_DOWN_TIMEOUT = 120.0
# How long a single !collect attempt (find + walk + break/kill one item)
# gets before giving up on it entirely -- generous (matches the mod's own
# COLLECT_TARGET_TIMEOUT_TICKS retry-and-give-up window plus real walking
# time), but bounded so a single stuck attempt can't hang the whole
# collect() loop forever.
COLLECT_ATTEMPT_TIMEOUT = 30.0
# How long to wait, after an attempt reports success (block destroyed /
# entity killed), for the corresponding item count to actually rise in
# InventoryTracker before concluding this attempt produced no drop at
# all (real vanilla randomness -- a cow can drop 0 leather, an enderman
# doesn't always drop a pearl) rather than "the drop is just still
# settling/being walked over".
DROP_CONFIRMATION_TIMEOUT = 5.0
# How many consecutive no-drop attempts (block/entity destroyed, but no
# expected item ever showed up) !collect tolerates before giving up
# entirely, rather than retrying the same query forever against
# genuinely unlucky RNG.
MAX_CONSECUTIVE_EMPTY_DROPS = 10


class MiningController:
    def __init__(self, bridge: ModBridge, inventory: InventoryTracker) -> None:
        self.bridge = bridge
        self.inventory = inventory
        # Same single-slot pending-future pattern movement.py's
        # MovementController uses for _pending_find: at most one !dig
        # or !collect attempt is ever in flight at a time (chat commands
        # are dispatched one at a time, and collect()'s own loop sends
        # its next single-item attempt only after the previous one
        # resolves), so one slot per event type is enough.
        self._pending_dig_down: asyncio.Future[dict] | None = None
        self._pending_collect_result: asyncio.Future[dict] | None = None
        # query/query_result (DropTable's drops_from/source_for) CAN
        # genuinely overlap in principle (nothing forces one in flight
        # at a time the way chat commands do), so this is a dict keyed
        # by the correlation id each request carries, not a single slot.
        self._pending_queries: dict[str, asyncio.Future[list[str]]] = {}

    async def dig_down(self, sender: str | None, count: float = 10) -> ActionResult:
        count = int(count)
        result = asyncio.get_event_loop().create_future()
        self._pending_dig_down = result
        log.info("digging down %d blocks", count)
        await self.bridge.send_dig_down(count)
        try:
            data = await asyncio.wait_for(result, timeout=DIG_DOWN_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("dig_down: no response from mod within %.0fs", DIG_DOWN_TIMEOUT)
            return ActionResult(message="I couldn't dig down (no response)")
        finally:
            self._pending_dig_down = None

        broken = data.get("broken", 0)
        reason = data.get("reason")
        if reason:
            return ActionResult(message=f"dug down {broken}/{count} -- {reason}")
        return ActionResult(message=f"dug down {broken} block{'s' if broken != 1 else ''}")

    async def debug_break(self, sender: str | None) -> ActionResult:
        """Temporary !debug command -- fire-and-forget, no result event.
        Tests InventoryActions.moveToHotbar in isolation, no mining
        involved at all: shift-clicks the diamond pickaxe out of the
        hotbar into main storage (a real, server-synced container click),
        then calls moveToHotbar to bring it back into hotbar slot 0 --
        the exact mechanism suspected of desyncing the server's (and so
        other clients') view of the bot's held item from what it locally
        believes it's holding (see FINDINGS.md's "InventoryActions.
        moveToHotbar's local-only swap genuinely desyncing the server's
        view of the held item" section for the live investigation this
        is testing). Read minebot-mod's own log directly for the
        diagnostic output; the actual test is comparing what a human
        observer sees the bot holding afterward against that log, not
        something this command can verify on its own.
        """
        await self.bridge.send_debug_swap_test()
        return ActionResult(message="debug: running the hotbar-swap test on the diamond pickaxe -- check mod logs")

    async def collect(self, sender: str | None, query: str, count: float) -> ActionResult:
        """Collects `count` of `query`, one attempt at a time -- each
        attempt is a single mod-side `collect` command (find nearest
        match, walk to it, break/kill it once), confirmed successful only
        once InventoryTracker shows a real count increase in one of the
        items DropTable says `query` should produce, not merely trusting
        the mod's own "destroyed/killed" report (see collect_result's
        wire-format docstring on the mod side for why that signal alone
        isn't proof of possession -- reported live: !give right after a
        !collect-reported "success" said "I haven't picked up anything",
        because the block breaking and the item landing in inventory are
        two different moments, and the old design conflated them).

        `query` is resolved through DropTable's source_for first, so
        asking for a raw item name that isn't itself directly minable/
        huntable (e.g. "cobblestone", which has no cobblestone block or
        mob of its own) still works -- source_for("cobblestone") resolves
        to "stone", and the collect attempts are actually sent for that.
        """
        count = int(count)
        target = await self._query_one("source_for", query)
        # drops_from/source_for answer in bare ids (DropTable's own maps
        # are keyed/valued that way -- see its own docstring), but
        # InventoryTracker.count_of needs the full "minecraft:<id>"
        # registry id its snapshots actually use (same convention
        # InventoryController's own _normalize_item_id already
        # establishes). Found live: !collect was comparing count_of
        # against a bare "cobblestone" that could never match the real
        # "minecraft:cobblestone" keys InventoryTracker stores, so every
        # attempt's drop-confirmation was structurally guaranteed to see
        # "no gain" regardless of whether a real drop landed.
        expected_drops = [item if ":" in item else f"minecraft:{item}" for item in await self._query("drops_from", target)]
        log.info("collecting %d %r (resolved target=%r, expected drops=%s)", count, query, target, expected_drops)

        collected = 0
        consecutive_empty_drops = 0
        last_reason: str | None = None

        while collected < count:
            before_counts = {item: self.inventory.count_of(item) for item in expected_drops}
            try:
                result = await asyncio.wait_for(self._collect_one(target), timeout=COLLECT_ATTEMPT_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("collect: attempt for %r timed out after %.0fs", target, COLLECT_ATTEMPT_TIMEOUT)
                last_reason = "timed out"
                break

            if not result.get("success"):
                last_reason = result.get("reason", "failed")
                break

            gained_item = await self._wait_for_any_gain(expected_drops, before_counts)
            if gained_item is None:
                consecutive_empty_drops += 1
                log.info(
                    "collect: %r destroyed/killed but no expected drop (%s) landed within %.0fs (%d/%d empty)",
                    target, expected_drops, DROP_CONFIRMATION_TIMEOUT, consecutive_empty_drops, MAX_CONSECUTIVE_EMPTY_DROPS,
                )
                if consecutive_empty_drops >= MAX_CONSECUTIVE_EMPTY_DROPS:
                    last_reason = f"kept getting empty drops from {target}"
                    break
                continue  # retry the same target type -- doesn't count toward collected

            consecutive_empty_drops = 0
            collected += 1
            log.info("collect: confirmed %r in inventory (%d/%d %s so far)", gained_item, collected, count, query)

        if collected >= count:
            return ActionResult(message=f"collected {collected}/{count} {query}")
        if last_reason:
            return ActionResult(message=f"collected {collected}/{count} {query} -- {last_reason}")
        return ActionResult(message=f"collected {collected}/{count} {query}")

    async def _collect_one(self, target: str) -> dict:
        """Sends a single one-item `collect` command and awaits its collect_result."""
        result = asyncio.get_event_loop().create_future()
        self._pending_collect_result = result
        try:
            await self.bridge.send_collect(target)
            return await result
        finally:
            self._pending_collect_result = None

    async def _wait_for_any_gain(self, expected_drops: list[str], before_counts: dict[str, int]) -> str | None:
        """Waits up to DROP_CONFIRMATION_TIMEOUT for any item in
        `expected_drops` to exceed its `before_counts` snapshot -- a
        direct count comparison, not gained_items()'s narrow 2-snapshot
        diff, so an unrelated inventory broadcast landing in between
        (e.g. a hotbar tool-switch for the *next* attempt) can never
        evict this specific confirmation the way it could evict a bare
        gained_items() check. Returns the first item found to have
        increased, or None if nothing did within the timeout.
        """
        deadline = now() + DROP_CONFIRMATION_TIMEOUT
        changed = asyncio.Event()

        def on_change() -> None:
            changed.set()

        self.inventory.add_change_listener(on_change)
        try:
            while True:
                for item in expected_drops:
                    if self.inventory.count_of(item) > before_counts.get(item, 0):
                        return item
                remaining = deadline - now()
                if remaining <= 0:
                    return None
                changed.clear()
                try:
                    await asyncio.wait_for(changed.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return None
        finally:
            self.inventory.remove_change_listener(on_change)

    async def _query_one(self, sub_type: str, argument: str) -> str:
        result = await self._query(sub_type, argument)
        return result[0] if result else argument

    async def _query(self, sub_type: str, argument: str) -> list[str]:
        key = str(uuid.uuid4())
        result = asyncio.get_event_loop().create_future()
        self._pending_queries[key] = result
        try:
            await self.bridge.send_query(sub_type, [argument], key)
            return await asyncio.wait_for(result, timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("query %s(%r) timed out -- falling back to the argument unchanged", sub_type, argument)
            return [argument]
        finally:
            self._pending_queries.pop(key, None)

    def on_dig_down_result(self, data: dict) -> None:
        """Called from the run loop for every dig_down_result event -- resolves dig_down()'s pending future, if any."""
        if self._pending_dig_down is not None and not self._pending_dig_down.done():
            self._pending_dig_down.set_result(data)

    def on_collect_result(self, data: dict) -> None:
        """Called from the run loop for every collect_result event -- resolves whichever single-item _collect_one() call is currently awaiting it, if any."""
        if self._pending_collect_result is not None and not self._pending_collect_result.done():
            self._pending_collect_result.set_result(data)

    def on_query_result(self, data: dict) -> None:
        """Called from the run loop for every query_result event -- resolves the pending query matching its correlation key, if any (queries can overlap, unlike collect/dig_down, so this is keyed rather than a single slot)."""
        key = data.get("key")
        pending = self._pending_queries.get(key)
        if pending is not None and not pending.done():
            pending.set_result(data.get("result", []))


def register_mining_actions(registry: ActionRegistry, mining: MiningController) -> None:
    registry.register(Action(
        name="dig",
        description="Dig straight down, stopping at lava/water or a big drop.",
        handler=mining.dig_down,
        params=[
            ActionParam("count", "int", "How many blocks to dig down.", required=False),
        ],
    ))
    registry.register(Action(
        name="debug",
        description="Temporary debug command: tests the hotbar-swap mechanism on the diamond pickaxe in isolation, with heavy diagnostic logging.",
        handler=mining.debug_break,
    ))
    registry.register(Action(
        name="collect",
        description="Collect the nearest N of a block or entity type (mining blocks, or killing entities for drops).",
        handler=mining.collect,
        params=[
            ActionParam("query", "string", "Block, entity, or item type, e.g. \"stone\", \"cow\", or \"cobblestone\"."),
            ActionParam("count", "int", "How many to collect."),
        ],
    ))
