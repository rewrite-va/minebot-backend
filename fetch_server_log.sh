#!/usr/bin/env bash
# Pulls the Minecraft server's latest.log over SFTP for debugging the
# bow-fire-never-lands investigation from the server side, not just the
# client's own logs/backend log. Credentials never touch git or chat --
# they live only in .env.server-logs (gitignored), which this script
# sources for its own use.
#
# Set up .env.server-logs (same directory as this script) with:
#   SERVER_SFTP_HOST=example.com
#   SERVER_SFTP_PORT=22
#   SERVER_SFTP_USER=someuser
#   SERVER_SFTP_REMOTE_LOG=/path/to/server/logs/latest.log
#   # Prefer a key instead of a password:
#   SERVER_SFTP_KEY=/home/colaila/.ssh/id_ed25519
#   # Only if you must use password auth (requires sshpass installed):
#   SERVER_SFTP_PASSWORD=...
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ENV_FILE=".env.server-logs"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE -- see this script's own header comment for the variables it needs." >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${SERVER_SFTP_HOST:?SERVER_SFTP_HOST not set in $ENV_FILE}"
: "${SERVER_SFTP_USER:?SERVER_SFTP_USER not set in $ENV_FILE}"
: "${SERVER_SFTP_REMOTE_LOG:?SERVER_SFTP_REMOTE_LOG not set in $ENV_FILE}"
PORT="${SERVER_SFTP_PORT:-22}"
OUT="${1:-/tmp/server-latest.log}"

if [[ -n "${SERVER_SFTP_KEY:-}" ]]; then
    sftp -i "$SERVER_SFTP_KEY" -P "$PORT" -o StrictHostKeyChecking=accept-new \
        "${SERVER_SFTP_USER}@${SERVER_SFTP_HOST}:${SERVER_SFTP_REMOTE_LOG}" "$OUT"
elif [[ -n "${SERVER_SFTP_PASSWORD:-}" ]]; then
    command -v sshpass >/dev/null || { echo "sshpass not installed -- install it, or switch to SERVER_SFTP_KEY instead." >&2; exit 1; }
    sshpass -p "$SERVER_SFTP_PASSWORD" sftp -P "$PORT" -o StrictHostKeyChecking=accept-new \
        "${SERVER_SFTP_USER}@${SERVER_SFTP_HOST}:${SERVER_SFTP_REMOTE_LOG}" "$OUT"
else
    echo "Set either SERVER_SFTP_KEY or SERVER_SFTP_PASSWORD in $ENV_FILE." >&2
    exit 1
fi

echo "Fetched to $OUT"
