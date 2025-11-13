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
) -> None:
    print(
        f"[모니터링 시작] 주기: {interval_minutes}분, 첨부 다운로드: {download_attachments}"
    )

    boards = _load_board_config(boards_config, attachments_base)
    board_map = {board["id"]: board for board in boards}

    links_db = _load_json_dict(LINKS_FILE)
    details_db = _load_json_dict(DETAILS_FILE)

    try:
        while True:
            cycle_start = time.time()

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
                    and (info.get("board_id") or "default") == board_id
                ]

                if not pending_keys:
                    print(
                        f"[{board_name}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 신규 링크 없음"
                    )
                    continue

                print(f"[{board_name}] 새 링크 {len(pending_keys)}건 처리 중...")

                for key in pending_keys:
                    link_info = links_db.get(key) or {}
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
                    )
                    if not detail:
                        print(f"[경고] 상세 크롤링 실패: {key}")
                        continue

                    if download_attachments and detail.get("attachment_dir"):
                        try:
                            result = generate_notice_images(
                                detail,
                                _to_abs(detail["attachment_dir"]),
                                max_images=max_images,
                            )
                            detail["image_result"] = result
                        except Exception as exc:
                            print(f"[에러] 이미지 생성 실패 ({key}): {exc}")

                    _store_detail(detail, details_db, key)
                    _write_json(DETAILS_FILE, details_db)

                    links_db[key]["crawled"] = True
                    links_db[key]["crawled_at"] = detail.get("crawled_at")
                    if detail.get("attachment_dir"):
                        links_db[key]["attachment_dir"] = detail["attachment_dir"]
                    links_db[key]["title"] = detail.get("title") or link_info.get("title")
                    _write_json(LINKS_FILE, links_db)

                    print(f"[처리 완료] {board_name} - {detail.get('title')}")

            elapsed = time.time() - cycle_start
            sleep_seconds = max(5, interval_minutes * 60 - int(elapsed))
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        print("\n사용자 중단으로 모니터링을 종료합니다.")


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
        "--max-images",
        type=int,
        default=20,
        help="공지당 생성할 최대 이미지 수 (기본값: 20)",
    )
    args = parser.parse_args()

    monitor(
        interval_minutes=max(1, args.interval),
        attachments_base=args.attachments_dir,
        download_attachments=True,
        max_images=max(3, args.max_images),
        boards_config=args.boards_config,
    )


if __name__ == "__main__":
    main()

