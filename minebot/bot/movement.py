"""Movement actions -- follow/stop, driven by minebot-mod's goal-based
control channel instead of computing movement ourselves. All the real work
(yaw-toward-target, forward/jump timing, actual physics) happens inside
the mod, which runs a real Minecraft client; this class is just the
goal-translation layer, usable both as a chat command and an LLM tool call
via ActionRegistry.

Every handler takes `sender` as its first argument, matching
ActionRegistry's dispatch contract -- !follow uses `sender` as the default
target so "!follow" with no argument follows whoever typed it; an explicit
"!follow name" resolves the name through EntityTracker's name->id
mapping (fed by the mod's own entity events) instead.
"""

from __future__ import annotations

import asyncio
import logging

from minebot.actions.registry import ActionRegistry
from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.bridge.client import ModBridge
from minebot.bridge.entities import EntityTracker
from minebot.bridge.self_position import SelfPositionTracker
from minebot.places import PlaceMemory
from minebot.timing import log_timing, now

log = logging.getLogger("minebot.movement")

FOLLOW_STOP_DISTANCE = 2.0
# !goto is expected to land at the exact requested coordinates, not just
# "somewhere nearby" -- 0.2 is the practical floor (tight enough to look
# and feel exact, loose enough that collision/floating-point noise near
# the target doesn't leave the bot jittering forever trying to close the
# last few hundredths of a block). Also used as GoalNear's own range on
# the mod side (see PathTracker.maybeReplan), so the planned path itself
# targets the exact block, not just its neighborhood.
GOTO_STOP_DISTANCE = 0.2
FIND_RESULT_TIMEOUT = 10.0
FIND_ARRIVAL_TIMEOUT = 60.0


