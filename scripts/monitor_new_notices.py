#!/usr/bin/env python3
"""
학교 공지 게시판을 주기적으로 확인하여
새 링크를 식별하고, 아직 수집되지 않은 게시물만
크롤링/이미지 생성까지 자동으로 수행하는 스크립트.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_PATH = SCRIPT_DIR.parent
BASE_DIR = str(BASE_PATH)

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(BASE_PATH) not in sys.path:
    sys.path.insert(0, str(BASE_PATH))


def _to_abs(path: str | None) -> str:
    if not path:
        return ''
    p = Path(path)
    if not p.is_absolute():
        p = (BASE_PATH / p).resolve()
    return str(p)


def _to_rel(path: str | None) -> str:
    if not path:
        return ''
    p = Path(path)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.relative_to(BASE_PATH))
    except ValueError:
        return str(p)

try:
    from crawl_notices import (
        MAX_ARTICLES,
        crawl_notice_detail,
        list_notice_links,
        DEFAULT_BOARDS,
        DEFAULT_ATTACHMENTS_DIR,
    )
except ImportError as exc:
    raise SystemExit(f"crawl_notices 모듈을 불러오지 못했습니다: {exc}") from exc

try:
    from scripts.generate_instagram_images import generate_notice_images
except ImportError as exc:
    raise SystemExit(f"이미지 생성 모듈 로드 실패: {exc}") from exc


DATA_PATH = BASE_PATH / "data"
LINKS_PATH = DATA_PATH / "notice_links.json"
DETAILS_PATH = DATA_PATH / "notices_db.json"
DATA_DIR = str(DATA_PATH)
LINKS_FILE = str(LINKS_PATH)
DETAILS_FILE = str(DETAILS_PATH)


def _set_data_dir(data_dir: str | None) -> None:
    global DATA_PATH, LINKS_PATH, DETAILS_PATH, DATA_DIR, LINKS_FILE, DETAILS_FILE
    if not data_dir:
        return
    DATA_PATH = Path(_to_abs(data_dir)).resolve()
    LINKS_PATH = DATA_PATH / "notice_links.json"
    DETAILS_PATH = DATA_PATH / "notices_db.json"
    DATA_DIR = str(DATA_PATH)
    LINKS_FILE = str(LINKS_PATH)
    DETAILS_FILE = str(DETAILS_PATH)

def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_json_dict(path: str) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items()}
            if isinstance(data, list):
                converted: dict[str, dict] = {}
                for item in data:
                    if isinstance(item, dict) and item.get("notice_key"):
                        converted[str(item["notice_key"])] = item
                    elif isinstance(item, dict) and item.get("id"):
                        converted[str(item["id"])] = item
                return converted
    except Exception:
        pass
    return {}


def _write_json(path: str, data: dict) -> None:
    _ensure_data_dir()
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _link_key(board_id: str | None, notice_id: str | None) -> str:
    board_part = str(board_id or "default")
    notice_part = str(notice_id or "")
    return f"{board_part}::{notice_part}"


def _load_board_config(path: str | None, attachments_base: str) -> list[dict[str, Any]]:
    attachments_base_path = Path(_to_abs(attachments_base))

    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                boards = json.load(fh)
                if not isinstance(boards, list):
                    raise ValueError("boards 설정 파일은 리스트 형태여야 합니다.")
        except Exception as exc:
            raise SystemExit(f"게시판 설정 파일 로드 실패: {exc}") from exc
    else:
        boards = DEFAULT_BOARDS

    normalized: list[dict[str, Any]] = []
    for board in boards:
        if not isinstance(board, dict):
            continue
        board_id = str(board.get("id") or "default")
        name = board.get("name") or board_id
        fallback_url = DEFAULT_BOARDS[0]["url"] if DEFAULT_BOARDS else ""
        url = board.get("url") or fallback_url
        max_articles = int(board.get("max_articles") or MAX_ARTICLES)
        parser_name = str(board.get("parser") or "").strip().lower()
        attachments_dir_value = board.get("attachments_dir")
        if attachments_dir_value:
            attachments_dir_path = Path(_to_abs(attachments_dir_value))
        else:
            attachments_dir_path = attachments_base_path / board_id
        attachments_dir_path = attachments_dir_path.resolve()
        normalized.append(
            {
                "id": board_id,
                "name": name,
                "url": url,
                "max_articles": max_articles,
                "attachments_dir": _to_rel(str(attachments_dir_path)),
                "parser": parser_name,
            }
        )
    return normalized


def _update_links(session_links: list[dict], links_db: dict[str, dict]) -> None:
    now_iso = datetime.now().isoformat()
    for entry in session_links:
        notice_id = entry.get("id")
        board_id = entry.get("board_id") or "default"
        if not notice_id:
            continue
        key = _link_key(board_id, notice_id)
        record = links_db.get(key, {})
        record.update(
            {
                "id": notice_id,
                "notice_id": notice_id,
                "url": entry.get("url") or record.get("url"),
                "title": entry.get("title") or record.get("title"),
                "board_id": board_id,
                "board_name": entry.get("board_name") or record.get("board_name"),
                "board_url": entry.get("board_url") or record.get("board_url"),
                "attachments_dir": entry.get("attachments_dir")
                or record.get("attachments_dir"),
                "last_checked": now_iso,
            }
        )
        if "first_seen" not in record:
            record["first_seen"] = now_iso
            record["crawled"] = False
        record["attachments_dir"] = _to_rel(record.get("attachments_dir"))
        links_db[key] = record


def _store_detail(record: dict, details_db: dict[str, dict], key: str) -> None:
    if not record:
        return
    record_copy = dict(record)
    record_copy.setdefault("notice_key", key)
    if "attachment_dir" in record_copy:
        record_copy["attachment_dir"] = _to_rel(record_copy.get("attachment_dir"))
    if "downloaded_files" in record_copy:
        record_copy["downloaded_files"] = [
            _to_rel(p) for p in record_copy.get("downloaded_files") or []
        ]
    if "pdf_files" in record_copy:
        record_copy["pdf_files"] = [
            _to_rel(p) for p in record_copy.get("pdf_files") or []
        ]
    if "png_files" in record_copy:
        record_copy["png_files"] = [
            _to_rel(p) for p in record_copy.get("png_files") or []
        ]
    details_db[key] = record_copy


def monitor(
    interval_minutes: int,
    *,
    attachments_base: str,
    download_attachments: bool = True,
    max_images: int = 20,
    boards_config: str | None = None,
    data_dir: str | None = None,
    workers: int = 4,
) -> None:
    _set_data_dir(data_dir)
    print(
        f"[모니터링 시작] 주기: {interval_minutes}분, 첨부 다운로드: {download_attachments}, 병렬 작업: {workers}"
    )
    print(f"[데이터 경로] {DATA_DIR}")
    print("[명령어] 'r' 또는 'refresh' 입력 시 즉시 새로고침, 'q' 또는 'quit' 입력 시 종료")
    print("-" * 60)

    boards = _load_board_config(boards_config, attachments_base)
    board_map = {board["id"]: board for board in boards}

    links_db = _load_json_dict(LINKS_FILE)
    details_db = _load_json_dict(DETAILS_FILE)

    # 새로고침 요청 플래그
    refresh_requested = threading.Event()
    should_quit = threading.Event()

    def input_listener():
        """사용자 입력을 받는 스레드"""
        while not should_quit.is_set():
            try:
                user_input = input().strip().lower()
                if user_input in ('r', 'refresh'):
                    print("\n[사용자 요청] 즉시 새로고침을 시작합니다...")
                    refresh_requested.set()
                elif user_input in ('q', 'quit'):
                    print("\n[사용자 요청] 모니터링을 종료합니다...")
                    should_quit.set()
                    break
                elif user_input in ('h', 'help', '?'):
                    print("\n[명령어 도움말]")
                    print("  r, refresh : 즉시 새로고침")
                    print("  q, quit    : 모니터링 종료")
                    print("  h, help, ? : 도움말 표시")
                    print()
            except EOFError:
                break
            except Exception:
                pass

    # 입력 리스너 스레드 시작
    listener_thread = threading.Thread(target=input_listener, daemon=True)
    listener_thread.start()

    def process_notice(key: str, link_info: dict, board: dict) -> tuple[str, dict | None, str | None]:
        board_id = board["id"]
        board_name = board["name"]
        board_url = board["url"]
        attachments_dir_rel = board["attachments_dir"]
        notice_id = link_info.get("notice_id") or link_info.get("id")

        detail = crawl_notice_detail(
            link_info.get("url"),
            notice_id=notice_id,
            download_attachments=download_attachments,
            attachments_dir=_to_abs(
                link_info.get("attachments_dir") or attachments_dir_rel
            ),
            title_hint=link_info.get("title"),
            board_id=board_id,
            board_name=board_name,
            board_url=board_url,
            parser=board.get("parser"),
        )
        if not detail:
            return key, None, "상세 크롤링 실패"

        if download_attachments and detail.get("attachment_dir"):
            try:
                result = generate_notice_images(
                    detail,
                    _to_abs(detail["attachment_dir"]),
                    max_images=max_images,
                )
                detail["image_result"] = result
            except Exception as exc:
                return key, detail, f"이미지 생성 실패: {exc}"

        return key, detail, None

    def run_crawl_cycle():
        """한 번의 크롤링 사이클 실행"""
        nonlocal links_db, details_db

        for board in boards:
            board_id = board["id"]
            board_name = board["name"]
            board_url = board["url"]
            attachments_dir_rel = board["attachments_dir"]
            attachments_dir_abs = _to_abs(attachments_dir_rel)
            os.makedirs(attachments_dir_abs, exist_ok=True)

            session_links = list_notice_links(
                board_url,
                max_articles=board.get("max_articles", MAX_ARTICLES),
                parser=board.get("parser"),
            )
            for entry in session_links:
                entry["board_id"] = board_id
                entry["board_name"] = board_name
                entry["board_url"] = board_url
                entry["attachments_dir"] = attachments_dir_rel

            _update_links(session_links, links_db)
            _write_json(LINKS_FILE, links_db)

            pending_keys = [
                key
                for key, info in links_db.items()
                if not info.get("crawled")
                and not info.get("hidden")
                and (info.get("board_id") or "default") == board_id
            ]

            if not pending_keys:
                print(
                    f"[{board_name}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 신규 링크 없음"
                )
                continue

            active_workers = max(1, min(workers, len(pending_keys)))
            print(f"[{board_name}] 새 링크 {len(pending_keys)}건 처리 중... (workers={active_workers})")

            with ThreadPoolExecutor(max_workers=active_workers) as executor:
                futures = {}
                for key in pending_keys:
                    link_info = dict(links_db.get(key) or {})
                    futures[
                        executor.submit(process_notice, key, link_info, dict(board))
                    ] = (key, link_info)

                for future in as_completed(futures):
                    key, link_info = futures[future]
                    try:
                        _, detail, error = future.result()
                    except Exception as exc:
                        print(f"[에러] 처리 실패 ({key}): {exc}")
                        continue

                    if error:
                        print(f"[경고] {error}: {key}")
                    if not detail:
                        continue

                    # 디스크의 최신 내용을 다시 읽어 이 항목만 갱신한다.
                    # (시작 시점 메모리 사본을 그대로 덮어쓰면 웹 대시보드의
                    #  이미지 순서변경/삭제/썸네일 편집이 되돌려진다.)
                    details_db = _load_json_dict(DETAILS_FILE)
                    links_db = _load_json_dict(LINKS_FILE)
                    if (links_db.get(key) or {}).get("hidden"):
                        print(f"[건너뜀] 대시보드에서 삭제된 공지: {key}")
                        continue
                    _store_detail(detail, details_db, key)
                    _write_json(DETAILS_FILE, details_db)

                    links_db.setdefault(key, {})
                    links_db[key]["crawled"] = True
                    links_db[key]["crawled_at"] = detail.get("crawled_at")
                    if detail.get("attachment_dir"):
                        links_db[key]["attachment_dir"] = detail["attachment_dir"]
                    links_db[key]["title"] = detail.get("title") or link_info.get("title")
                    _write_json(LINKS_FILE, links_db)

                    print(f"[처리 완료] {board_name} - {detail.get('title')}")

    try:
        while not should_quit.is_set():
            cycle_start = time.time()

            # 크롤링 사이클 실행
            run_crawl_cycle()

            # 새로고침 플래그 초기화
            refresh_requested.clear()

            elapsed = time.time() - cycle_start
            sleep_seconds = max(5, interval_minutes * 60 - int(elapsed))

            print(f"\n[대기 중] 다음 크롤링까지 {sleep_seconds}초 대기... (명령어 입력 가능)")

            # 대기 시간 동안 새로고침 요청 확인
            for _ in range(sleep_seconds):
                if should_quit.is_set():
                    break
                if refresh_requested.is_set():
                    print("\n[즉시 새로고침] 크롤링을 시작합니다...")
                    break
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n사용자 중단으로 모니터링을 종료합니다.")
        should_quit.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="충남대 공지 자동 모니터링")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="크롤링 주기 (분 단위, 기본값: 60분)",
    )
    parser.add_argument(
        "--attachments-dir",
        default=DEFAULT_ATTACHMENTS_DIR,
        help="첨부파일 저장 기본 경로 (게시판 id 하위로 생성됨)",
    )
    parser.add_argument(
        "--boards-config",
        help="모니터링할 게시판 설정 JSON 파일 경로",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="notice_links.json / notices_db.json 저장 경로 (기본: data)",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=20,
        help="공지당 생성할 최대 이미지 수 (기본값: 20)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="동시에 처리할 신규 공지 수 (기본값: 4)",
    )
    args = parser.parse_args()

    monitor(
        interval_minutes=max(1, args.interval),
        attachments_base=args.attachments_dir,
        download_attachments=True,
        max_images=max(3, args.max_images),
        boards_config=args.boards_config,
        data_dir=args.data_dir,
        workers=max(1, args.workers),
    )


if __name__ == "__main__":
    main()
