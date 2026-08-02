"""Movement command handlers (MVP scope from prompt.txt: forward, backward,
left, right).

Not implemented yet: sending these requires the PLAY-phase
ServerboundMovePlayerPacket variants and tracking the bot's current
position/rotation (from ClientboundPlayerPositionPacket on spawn), neither
of which exist yet in minebot/protocol. Registered here as stubs so the
chat -> command-registry -> handler wiring can be exercised end-to-end
before PLAY-phase packets are built.
"""

from __future__ import annotations

from minebot.commands.registry import CommandRegistry


class MovementController:
    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.yaw = 0.0

    async def forward(self, distance: float = 1.0) -> None:
        raise NotImplementedError("requires PLAY-phase movement packets")

    async def backward(self, distance: float = 1.0) -> None:
        raise NotImplementedError("requires PLAY-phase movement packets")

    async def strafe_left(self, distance: float = 1.0) -> None:
        raise NotImplementedError("requires PLAY-phase movement packets")

    async def strafe_right(self, distance: float = 1.0) -> None:
        raise NotImplementedError("requires PLAY-phase movement packets")


def register_movement_commands(registry: CommandRegistry, movement: MovementController) -> None:
    registry.register("forward", lambda *a: movement.forward(*a))
    registry.register("backward", lambda *a: movement.backward(*a))
    registry.register("left", lambda *a: movement.strafe_left(*a))
    registry.register("right", lambda *a: movement.strafe_right(*a))
