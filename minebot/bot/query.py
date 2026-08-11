"""!query <arg> -- read-only introspection into the mod's own live state,
e.g. `!query player_intention` reports whether PlayerIntentionState is
currently IDLE/FOLLOW/DEFEND. Backs MinebotMod.handleQuery's own
`query_result` reply (see its own docstring for the full list of `arg`
values supported mod-side) -- built specifically so state can be asked
about directly instead of only ever inferred indirectly from broadcast
events, which is what let a real bug (LegsState staying in GOTO after
!stop -- see LegsStateMachine's own isStopCommand fix) go unnoticed for as
long as it did: nothing could assert against real final state before this.

`!query position` in particular exists to break a real, confirmed live
deadlock: the regular broadcast `position` event is exact-dedup'd
mod-side (see maybeBroadcastPositionEvent's own docstring) -- a bot that
goes genuinely motionless (e.g. wedged against a wall while LegsGotoNode
keeps failing to find a path to an unreachable target, re-planning A*
every tick with no backoff) produces bit-identical position snapshots
forever, so no further `position` event goes out at all, even though the
client itself is alive and ticking normally. Anything that only ever
waits for "the next broadcast position event" (actions.wait_for_position/
goto/teleport, all in minebot/testing/) hangs forever in that state with
nothing to explain why. `!query position` reads the bot's real live
position fresh, on demand, sidestepping the dedup entirely.

`!query block <x> <y> <z>` reads the real block ID at a given world
position, straight off the client's own loaded chunk data -- added to
debug a real live report: NO_PATH on ground a human operator confirmed
was flat/void. Lets a human (or Python) directly confirm what block the
mod itself actually sees at an exact coordinate, ruling out (or
confirming) a real discrepancy between what a human sees in the client
and what pathfinding's own block reads see.
"""

from __future__ import annotations

from minebot.actions.types import Action, ActionParam, ActionResult
from minebot.actions.registry import ActionRegistry
from minebot.bridge.client import ModBridge
from minebot.bridge.query import QueryResultTracker

# Real network round trip (send query, mod replies) but no game-world
# action involved at all -- should resolve in well under a second in
# practice; kept short so a query that never gets a reply (e.g. the mod
# somehow not connected) fails fast and visibly rather than hanging a chat
# command indefinitely.
QUERY_TIMEOUT_SECONDS = 5.0


def register_query_action(registry: ActionRegistry, bridge: ModBridge, query_result: QueryResultTracker) -> None:
    async def handler(sender: str | None, arg: str, x: int | None = None, y: int | None = None, z: int | None = None) -> ActionResult:
        await bridge.send_query(arg, x=x, y=y, z=z)
        event = await query_result.wait_for_next(timeout=QUERY_TIMEOUT_SECONDS)
        if "error" in event.data:
            return ActionResult(message=f"query error: {event.data['error']}")
        # "position"/"block" each carry their own reply shape, not a plain
        # "result" string like every other queryable fact -- see
        # MinebotMod.handleQuery's own docstring for why.
        if arg == "position":
            return ActionResult(message=f"position = {event.data.get('position')}")
        if arg == "block":
            return ActionResult(message=f"block at ({x}, {y}, {z}) = {event.data.get('block')} (loaded={event.data.get('loaded')})")
        return ActionResult(message=f"{arg} = {event.data.get('result')}")

    registry.register(Action(
        name="query",
        description="Ask the mod for its own current live state (e.g. !query player_intention, !query legs, !query hands, !query head, !query position, !query block <x> <y> <z>).",
        handler=handler,
        params=[
            ActionParam("arg", "string", "What to query: player_intention, legs, hands, head, position, or block.", required=True),
            ActionParam("x", "int", "Block x coordinate (only for 'block').", required=False),
            ActionParam("y", "int", "Block y coordinate (only for 'block').", required=False),
            ActionParam("z", "int", "Block z coordinate (only for 'block').", required=False),
        ],
    ))
