#!/usr/bin/env python3
"""
monitor_new_notices.py와 web_dashboard.py를 한 번에 실행합니다.
Ctrl+C(또는 SIGTERM)로 두 프로세스를 함께 종료합니다.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MONITOR_SCRIPT = SCRIPT_DIR / "monitor_new_notices.py"
DASHBOARD_SCRIPT = SCRIPT_DIR / "web_dashboard.py"


def _terminate_all(procs: list[subprocess.Popen[str]]) -> None:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        if p.poll() is None:
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="공지 모니터링 크론 + 웹 대시보드를 동시에 실행합니다.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="모니터 크롤링 주기(분, 기본 60)",
    )
    parser.add_argument(
        "--attachments-dir",
        default="",
        help="첨부 저장 경로(비우면 monitor_new_notices 기본값)",
    )
    parser.add_argument(
        "--boards-config",
        default="",
        help="게시판 설정 JSON 경로",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="notice_links.json / notices_db.json 저장 경로",
    )
    parser.add_argument("--max-images", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--host", default="127.0.0.1", help="대시보드 호스트")
    parser.add_argument("--port", type=int, default=8001, help="대시보드 포트")
    parser.add_argument("--debug", action="store_true", help="Flask 디버그 모드")
    args = parser.parse_args()

    if not MONITOR_SCRIPT.is_file():
        sys.exit(f"스크립트 없음: {MONITOR_SCRIPT}")
    if not DASHBOARD_SCRIPT.is_file():
        sys.exit(f"스크립트 없음: {DASHBOARD_SCRIPT}")

    py = sys.executable
    monitor_cmd: list[str] = [
        py,
        str(MONITOR_SCRIPT),
        "--interval",
        str(max(1, args.interval)),
        "--max-images",
        str(max(3, args.max_images)),
        "--workers",
        str(max(1, args.workers)),
    ]
    if args.attachments_dir.strip():
        monitor_cmd += ["--attachments-dir", args.attachments_dir.strip()]
    if args.boards_config.strip():
        monitor_cmd += ["--boards-config", args.boards_config.strip()]
    if args.data_dir.strip():
        monitor_cmd += ["--data-dir", args.data_dir.strip()]

    dashboard_cmd: list[str] = [
        py,
        str(DASHBOARD_SCRIPT),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--data-dir",
        args.data_dir,
    ]
    if args.debug:
        dashboard_cmd.append("--debug")

    procs: list[subprocess.Popen[str]] = [
        subprocess.Popen(monitor_cmd, cwd=str(REPO_ROOT)),
        subprocess.Popen(dashboard_cmd, cwd=str(REPO_ROOT)),
    ]

    def _on_signal(_signum: int, _frame: object | None) -> None:
        _terminate_all(procs)
        sys.exit(130)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        while True:
            for i, p in enumerate(procs):
                code = p.poll()
                if code is not None:
                    label = "모니터" if i == 0 else "대시보드"
                    print(f"[run_monitor_and_dashboard] {label} 종료 (코드 {code})", file=sys.stderr)
                    _terminate_all(procs)
                    sys.exit(code if code is not None else 1)
            time.sleep(0.4)
    except KeyboardInterrupt:
        _terminate_all(procs)
        sys.exit(130)


if __name__ == "__main__":
    main()
