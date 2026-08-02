"""Movement command handlers (MVP scope from prompt.txt: forward, backward,
left, right).

Not implemented yet: sending these requires the PLAY-phase
ServerboundMovePlayerPacket variants and tracking the bot's current
position/rotation (from ClientboundPlayerPositionPacket on spawn), neither
of which exist yet in minebot/protocol. Registered here as stubs so the
chat -> command-registry -> handler wiring can be exercised end-to-end
before PLAY-phase packets are built.

Handlers take `conn` as their first argument to match run_play_loop's
dispatch contract (commands.dispatch(text, conn) -- see bot/play_loop.py),
since sending real movement packets will need the connection once
implemented.
"""

from __future__ import annotations

from minebot.commands.registry import CommandRegistry
from minebot.net.connection import Connection


class MovementController:
    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.yaw = 0.0

    async def forward(self, conn: Connection, distance: float = 1.0) -> None:
        raise NotImplementedError("requires PLAY-phase movement packets")

    async def backward(self, conn: Connection, distance: float = 1.0) -> None:
        raise NotImplementedError("requires PLAY-phase movement packets")

    async def strafe_left(self, conn: Connection, distance: float = 1.0) -> None:
        raise NotImplementedError("requires PLAY-phase movement packets")

    async def strafe_right(self, conn: Connection, distance: float = 1.0) -> None:
        raise NotImplementedError("requires PLAY-phase movement packets")


def register_movement_commands(registry: CommandRegistry, movement: MovementController) -> None:
    registry.register("forward", movement.forward)
    registry.register("backward", movement.backward)
    registry.register("left", movement.strafe_left)
    registry.register("right", movement.strafe_right)
