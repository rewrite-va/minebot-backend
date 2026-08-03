#!/usr/bin/env bash
# Starts the minebot Python backend. Waits for minebot-mod (running inside
# a real Minecraft client) to connect in over the local control-channel
# WebSocket -- see FINDINGS.md for the full architecture.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

exec uv run python -m minebot.main