class MovementController:
    def __init__(
        self, bridge: ModBridge, tracker: EntityTracker, places: PlaceMemory, self_position: SelfPositionTracker,
    ) -> None:
        self.bridge = bridge
        self.tracker = tracker
        self.places = places
        self.self_position = self_position
        # The mod's FOLLOW goal is pinned to a fixed entity ID, but a
        # player who disconnects and reconnects gets a brand new one --
        # the old id then never resolves again and the bot just stands
        # idle forever with no error (found live: !follow silently
        # stopped working the moment the followed player rejoined).
        # Tracking the *name* we're supposed to be following (as opposed
        # to just fire-and-forgetting the id to the mod) lets
        # on_entity_added re-issue `follow` with the fresh id the moment
        # that name reappears.
        self._following_name: str | None = None
        # !find is a fire-and-forget mod command answered by a later,
        # separately-delivered find_result event (no request-id
        # correlation exists in the wire protocol) -- this future is how
        # find() suspends until run_loop.py's on_find_result callback
        # delivers that answer. Only one !find is ever in flight at a
        # time (chat commands are dispatched one at a time), so a single
        # slot is enough; asserted by find() itself rather than queued.
        self._pending_find: asyncio.Future[dict] | None = None
        # Same shape as _pending_find, for the mod's `arrived` event --
        # only set while find() is actively walking to a just-found
        # target, so it can send a distinct "here is the X" message on
        # actual arrival instead of only ever confirming "going there".
        self._pending_arrival: asyncio.Future[None] | None = None

    async def follow(self, sender: str | None, player_name: str | None = None) -> ActionResult:
        target_name = player_name if player_name else sender
        if target_name is None:
            raise RuntimeError(
                "no player name given and no sender name available to follow "
                "(e.g. triggered from console/system chat, not a player message)"
            )

        entity = self.tracker.find_by_name(target_name)
        if entity is None:
            log.warning("follow: no known entity named %s (not currently visible?)", target_name)
            return ActionResult(message=f"I can't see {target_name}")

        self._following_name = target_name
        log.info("starting follow of %s (entity %d)", target_name, entity.id)
        await self.bridge.send_follow(entity.id, stop_distance=FOLLOW_STOP_DISTANCE)
        return ActionResult(message=f"ok, following {target_name}")

    async def stop(self, sender: str | None) -> ActionResult:
        self._following_name = None
        await self.bridge.send_stop()
        return ActionResult(message="ok, stopped")

    async def save(self, sender: str | None, name: str) -> ActionResult:
        """Saves the *caller's* current position under `name`, not the
        bot's own -- a player standing somewhere and typing "!save home"
        means "remember where I'm standing", not "remember where the bot
        happens to be" (found live: those two positions are rarely the
        same -- the bot could be off following someone else, mid-!collect,
        or just not have walked over yet). Resolved the same way !goto
        resolves a player-name target, via EntityTracker's live position
        mirror of the mod's own entity events.
        """
        if sender is None:
            log.warning("save: no sender to resolve a position for")
            return ActionResult(message="I don't know who's asking, so I don't know whose position to save")

        caller = self.tracker.find_by_name(sender)
        if caller is None:
            log.warning("save: sender %r isn't a currently tracked player", sender)
            return ActionResult(message="I can't see you right now, try again once I can")

        self.places.remember(name, caller.x, caller.y, caller.z)
        log.info("saved %r at (%.1f, %.1f, %.1f) (caller=%s)", name, caller.x, caller.y, caller.z, sender)
        return ActionResult(message=f"ok, saved this place as {name}")

    async def goto(self, sender: str | None, target: str | float, y: float | None = None, z: float | None = None) -> ActionResult:
        """Resolves `target` in order: explicit coordinates (all three
        args given as numbers) -> a known player's current position ->
        a remembered place -> gives up. Block-type and entity-type
        resolution (PENDING.md's "universal !goto") aren't implemented
        yet -- neither block-scanning nor entity-type tracking beyond
        players exists on the mod side yet; this handler is written so
        adding those later is just two more `elif` branches here, no
        interface change needed.
        """
        self._following_name = None  # goto is a one-shot goal, not FOLLOW -- don't let a later reconnect resume-follow hijack it

        if y is not None and z is not None:
            x_coord = float(target)
            log.info("going to explicit coordinates (%.1f, %.1f, %.1f)", x_coord, y, z)
            await self.bridge.send_goto(x_coord, y, z, stop_distance=GOTO_STOP_DISTANCE)
            return ActionResult(message=f"ok, going to ({x_coord:.0f}, {y:.0f}, {z:.0f})")

        target_name = str(target)

        entity = self.tracker.find_by_name(target_name)
        if entity is not None:
            log.info("going to player %s (entity %d)", target_name, entity.id)
            await self.bridge.send_goto(entity.x, entity.y, entity.z, stop_distance=GOTO_STOP_DISTANCE)
            return ActionResult(message=f"ok, going to {target_name}")

        place = self.places.get(target_name)
        if place is not None:
            log.info("going to remembered place %r at (%.1f, %.1f, %.1f)", target_name, place.x, place.y, place.z)
            await self.bridge.send_goto(place.x, place.y, place.z, stop_distance=GOTO_STOP_DISTANCE)
            return ActionResult(message=f"ok, going to {target_name}")

        log.warning("goto: %r is not a known player, remembered place, or coordinate", target_name)
        return ActionResult(message=f"I don't know where {target_name} is -- not a player I can see, not a saved place, and not coordinates")

    async def find(self, sender: str | None, query: str) -> ActionResult:
        """Locates the nearest entity or block matching `query` (entity
        type tried first, block type as fallback -- e.g. "cow" resolves as
        an entity since there's no cow block, "stone" resolves as a block
        since there's no stone entity) and walks to it, matching
        mindcraft's !searchForBlock/!searchForEntity behavior of moving to
        the result rather than just reporting it.
        """
        self._following_name = None  # find-and-goto is a one-shot goal, same reasoning as goto()

        found = asyncio.get_event_loop().create_future()
        self._pending_find = found
        log.info("searching for %r", query)
        sent_at = now()
        await self.bridge.send_find(query)
        try:
            result = await asyncio.wait_for(found, timeout=FIND_RESULT_TIMEOUT)
            log_timing(log, "find: got result @ +%.3fs", now() - sent_at)
        except asyncio.TimeoutError:
            log.warning("find: no response from mod for %r within %.0fs", query, FIND_RESULT_TIMEOUT)
            return ActionResult(message=f"I couldn't search for {query} (no response)")
        finally:
            self._pending_find = None

        if not result.get("found"):
            if not result.get("recognized", True):
                # BLOCK/ENTITY_TYPE are DefaultedRegistry mod-side, so an
                # unrecognized query would otherwise report the exact same
                # "not found nearby" as a real type that's just out of
                # range -- a player asked for these to be told apart
                # ("!find aaa" vs "!find allay" with none around).
                log.info("find: %r is not a recognized block or entity type", query)
                return ActionResult(message=f"{query} isn't a block or mob I know of")
            log.info("find: no %r within range", query)
            return ActionResult(message=f"I couldn't find any {query} nearby")

        x, y, z = result["x"], result["y"], result["z"]
        kind = result.get("kind", "thing")
        log.info("found %s %r at (%.1f, %.1f, %.1f), going there", kind, query, x, y, z)
        await self.bridge.send_chat(f"found {query} at ({x:.0f}, {y:.0f}, {z:.0f}), going there")

        arrived = asyncio.get_event_loop().create_future()
        self._pending_arrival = arrived
        await self.bridge.send_goto(x, y, z, stop_distance=GOTO_STOP_DISTANCE)
        try:
            await asyncio.wait_for(arrived, timeout=FIND_ARRIVAL_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("find: never arrived at %r within %.0fs (stuck or path too long)", query, FIND_ARRIVAL_TIMEOUT)
            return ActionResult(message=f"I'm having trouble reaching the {query}, might be stuck")
        finally:
            self._pending_arrival = None

        return ActionResult(message=f"here is the {query}")

    def on_find_result(self, data: dict) -> None:
        """Called from the run loop for every find_result event -- resolves
        whichever find() call is currently waiting, if any. A find_result
        arriving with nothing awaiting it (e.g. after a timeout already
        gave up) is just ignored.
        """
        log_timing(log, "on_find_result called @ %.3f: pending=%s", now(), self._pending_find is not None)
        if self._pending_find is not None and not self._pending_find.done():
            self._pending_find.set_result(data)

    def on_arrived(self) -> None:
        """Called from the run loop for every `arrived` event (see
        MinebotMod's gotoArrived) -- resolves find()'s wait for actually
        reaching its target, if find() is the one currently waiting.
        Ignored if nothing is pending (e.g. a bare !goto's own arrival,
        or one arriving after find()'s 60s timeout already gave up).
        """
        if self._pending_arrival is not None and not self._pending_arrival.done():
            self._pending_arrival.set_result(None)

    async def on_entity_added(self, name: str | None, entity_id: int) -> None:
        """Called from the run loop for every `entity` "add" event -- if
        the entity that just appeared is the player we're supposed to be
        following, re-sends `follow` with their fresh id so a
        disconnect/reconnect doesn't silently strand the goal on an id
        that will never resolve again.
        """
        if name is None or name != self._following_name:
            return
        log.info("resuming follow of %s after reconnect (new entity %d)", name, entity_id)
        await self.bridge.send_follow(entity_id, stop_distance=FOLLOW_STOP_DISTANCE)


def register_movement_actions(registry: ActionRegistry, movement: MovementController) -> None:
    registry.register(Action(
        name="follow",
        description="Walk to and follow a player. If no player_name is given, follows whoever sent the command.",
        handler=movement.follow,
        params=[
            ActionParam("player_name", "string", "Name of the player to follow.", required=False),
        ],
    ))
    registry.register(Action(
        name="stop",
        description="Stop whatever movement goal is currently active (follow/goto) and stand still.",
        handler=movement.stop,
    ))
    registry.register(Action(
        name="save",
        description="Save the caller's current position under a name, for later !goto.",
        handler=movement.save,
        params=[
            ActionParam("name", "string", "Name to save this place as."),
        ],
    ))
    registry.register(Action(
        name="goto",
        description=(
            "Walk to a target: a player's name, a previously remembered place, or explicit x y z coordinates. "
            "Does not continue following -- use !follow for that."
        ),
        handler=movement.goto,
        params=[
            ActionParam("target", "string", "Player name, remembered place name, or the x coordinate if giving explicit x y z."),
            ActionParam("y", "float", "Y coordinate, only if giving explicit coordinates.", required=False),
            ActionParam("z", "float", "Z coordinate, only if giving explicit coordinates.", required=False),
        ],
    ))
    registry.register(Action(
        name="find",
        description=(
            "Find and walk to the nearest block or entity matching a type (e.g. \"cow\", \"stone\"). "
            "Entity types are tried first, falling back to block types."
        ),
        handler=movement.find,
        params=[
            ActionParam("query", "string", "Block or entity type to search for, e.g. \"cow\" or \"oak_log\"."),
        ],
    ))
