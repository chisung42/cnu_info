#!/usr/bin/env bash
set -euo pipefail

# Pull-only runtime synchronization. The production server is authoritative;
# this script never sends local crawl data back to the server.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${CNUINFO_SERVER_HOST:-moon@moonhome.kro.kr}"
REMOTE_PORT="${CNUINFO_SERVER_PORT:-2222}"
REMOTE_DIR="${CNUINFO_SERVER_DIR:-/srv/cnuinfo}"
MIRROR=false

if [[ "${1:-}" == "--mirror" ]]; then
  MIRROR=true
elif [[ $# -gt 0 ]]; then
  echo "사용법: $0 [--mirror]" >&2
  exit 2
fi

RSYNC_ARGS=(-a --partial --human-readable)
if [[ "$MIRROR" == true ]]; then
  # Removes only local runtime files that no longer exist on the server.
  RSYNC_ARGS+=(--delete-delay)
fi

cd "$ROOT_DIR"
rsync "${RSYNC_ARGS[@]}" -e "ssh -p $REMOTE_PORT" \
  "$REMOTE_HOST:$REMOTE_DIR/data/" "$ROOT_DIR/data/"
rsync "${RSYNC_ARGS[@]}" -e "ssh -p $REMOTE_PORT" \
  "$REMOTE_HOST:$REMOTE_DIR/attachments/" "$ROOT_DIR/attachments/"

echo "서버 운영 데이터를 로컬로 동기화했습니다."
