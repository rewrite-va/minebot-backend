# Integration test coverage: pending vs. done

Tracks real-client integration test coverage (`tests/integration/` /
`!runtest`, see `minebot-mod`'s `TESTING.md` for how these run) against
the bot's actual command surface (`minebot/bot/*.py`'s registered
`Action`s, `MinebotMod.dispatchMessage`'s wire command `case`s). A
checked box means a registered `TestCase` + pytest entry point exists and
currently passes on both entry points (`!runtest`, `uv run pytest
tests/integration/`); unchecked means no real-client coverage exists yet
-- the capability may still have unit tests against a fake bridge, just
nothing that drives a real client/world.

## Movement / pathfinding

- [x] `goto` -- bare-coordinate walk, asserts arrival (`goto`)
- [x] `goto` onto a placed schematic block (`goto_onto_schematic`)
- [x] `goto` across a 1-block gap, jump required (`goto_jump_1`)
- [x] `goto` across a 2-block gap + climb (`goto_jump_2`)
- [x] `goto` across a longer course combining a required path checkpoint
      and a 2-block forbidden gap (`goto_jump_3`)
- [x] `goto` toward a genuinely unreachable target, asserts it's never
      reached (`goto_impossible`)
- [ ] `goto` through a path requiring the bot to BREAK a block out of the
      way (`BlockBreaker`/digging-while-pathing, not just walking around
      obstacles)
- [x] `goto` across a longer course combining a required path checkpoint
      and a five-block forbidden gap (`goto_jump_4`)
- [x] `goto` up/down stairs or slabs (half-height terrain, distinct from
      a full-block jump) (`goto_stairs_1`)
- [x] `goto` across a gap wider than the bot's real jump range -- asserts
      the bot recognizes it as unreachable rather than attempting and
      failing the jump (distinct from `goto_impossible`'s wall-blocked
      case) (`goto_jump_5`)
- [ ] `stop` mid-`goto` -- asserts the bot actually halts and every peer
      state machine (legs/hands/head/player_intention) returns to IDLE
      before its target is reached

## Following / defending / combat

- [ ] `follow` a player -- asserts the bot's real position converges to
      within `stopDistance` and re-converges after the target moves
- [ ] `follow` stopped via `stop` -- asserts the bot actually halts
- [ ] `defend` (self) -- spawn/summon a hostile mob nearby, assert the
      bot engages and the mob dies (or the bot survives / player_intention
      reflects DEFEND)
- [ ] `defend <player>` -- asserts the bot stays near the named player
      and fights hostiles that approach them, not just itself
- [ ] `kill <entity type>` -- one-shot fight-and-kill, asserts target
      entity's death and player_intention returning to IDLE after
- [ ] `kill` a targeted player entity id (vs. a type-query kill)

## Items / inventory

- [ ] `pickup` -- drop an item near the bot, assert it walks over and
      picks it up (inventory reflects the gain)
- [ ] `give` with an explicit recipient/item/quantity
- [ ] `give` with defaults (no recipient -> drop at own feet; no item ->
      "last item picked up"; no/zero quantity -> whole stack)

## Sleep / world state

- [ ] `sleep` -- asserts the bot finds and sleeps in a real placed bed
      (real vanilla sleep state, not just "walked near a bed")

## Death / respawn

- [ ] Death while executing a `goto`/`follow`/`defend` -- asserts
      `DeathWatcher`'s own recovery path takes over cleanly and no stale
      command re-fires post-respawn (see `DeathWatcher`'s own docstring
      for the exact live bug this guards against: "!follow -> !defend ->
      die -> respawn -> back to follow instead of defend")

## Query / introspection

- [x] Exercised indirectly by every schematic-driven test's own teardown
      assertion (`player_intention`/`legs`/`hands`/`head` all IDLE after
      each test, see `tests/integration/conftest.py`'s `ctx` fixture) --
      no dedicated standalone test, but effectively covered on every run.
