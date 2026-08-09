import pytest

from minebot.bot.movement import FOLLOW_STOP_DISTANCE, MovementController
from minebot.bot.player_intention import PlayerIntention, PlayerIntentionController
from minebot.bridge.entities import EntityTracker


class RecordingBridge:
    """Stands in for a real ModBridge: records every sent command instead
    of touching a socket, so command translation can be tested without a
    running mod.
    """

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_follow(self, player_name, stop_distance=2.0):
        self.sent.append(("follow", {"player_name": player_name, "stop_distance": stop_distance}))

    async def send_stop(self):
        self.sent.append(("stop", {}))

    async def send_pickup(self):
        self.sent.append(("pickup", {}))

    async def send_sleep(self):
        self.sent.append(("sleep", {}))

    async def send_chat(self, text):
        self.sent.append(("chat", {"text": text}))


def _movement(bridge, tracker=None, intention=None) -> MovementController:
    tracker = tracker if tracker is not None else EntityTracker()
    intention = intention if intention is not None else PlayerIntentionController()
    return MovementController(bridge, tracker, intention)


@pytest.mark.asyncio
async def test_follow_with_explicit_name_sends_follow_for_that_name():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    result = await movement.follow(None, "Alex")

    assert bridge.sent == [("follow", {"player_name": "Alex", "stop_distance": FOLLOW_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_follow_with_no_name_follows_the_chat_sender():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    result = await movement.follow("Alex")

    assert bridge.sent == [("follow", {"player_name": "Alex", "stop_distance": FOLLOW_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_follow_with_no_name_and_no_sender_raises():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    with pytest.raises(RuntimeError):
        await movement.follow(None)


@pytest.mark.asyncio
async def test_follow_unseen_player_still_sends_follow_by_name():
    # Unlike the old EntityTracker-gated behavior, a name the bot has
    # never seen as a loaded entity is still sent straight through --
    # PlayerController (mod-side) may still resolve it via the tab list
    # even though Python itself has no record of them (see movement.py's
    # own module docstring).
    bridge = RecordingBridge()
    movement = _movement(bridge)

    result = await movement.follow(None, "NobodyHome")

    assert bridge.sent == [("follow", {"player_name": "NobodyHome", "stop_distance": FOLLOW_STOP_DISTANCE})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_stop_sends_stop_command():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    result = await movement.stop(None)

    assert bridge.sent == [("stop", {})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_stop_sets_player_intention_to_idle():
    # Mirrors minebot-mod's PlayerIntention: !stop ends DEFEND there too
    # (see PlayerIntentionState's own docstring), so the tracked intention
    # here must follow the same edge, or a later hostile hit would wrongly
    # stay silent thinking DEFEND is still active.
    bridge = RecordingBridge()
    intention = PlayerIntentionController()
    intention.set_intention(PlayerIntention.DEFEND)
    movement = _movement(bridge, intention=intention)

    await movement.stop(None)

    assert intention.current == PlayerIntention.IDLE


@pytest.mark.asyncio
async def test_follow_sets_player_intention_to_follow():
    bridge = RecordingBridge()
    intention = PlayerIntentionController()
    intention.set_intention(PlayerIntention.DEFEND)
    movement = _movement(bridge, intention=intention)

    await movement.follow(None, "Alex")

    assert intention.current == PlayerIntention.FOLLOW


@pytest.mark.asyncio
async def test_pickup_sends_pickup_command():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    result = await movement.pickup(None)

    assert bridge.sent == [("pickup", {})]
    assert result.message is not None


@pytest.mark.asyncio
async def test_sleep_sends_sleep_command():
    bridge = RecordingBridge()
    movement = _movement(bridge)

    result = await movement.sleep(None)

    assert bridge.sent == [("sleep", {})]
    assert result.message is not None
