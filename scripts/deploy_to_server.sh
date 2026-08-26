#!/usr/bin/env bash
set -euo pipefail

# The server owns runtime data (data/, attachments/, .env). This script only
# deploys Git-tracked application code and then restarts the managed services.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${CNUINFO_SERVER_HOST:-moon@moonhome.kro.kr}"
REMOTE_PORT="${CNUINFO_SERVER_PORT:-2222}"
REMOTE_DIR="${CNUINFO_SERVER_DIR:-/srv/cnuinfo}"

cd "$ROOT_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "커밋되지 않은 코드 변경이 있습니다. 커밋 후 다시 실행하세요." >&2
  exit 1
fi

git push origin main

ssh -p "$REMOTE_PORT" "$REMOTE_HOST" "
  set -e
  cd '$REMOTE_DIR'
  git fetch origin main
  git reset --hard origin/main
  .venv/bin/pip install -r requirements.txt
  sudo install -m 644 deploy/cnu-info-web.service /etc/systemd/system/cnu-info-web.service
  sudo install -m 644 deploy/cnu-info-monitor.service /etc/systemd/system/cnu-info-monitor.service
  sudo systemctl daemon-reload
  sudo install -m 644 deploy/cnu-info.nginx /etc/nginx/sites-available/cnu-info
  sudo nginx -t
  sudo systemctl reload nginx
  sudo systemctl restart cnu-info-web cnu-info-monitor
  curl -fsS -o /dev/null http://127.0.0.1:8003/
"

echo "배포 완료: 코드만 갱신했고 서버의 data/, attachments/, .env는 유지했습니다."
