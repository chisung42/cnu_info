#!/usr/bin/env python3
"""
공지 크롤링 진행 상황을 웹 페이지로 제공하는 대시보드.

- notices_db.json / notice_links.json을 읽어 상태 표시
- 제목/내용 복사 버튼 제공
- 생성된 이미지를 ZIP으로 다운로드할 수 있는 링크 제공
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    jsonify,
    render_template_string,
    request,
    send_file,
)
from PIL import Image
from werkzeug.serving import WSGIRequestHandler

try:
    from scripts.notice_display import make_display_content
except ImportError:
    from notice_display import make_display_content

try:
    try:
        from scripts import instagram_sync
    except ImportError:
        import instagram_sync
    INSTAGRAM_SYNC_AVAILABLE = True
except Exception:
    instagram_sync = None
    INSTAGRAM_SYNC_AVAILABLE = False

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_PATH = SCRIPT_DIR.parent
BASE_DIR = str(BASE_PATH)
DATA_DIR = str(BASE_PATH / "data")
NOTICE_DB_PATH = str(BASE_PATH / "data" / "notices_db.json")
LINKS_PATH = str(BASE_PATH / "data" / "notice_links.json")

ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".hwp", ".hwpx"}


def _set_data_dir(data_dir: str | None) -> None:
    global DATA_DIR, NOTICE_DB_PATH, LINKS_PATH
    if not data_dir:
        return
    data_path = Path(data_dir)
    if not data_path.is_absolute():
        data_path = (BASE_PATH / data_path).resolve()
    DATA_DIR = str(data_path)
    NOTICE_DB_PATH = str(data_path / "notices_db.json")
    LINKS_PATH = str(data_path / "notice_links.json")


def _sanitize_upload_filename(filename: str | None) -> str:
    if not filename:
        return ""
    name = os.path.basename(filename)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:80] or f"upload_{uuid.uuid4().hex[:8]}"


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _unique_path(directory: str, stem: str, ext: str) -> str:
    candidate = f"{stem}{ext}"
    counter = 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{stem}_{counter}{ext}"
        counter += 1
    return os.path.join(directory, candidate)


def _save_image_as_png(src_path: str, dest_path: str) -> str:
    _ensure_dir(os.path.dirname(dest_path))
    with Image.open(src_path) as img:
        img.convert("RGB").save(dest_path, format="PNG", quality=95)
    return dest_path


def _convert_pdf_to_pngs(pdf_path: str, output_dir: str, base_name: str) -> list[str]:
    _ensure_dir(output_dir)
    converted: list[str] = []
    try:
        import fitz  # type: ignore

        doc = fitz.open(pdf_path)
        try:
            for index, page in enumerate(doc):
                pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
                dest = os.path.join(output_dir, f"{base_name}_{index+1:03d}.png")
                pix.save(dest)
                converted.append(dest)
        finally:
            doc.close()
        return converted
    except Exception:
        pass

    try:
        from pdf2image import convert_from_path  # type: ignore

        images = convert_from_path(pdf_path, dpi=200)
        for index, img in enumerate(images):
            dest = os.path.join(output_dir, f"{base_name}_{index+1:03d}.png")
            img.convert("RGB").save(dest, format="PNG")
            converted.append(dest)
        return converted
    except Exception:
        pass

    # Fallback to ImageMagick if available
    try:
        dest_pattern = os.path.join(output_dir, f"{base_name}_%03d.png")
        subprocess.run(
            ["magick", "-density", "200", pdf_path, "-quality", "92", dest_pattern],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for name in sorted(os.listdir(output_dir)):
            path = os.path.join(output_dir, name)
            if path.startswith(os.path.join(output_dir, f"{base_name}_")) and path.endswith(".png"):
                converted.append(path)
        return converted
    except Exception:
        pass

    return converted

# crawl_notices 모듈 임포트
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from crawl_notices import (
        DEFAULT_ATTACHMENTS_DIR,
        FACTCHAT_MODEL,
        _summarize_complex_content,
        crawl_notice_detail,
    )
    CRAWL_AVAILABLE = True
    MANUAL_AI_AVAILABLE = True
except ImportError:
    CRAWL_AVAILABLE = False
    MANUAL_AI_AVAILABLE = False
    DEFAULT_ATTACHMENTS_DIR = "attachments"

try:
    from generate_instagram_images import generate_notice_images, generate_notice_thumbnail_header
    IMAGE_GEN_AVAILABLE = True
except ImportError:
    IMAGE_GEN_AVAILABLE = False

try:
    from document_conversion import HWP_EXTENSIONS, convert_hwp_to_pdf
except ImportError:
    HWP_EXTENSIONS = (".hwp", ".hwpx")
    convert_hwp_to_pdf = None


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


def _clear_recrawl_derived_files(attachment_dir: str | None) -> list[str]:
    """Remove only reproducible outputs before a notice is re-crawled."""
    if not attachment_dir:
        return []

    attachment_path = Path(_to_abs(attachment_dir)).resolve()
    attachments_root = (BASE_PATH / DEFAULT_ATTACHMENTS_DIR).resolve()
    try:
        attachment_path.relative_to(attachments_root)
    except ValueError:
        return []

    # Keep original files, including any manually uploaded attachment.  These
    # folders are entirely regenerated from source during the crawl.
    removed: list[str] = []
    for dirname in ("pdfs", "pngs", "result"):
        target = attachment_path / dirname
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(dirname)
    return removed


def _renumber_result_files(paths: list[str]) -> tuple[list[str], dict[str, str]]:
    """주어진 순서(paths)대로 실제 이미지 파일을 result 폴더 안에서
    01, 02, 03 ... 으로 물리적으로 rename 한다. Finder가 파일명 순으로
    이미지를 정렬하므로, 순서 변경/삭제 결과가 폴더에서도 그대로 보이게 한다.

    반환값: (새 상대경로 목록, {옛 상대경로: 새 상대경로} 매핑)
    존재하지 않는 파일은 건너뛴다. 충돌을 피하려고 임시 이름으로 먼저 바꾼 뒤
    최종 이름으로 다시 바꾸는 2단계 rename 을 사용한다."""
    existing = [(p, _to_abs(p)) for p in paths if p and os.path.exists(_to_abs(p))]
    if not existing:
        return [], {}

    # 1단계: 모두 고유한 임시 이름으로 변경
    staged: list[tuple[str, str, str]] = []  # (옛 상대경로, 임시 절대경로, 확장자)
    for idx, (rel, abs_path) in enumerate(existing):
        directory = os.path.dirname(abs_path)
        ext = os.path.splitext(abs_path)[1].lower() or ".jpg"
        tmp_path = os.path.join(directory, f".__reorder_tmp_{idx}{ext}")
        os.replace(abs_path, tmp_path)
        staged.append((rel, tmp_path, ext))

    # 2단계: 위치 기반 일련번호로 최종 변경
    new_paths: list[str] = []
    mapping: dict[str, str] = {}
    for position, (old_rel, tmp_path, ext) in enumerate(staged, start=1):
        directory = os.path.dirname(tmp_path)
        final_path = os.path.join(directory, f"{position:02d}{ext}")
        os.replace(tmp_path, final_path)
        new_rel = _to_rel(final_path)
        new_paths.append(new_rel)
        mapping[old_rel] = new_rel
    return new_paths, mapping


app = Flask(__name__)


# The dashboard is reachable through a public Funnel URL. Do not turn this
# into a general-purpose shell: every executable below is deliberately fixed.
ADMIN_OPERATION_COMMANDS: dict[str, list[tuple[str, list[str]]]] = {
    "status": [
        ("서비스", ["systemctl", "is-active", "cnu-info-web.service", "cnu-info-monitor.service"]),
        ("코드 버전", ["git", "-C", BASE_DIR, "rev-parse", "--short", "HEAD"]),
        ("디스크", ["df", "-h", BASE_DIR]),
    ],
    "logs": [
        # Keep both services in one journal query. Running two separate queries
        # looked newest-first within each block, but not across the dashboard.
        # -r makes the globally newest event the first displayed line.
        (
            "통합 최근 로그 (최신순)",
            [
                "journalctl",
                "-r",
                "-u",
                "cnu-info-web.service",
                "-u",
                "cnu-info-monitor.service",
                "-n",
                "120",
                "--no-pager",
            ],
        ),
    ],
    "refresh_crawler": [
        (
            "새로고침 신호 전송",
            ["sudo", "-n", "systemctl", "kill", "-s", "USR1", "cnu-info-monitor.service"],
        ),
        ("크롤러 상태", ["systemctl", "is-active", "cnu-info-monitor.service"]),
    ],
}


def _run_admin_operation(operation: str) -> tuple[bool, str]:
    commands = ADMIN_OPERATION_COMMANDS.get(operation)
    if not commands:
        return False, "허용되지 않은 작업입니다."

    chunks: list[str] = []
    success = True
    for label, command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            output = (completed.stdout + completed.stderr).strip() or "(출력 없음)"
            chunks.append(f"$ {label}\n{output}")
            if completed.returncode != 0:
                success = False
        except subprocess.TimeoutExpired:
            chunks.append(f"$ {label}\n시간 초과 (20초)")
            success = False
        except OSError as exc:
            chunks.append(f"$ {label}\n실행 실패: {exc}")
            success = False

    return success, "\n\n".join(chunks)[-20000:]


class DashboardRequestHandler(WSGIRequestHandler):
    """Keep the terminal readable while the dashboard loads many previews."""

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # A board page can request dozens of /media previews. They are expected
        # browser background traffic, unlike the dashboard/API requests worth
        # showing in the terminal.
        if self.path.split("?", 1)[0].startswith("/media/"):
            return
        super().log_request(code, size)


def _load_json(path: str) -> Any:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def load_notice_dict() -> dict[str, dict]:
    raw = _load_json(NOTICE_DB_PATH)
    result: dict[str, dict] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item.setdefault("notice_key", str(key))
            if "attachment_dir" in item:
                item["attachment_dir"] = _to_rel(item.get("attachment_dir"))
            image_result = item.get("image_result")
            if isinstance(image_result, dict):
                if "result_dir" in image_result:
                    image_result["result_dir"] = _to_rel(image_result.get("result_dir"))
                if "generated_images" in image_result:
                    image_result["generated_images"] = [
                        _to_rel(path)
                        for path in image_result.get("generated_images") or []
                    ]
                if "thumbnail_texts" in image_result and isinstance(image_result.get("thumbnail_texts"), dict):
                    normalized_texts: dict[str, str] = {}
                    for k, v in (image_result.get("thumbnail_texts") or {}).items():
                        if not isinstance(k, str):
                            continue
                        if not isinstance(v, str):
                            continue
                        normalized_texts[_to_rel(k)] = v
                    image_result["thumbnail_texts"] = normalized_texts
                if "skipped_images" in image_result:
                    image_result["skipped_images"] = [
                        _to_rel(path)
                        for path in image_result.get("skipped_images") or []
                    ]
            result[str(key)] = item
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                board_id = item.get("board_id") or "default"
                notice_id = item.get("id")
                notice_key = item.get("notice_key") or f"{board_id}::{notice_id}"
                item = dict(item)
                item.setdefault("notice_key", notice_key)
                if "attachment_dir" in item:
                    item["attachment_dir"] = _to_rel(item.get("attachment_dir"))
                image_result = item.get("image_result")
                if isinstance(image_result, dict):
                    if "result_dir" in image_result:
                        image_result["result_dir"] = _to_rel(
                            image_result.get("result_dir")
                        )
                    if "generated_images" in image_result:
                        image_result["generated_images"] = [
                            _to_rel(path)
                            for path in image_result.get("generated_images") or []
                        ]
                    if "thumbnail_texts" in image_result and isinstance(image_result.get("thumbnail_texts"), dict):
                        normalized_texts: dict[str, str] = {}
                        for k, v in (image_result.get("thumbnail_texts") or {}).items():
                            if not isinstance(k, str):
                                continue
                            if not isinstance(v, str):
                                continue
                            normalized_texts[_to_rel(k)] = v
                        image_result["thumbnail_texts"] = normalized_texts
                    if "skipped_images" in image_result:
                        image_result["skipped_images"] = [
                            _to_rel(path)
                            for path in image_result.get("skipped_images") or []
                        ]
                result[str(notice_key)] = item
    return result


def load_notices() -> list[dict]:
    notice_dict = load_notice_dict()
    notices = list(notice_dict.values())

    def _sort_key(item: dict) -> tuple:
        date_str = item.get("date") or ""
        try:
            parsed = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except Exception:
            # 날짜가 비어 있거나 형식이 달라도 대시보드 전체가 실패하지 않도록
            # 정렬 가능한 별도 값으로 처리한다. ``datetime.min.timestamp()``는
            # macOS에서 지원 범위를 벗어나 ValueError를 낼 수 있다.
            return (
                1,
                0,
                item.get("board_name", item.get("board_id", "")),
                item.get("title", ""),
            )
        return (
            0,
            -parsed.toordinal(),
            item.get("board_name", item.get("board_id", "")),
            item.get("title", ""),
        )

    return sorted(notices, key=_sort_key)


def load_links() -> dict[str, dict]:
    raw = _load_json(LINKS_PATH)
    if isinstance(raw, dict):
        normalized: dict[str, dict] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            if "attachments_dir" in item:
                item["attachments_dir"] = _to_rel(item.get("attachments_dir"))
            if "attachment_dir" in item:
                item["attachment_dir"] = _to_rel(item.get("attachment_dir"))
            normalized[str(key)] = item
        return normalized
    if isinstance(raw, list):
        result: dict[str, dict] = {}
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                entry = dict(item)
                if "attachments_dir" in entry:
                    entry["attachments_dir"] = _to_rel(entry.get("attachments_dir"))
                if "attachment_dir" in entry:
                    entry["attachment_dir"] = _to_rel(entry.get("attachment_dir"))
                result[str(entry["id"])] = entry
        return result
    return {}


TEMPLATE = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>CNU Notice Dashboard</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Noto Sans KR', sans-serif; margin: 0; padding: 0; background: #f5f6f8; color: #1f2933; }
        header { background: #111827; color: #f9fafb; padding: 20px 30px; }
        header h1 { margin: 0; font-size: 26px; }
        .container { max-width: 1100px; margin: 30px auto; padding: 0 20px 40px; }
        .meta { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
        .meta span { background: #fff; padding: 10px 16px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); font-size: 14px; }
        .tabs { display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }
        .tab-btn { border: none; border-radius: 999px; padding: 10px 18px; background: #e5e7eb; color: #1f2933; cursor: pointer; font-size: 14px; transition: all 0.2s ease; }
        .tab-btn.active { background: #2563eb; color: #fff; box-shadow: 0 4px 12px rgba(37,99,235,0.25); }
        .tab-panel { display: none; }
        .tab-panel.active { display: block; }
        .card { background: #fff; border-radius: 12px; box-shadow: 0 1px 4px rgba(15,23,42,0.12); padding: 22px; margin-bottom: 24px; }
        .page { display: none; }
        .page.active { display: block; }
        .card h2 { margin-top: 0; font-size: 20px; color: #0f172a; }
        .info-line { display: flex; gap: 10px; font-size: 14px; color: #4b5563; margin-bottom: 12px; flex-wrap: wrap; }
        .info-line a { color: #2563eb; text-decoration: none; }
        .info-line a:hover { text-decoration: underline; }
        .btn { display: inline-flex; align-items: center; gap: 6px; margin-right: 10px; background: #2563eb; color: #fff; border: none; border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 14px; }
        .btn.secondary { background: #6b7280; }
        .btn.danger { background: #dc2626; }
        .btn[disabled] { opacity: 0.5; cursor: not-allowed; }
        textarea { width: 100%; min-height: 120px; font-size: 14px; padding: 10px; border-radius: 8px; border: 1px solid #d1d5db; resize: vertical; background: #f9fafb; }
        .upload-section { border: 1px dashed #cbd5f5; border-radius: 10px; padding: 14px; background: #eef2ff; margin-bottom: 14px; }
        .upload-title { font-size: 14px; font-weight: 600; color: #1d4ed8; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
        .upload-dropzone { border: 2px dashed #93c5fd; border-radius: 10px; padding: 18px; text-align: center; font-size: 13px; color: #1f2933; background: #fff; transition: border-color .2s ease, background .2s ease; cursor: pointer; }
        .upload-dropzone.dragover { border-color: #2563eb; background: #e0ecff; }
        .upload-status { margin-top: 8px; font-size: 12px; color: #1f2933; display: none; }
        .upload-section button { margin-top: 10px; }
        .preview-section { margin-top: 16px; }
        .preview-title { font-size: 14px; font-weight: 600; color: #1f2937; margin-bottom: 10px; }
        .preview-grid { display: flex; gap: 12px; flex-wrap: wrap; }
        .preview-item { text-align: center; width: 180px; cursor: grab; user-select: none; position: relative; }
        .preview-item.dragging { opacity: 0.6; cursor: grabbing; }
        .preview-item button.delete-btn { position: absolute; top: 6px; right: 6px; background: rgba(239,68,68,0.9); border: none; color: #fff; border-radius: 50%; width: 24px; height: 24px; cursor: pointer; font-size: 14px; }
        .preview-grid img { width: 100%; border-radius: 10px; box-shadow: 0 1px 3px rgba(15,23,42,0.18); object-fit: cover; }
        .preview-grid span { font-size: 12px; color: #4b5563; display: block; margin-top: 6px; word-break: break-all; }
        .thumb-edit { margin-top: 12px; display: grid; gap: 10px; width: 100%; }
        .thumb-field { width: 100%; min-height: 44px; padding: 12px 14px; border: 1px solid #e5e7eb; border-radius: 10px; font-size: 14px; color: #111827; outline: none; background: #fff; box-sizing: border-box; }
        .thumb-field:focus { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37,99,235,0.15); }
        .thumb-save { background: #111827; color: #fff; border: none; border-radius: 10px; padding: 12px 14px; font-size: 14px; cursor: pointer; }
        .images { font-size: 14px; color: #374151; margin-top: 8px; }
        .status { margin-top: 12px; font-size: 13px; color: #6b7280; }
        .pagination { display: flex; gap: 8px; margin-top: 20px; flex-wrap: wrap; }
        .page-btn { border: none; border-radius: 8px; padding: 8px 12px; background: #e5e7eb; color: #1f2933; cursor: pointer; font-size: 12px; }
        .page-btn.active { background: #2563eb; color: #fff; }
        .page-info { font-size: 13px; color: #6b7280; display: flex; align-items: center; gap: 8px; }
        .toast { position: fixed; right: 20px; bottom: 20px; background: rgba(17,24,39,0.9); color: #fff; padding: 12px 18px; border-radius: 8px; opacity: 0; transition: opacity .3s ease; pointer-events: none; }
        .toast.show { opacity: 1; }
        .admin-panel { margin: 0 0 24px; padding: 18px; border-radius: 12px; background: #111827; color: #e5e7eb; box-shadow: 0 1px 4px rgba(15,23,42,0.2); }
        .admin-panel h2 { margin: 0 0 6px; font-size: 17px; color: #fff; }
        .admin-panel p { margin: 0 0 12px; color: #cbd5e1; font-size: 13px; }
        .admin-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
        .admin-actions button { border: 1px solid #475569; border-radius: 7px; background: #1e293b; color: #f8fafc; padding: 8px 11px; cursor: pointer; font-size: 13px; }
        .admin-actions button:hover { background: #334155; }
        .admin-actions button:disabled { opacity: .55; cursor: wait; }
        #admin-output { margin: 0; min-height: 100px; max-height: 360px; overflow: auto; white-space: pre-wrap; background: #020617; border: 1px solid #334155; border-radius: 8px; padding: 12px; color: #d1fae5; font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
        @media (max-width: 768px) {
            header { padding: 18px; }
            .container { padding: 0 14px 30px; }
            .tabs { gap: 8px; }
            .tab-btn { padding: 8px 14px; font-size: 13px; }
            .pagination { gap: 4px; }
            .page-btn { padding: 6px 10px; }
        }
    </style>
</head>
<body>
    <header>
        <h1>충남대 공지 대시보드</h1>
    </header>
    <div class="container">
        <div class="meta">
            <span>총 게시물: {{ total }}건</span>
            <span>이미지 생성 완료: {{ with_images }}건</span>
            <span>최근 업데이트: {{ last_updated }}</span>
        </div>
        <section class="admin-panel" aria-label="서버 운영 도구">
            <h2>서버 운영</h2>
            <p>허용된 작업만 실행됩니다. 임의 명령어 실행 기능은 제공하지 않습니다.</p>
            <div class="admin-actions">
                <button type="button" data-admin-operation="status">상태 확인</button>
                <button type="button" data-admin-operation="logs">최근 로그 (최신순)</button>
                <button type="button" data-admin-operation="refresh_crawler">새로고침 (r)</button>
            </div>
            <pre id="admin-output">상태 확인을 누르면 서버 정보를 표시합니다.</pre>
        </section>
        {% if board_groups %}
        <div class="tabs" id="tabs">
            {% for group in board_groups %}
            <button class="tab-btn {% if loop.first %}active{% endif %}" data-target="tab-{{ group.board_id }}">
                {{ group.board_name }} ({{ group.count }})
            </button>
            {% endfor %}
        </div>
        {% for group in board_groups %}
        <div
            class="tab-panel {% if loop.first %}active{% endif %}"
            id="tab-{{ group.board_id }}"
            data-pages="{{ group.page_count }}"
            data-board="{{ group.board_id }}"
        >
            {% for page_idx in group.page_range %}
            <div class="page {% if page_idx == 0 %}active{% endif %}" data-page="{{ page_idx }}" data-loaded="{% if page_idx == 0 %}true{% else %}false{% endif %}">
                {% if page_idx == 0 %}
                {{ group.initial_page_html | safe }}
                {% endif %}
            </div>
            {% endfor %}
            {% if group.page_count > 1 %}
            <div class="pagination" data-board="{{ group.board_id }}">
                <div class="page-info">
                    페이지 <span class="current-page">1</span> / {{ group.page_count }}
                </div>
                {% for page_idx in group.page_range %}
                <button class="page-btn {% if page_idx == 0 %}active{% endif %}" data-page="{{ page_idx }}">
                    {{ page_idx + 1 }}
                </button>
                {% endfor %}
            </div>
            {% endif %}
        </div>
        {% endfor %}
        {% else %}
        <p>표시할 공지가 없습니다. 모니터링 스크립트가 실행 중인지 확인하세요.</p>
        {% endif %}
    </div>
    <div id="toast" class="toast"></div>
    <script>
        function copyText(text) {
            if (!text) {
                showToast("복사할 내용이 없습니다.");
                return;
            }
            navigator.clipboard.writeText(text).then(function() {
                showToast("복사 완료!");
            }).catch(function(err) {
                console.error(err);
                showToast("복사 실패");
            });
        }
        function showToast(message) {
            const toast = document.getElementById("toast");
            toast.textContent = message;
            toast.classList.add("show");
            setTimeout(() => toast.classList.remove("show"), 1800);
        }

        function runAdminOperation(operation, button) {
            const output = document.getElementById("admin-output");
            if (!output || !operation) return;
            document.querySelectorAll("[data-admin-operation]").forEach(item => item.disabled = true);
            output.textContent = "실행 중...";
            fetch(`/admin/operations/${operation}`, {
                method: "POST",
                headers: { "X-CNU-Admin-Action": "run" },
            })
                .then(response => response.json())
                .then(data => {
                    output.textContent = data.output || data.error || "출력이 없습니다.";
                    if (!data.success) showToast(data.error || "작업이 실패했습니다.");
                })
                .catch(err => {
                    console.error(err);
                    output.textContent = "작업 요청 중 오류가 발생했습니다.";
                    showToast("서버 작업 실패");
                })
                .finally(() => document.querySelectorAll("[data-admin-operation]").forEach(item => item.disabled = false));
        }
        function openFolder(noticeKey) {
            fetch(`/open-folder/${noticeKey}`)
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(data.message || "폴더를 열었습니다.");
                    } else {
                        showToast(data.error || "폴더를 열 수 없습니다.");
                    }
                })
                .catch(err => {
                    console.error(err);
                    showToast("폴더 열기 실패");
                });
        }
        function recrawlNotice(noticeKey) {
            if (!confirm("이 공지를 다시 크롤링하시겠습니까?")) {
                return;
            }
            showToast("재크롤링 중...");
            fetch(`/recrawl/${noticeKey}`)
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(data.message || "재크롤링 완료!");
                        setTimeout(() => location.reload(), 1500);
                    } else {
                        showToast(data.error || "재크롤링 실패");
                    }
                })
                .catch(err => {
                    console.error(err);
                    showToast("재크롤링 실패");
                });
        }

        function summarizeNoticeWithAI(noticeKey) {
            if (!confirm("원문을 바꾸지 않고 AI 정리본을 생성하시겠습니까?\\n첨부파일과 이미지는 다시 크롤링하지 않습니다.")) {
                return;
            }
            showToast("AI가 본문을 정리 중...");
            fetch(`/ai-summarize/${noticeKey}`, { method: "POST" })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(data.message || "AI 본문 정리가 완료되었습니다.");
                        setTimeout(() => location.reload(), 1200);
                    } else {
                        showToast(data.error || "AI 본문 정리에 실패했습니다.");
                    }
                })
                .catch(err => {
                    console.error(err);
                    showToast("AI 본문 정리에 실패했습니다.");
                });
        }

        function deleteNotice(noticeKey) {
            if (!noticeKey) return;
            if (!confirm("이 공지와 첨부파일, 생성 이미지를 모두 삭제하시겠습니까?\\n이 작업은 되돌릴 수 없습니다.")) {
                return;
            }
            showToast("공지 삭제 중...");
            fetch(`/delete-notice/${noticeKey}`, { method: "POST" })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(data.message || "공지를 삭제했습니다.");
                        setTimeout(() => location.reload(), 700);
                    } else {
                        showToast(data.error || "공지 삭제 실패");
                    }
                })
                .catch(err => {
                    console.error(err);
                    showToast("공지 삭제 실패");
                });
        }

        function uploadFiles(noticeKey, files, statusEl) {
            if (!files || files.length === 0) {
                return;
            }
            const formData = new FormData();
            Array.from(files).forEach(file => formData.append("files", file));
            if (statusEl) {
                statusEl.textContent = "업로드 중...";
                statusEl.style.display = "block";
            }
            fetch(`/upload-images/${noticeKey}`, {
                method: "POST",
                body: formData,
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(data.message || "이미지를 업로드했습니다.");
                        setTimeout(() => location.reload(), 1500);
                    } else {
                        if (statusEl) {
                            statusEl.textContent = data.error || "업로드 실패";
                        }
                        showToast(data.error || "업로드 실패");
                    }
                })
                .catch(err => {
                    console.error(err);
                    if (statusEl) {
                        statusEl.textContent = "업로드 중 오류가 발생했습니다.";
                        statusEl.style.display = "block";
                    }
                    showToast("업로드 실패");
                });
        }

        function setupUploadZones() {
            const sections = document.querySelectorAll(".upload-section");
            sections.forEach(section => {
                if (section.dataset.initialized === "true") return;
                section.dataset.initialized = "true";
                const noticeKey = section.dataset.notice;
                const dropzone = section.querySelector(".upload-dropzone");
                const input = section.querySelector("input[type=file]");
                const statusEl = section.querySelector(".upload-status");

                const handleFiles = fileList => {
                    if (!noticeKey || !fileList || fileList.length === 0) return;
                    uploadFiles(noticeKey, fileList, statusEl);
                    if (input) {
                        input.value = "";
                    }
                };

                if (dropzone) {
                    dropzone.addEventListener("click", () => input?.click());
                    dropzone.addEventListener("dragover", event => {
                        event.preventDefault();
                        dropzone.classList.add("dragover");
                    });
                    dropzone.addEventListener("dragleave", event => {
                        event.preventDefault();
                        dropzone.classList.remove("dragover");
                    });
                    dropzone.addEventListener("drop", event => {
                        event.preventDefault();
                        dropzone.classList.remove("dragover");
                        handleFiles(event.dataTransfer?.files);
                    });
                }

                if (input) {
                    input.addEventListener("change", event => {
                        handleFiles(event.target.files);
                    });
                }
            });
        }

        function deleteImage(noticeKey, imagePath) {
            if (!noticeKey || !imagePath) return;
            if (!confirm("이 이미지를 삭제하시겠습니까?")) {
                return;
            }
            showToast("이미지 삭제 중...");
            fetch(`/delete-image/${noticeKey}`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ image: imagePath }),
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(data.message || "삭제되었습니다.");
                        setTimeout(() => location.reload(), 1000);
                    } else {
                        showToast(data.error || "삭제 실패");
                    }
                })
                .catch(err => {
                    console.error(err);
                    showToast("삭제 실패");
                });
        }

        function getGridOrder(grid) {
            return Array.from(grid.querySelectorAll(".preview-item"))
                .map(item => item.dataset.path)
                .filter(Boolean);
        }

        function persistImageOrder(noticeKey, order) {
            if (!noticeKey || !order || order.length === 0) {
                return;
            }
            fetch(`/reorder-images/${noticeKey}`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ order }),
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(data.message || "이미지 순서를 저장했습니다.");
                        // 파일명이 실제로 바뀌므로 화면을 새로고침해 동기화한다.
                        setTimeout(() => location.reload(), 800);
                    } else {
                        showToast(data.error || "이미지 순서를 저장하지 못했습니다.");
                    }
                })
                .catch(err => {
                    console.error(err);
                    showToast("이미지 순서 저장 실패");
                });
        }

        function setupDragAndDrop() {
            const grids = document.querySelectorAll(".preview-grid[data-notice]");
            grids.forEach(grid => {
                if (grid.dataset.initialized === "true") return;
                grid.dataset.initialized = "true";
                const noticeKey = grid.dataset.notice;
                let draggedItem = null;
                let startOrder = [];

                grid.addEventListener("dragstart", event => {
                    if (event.target && event.target.closest("input, textarea, button")) {
                        return;
                    }
                    const item = event.target.closest(".preview-item");
                    if (!item) return;
                    draggedItem = item;
                    startOrder = getGridOrder(grid);
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", item.dataset.path || "");
                    item.classList.add("dragging");
                });

                grid.addEventListener("dragenter", event => {
                    if (!draggedItem) return;
                    event.preventDefault();
                });

                grid.addEventListener("dragover", event => {
                    if (!draggedItem) return;
                    event.preventDefault();
                    const target = event.target.closest(".preview-item");
                    if (!target || target === draggedItem) return;
                    const rect = target.getBoundingClientRect();
                    const shouldInsertAfter = event.clientY > rect.top + rect.height / 2;
                    grid.insertBefore(
                        draggedItem,
                        shouldInsertAfter ? target.nextSibling : target
                    );
                });

                grid.addEventListener("drop", event => {
                    if (!draggedItem) return;
                    event.preventDefault();
                    draggedItem.classList.remove("dragging");
                    const newOrder = getGridOrder(grid);
                    if (JSON.stringify(newOrder) !== JSON.stringify(startOrder)) {
                        persistImageOrder(noticeKey, newOrder);
                    }
                    draggedItem = null;
                });

                grid.addEventListener("dragend", () => {
                    if (draggedItem) {
                        draggedItem.classList.remove("dragging");
                        draggedItem = null;
                    }
                });
            });
        }

        function saveThumbHeader(noticeKey) {
            const wrap = document.querySelector(`.thumb-edit[data-notice="${noticeKey}"]`);
            if (!wrap) return;
            const titleInput = wrap.querySelector("input.thumb-title");
            const dateInput = wrap.querySelector("input.thumb-date");
            const title = titleInput ? (titleInput.value || "") : "";
            const date = dateInput ? (dateInput.value || "") : "";

            showToast("썸네일 적용 중...");
            fetch(`/update-thumbnail-header/${noticeKey}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title, date }),
            })
                .then(resp => resp.json())
                .then(data => {
                    if (data.success) {
                        showToast(data.message || "썸네일을 적용했습니다.");
                        setTimeout(() => location.reload(), 800);
                    } else {
                        showToast(data.error || "썸네일 적용 실패");
                    }
                })
                .catch(err => {
                    console.error(err);
                    showToast("썸네일 적용 실패");
                });
        }
        document.addEventListener("DOMContentLoaded", function() {
            const ACTIVE_TAB_KEY = "cnu_notice_dashboard_active_tab";
            const tabs = document.querySelectorAll(".tab-btn");
            const panels = document.querySelectorAll(".tab-panel");

            function activateTab(targetId) {
                if (!targetId) return false;
                const targetTab = Array.from(tabs).find(tab => tab.dataset.target === targetId);
                const targetPanel = document.getElementById(targetId);
                if (!targetTab || !targetPanel) return false;
                tabs.forEach(t => t.classList.remove("active"));
                panels.forEach(p => p.classList.remove("active"));
                targetTab.classList.add("active");
                targetPanel.classList.add("active");
                try {
                    localStorage.setItem(ACTIVE_TAB_KEY, targetId);
                } catch (err) {
                    console.error(err);
                }
                return true;
            }

            tabs.forEach(tab => {
                tab.addEventListener("click", () => {
                    activateTab(tab.dataset.target);
                });
            });

            try {
                const savedTab = localStorage.getItem(ACTIVE_TAB_KEY);
                if (savedTab) {
                    activateTab(savedTab);
                }
            } catch (err) {
                console.error(err);
            }

            panels.forEach(panel => {
                const pagination = panel.querySelector(".pagination");
                if (!pagination) return;
                const boardId = panel.dataset.board;
                const pageBtns = pagination.querySelectorAll(".page-btn");
                const pages = panel.querySelectorAll(".page");
                const currentPageSpan = pagination.querySelector(".current-page");
                pageBtns.forEach(btn => {
                    btn.addEventListener("click", () => {
                        const pageIdx = parseInt(btn.dataset.page, 10);
                        pageBtns.forEach(b => b.classList.remove("active"));
                        btn.classList.add("active");
                        pages.forEach(p => p.classList.remove("active"));
                        const selectedPage = panel.querySelector(`.page[data-page="${pageIdx}"]`);
                        if (selectedPage) {
                            const finishActivate = () => {
                                selectedPage.classList.add("active");
                                setupUploadZones();
                                setupDragAndDrop();
                            };
                            if (selectedPage.dataset.loaded !== "true") {
                                selectedPage.innerHTML = '<p>로딩 중...</p>';
                                fetch(`/board-page/${boardId}/${pageIdx}`)
                                    .then(response => response.json())
                                    .then(data => {
                                        if (data.success) {
                                            selectedPage.innerHTML = data.html || "";
                                            selectedPage.dataset.loaded = "true";
                                            finishActivate();
                                        } else {
                                            selectedPage.innerHTML = '<p>페이지를 불러오지 못했습니다.</p>';
                                        }
                                    })
                                    .catch(err => {
                                        console.error(err);
                                        selectedPage.innerHTML = '<p>페이지를 불러오지 못했습니다.</p>';
                                    });
                            } else {
                                finishActivate();
                            }
                        }
                        if (currentPageSpan) currentPageSpan.textContent = String(pageIdx + 1);
                    });
                });
            });
            setupUploadZones();
            setupDragAndDrop();
            document.querySelectorAll("[data-admin-operation]").forEach(button => {
                button.addEventListener("click", () => runAdminOperation(button.dataset.adminOperation, button));
            });
        });
    </script>
</body>
</html>
"""

NOTICE_CARDS_TEMPLATE = """
{% for notice in notices %}
<div class="card">
    <h2>{{ notice.title or '제목 없음' }}</h2>
    <div class="info-line">
        <span>📅 {{ notice.date or '날짜 정보 없음' }}</span>
        <span>🆔 {{ notice.id }}</span>
        {% if notice.url %}
            <a href="{{ notice.url }}" target="_blank">원본 게시물 보기</a>
        {% endif %}
    </div>
    <div>
        <button class="btn" onclick="copyText(`{{ notice.title or '' }}`)">제목 복사</button>
        <button class="btn" onclick="copyText(`{{ notice.display_content or notice.content or '' }}`)">본문 복사</button>
        {% if notice.image_result and notice.image_result.generated_images %}
            <a class="btn secondary" href="/download/{{ notice.notice_key }}" onclick="copyText(`{{ notice.display_content or notice.content or '' }}`); return true;">이미지 다운로드</a>
            <button class="btn secondary" onclick="copyText(`{{ notice.display_content or notice.content or '' }}`); openFolder('{{ notice.notice_key }}')">📁 Finder에서 열기</button>
        {% else %}
            <button class="btn secondary" disabled>이미지 없음</button>
        {% endif %}
        <button class="btn secondary" onclick="summarizeNoticeWithAI('{{ notice.notice_key }}')">✨ AI 본문 정리</button>
        <button class="btn secondary" onclick="recrawlNotice('{{ notice.notice_key }}')">🔄 재크롤링</button>
        <button class="btn danger" onclick="deleteNotice('{{ notice.notice_key }}')">🗑️ 공지 삭제</button>
    </div>
    <div class="upload-section" data-notice="{{ notice.notice_key }}">
        <div class="upload-title">이미지 추가 (JPG, PNG, PDF, HWP/HWPX 지원)</div>
        <div class="upload-dropzone">
            이 영역에 파일을 드래그하거나 클릭하여 선택하세요.
        </div>
        <input type="file" multiple accept=".jpg,.jpeg,.png,.pdf,.webp,.hwp,.hwpx" hidden>
        <div class="upload-status"></div>
    </div>
    {% if notice.preview_urls %}
    <div class="preview-section">
        <div class="preview-title">미리보기 ({{ notice.preview_urls|length }}장, 드래그하여 순서 변경)</div>
        <div class="preview-grid" data-notice="{{ notice.notice_key }}">
            {% for item in notice.preview_urls %}
            <div class="preview-item" draggable="true" data-path="{{ item.path|e }}">
                <button class="delete-btn" onclick="deleteImage('{{ notice.notice_key }}', '{{ item.path|e }}')" title="삭제">×</button>
                <img src="{{ item.url }}" alt="{{ item.name }}" loading="lazy">
                <span>{{ item.name }}</span>
                {% if loop.first %}
                <div class="thumb-edit" data-notice="{{ notice.notice_key }}">
                    <input class="thumb-field thumb-title" type="text" value="{{ notice.thumb_title_value or '' }}" placeholder="썸네일 제목(기본: 공지 제목)" data-notice="{{ notice.notice_key }}">
                    <input class="thumb-field thumb-date" type="text" value="{{ notice.thumb_date_value or '' }}" placeholder="썸네일 날짜(기본: 공지 날짜)" data-notice="{{ notice.notice_key }}">
                    <button class="thumb-save" onclick="saveThumbHeader('{{ notice.notice_key }}')">썸네일(01) 적용</button>
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>
    {% endif %}
    <textarea readonly>{{ notice.display_content or notice.content or '' }}</textarea>
    <div class="images">
        {% if notice.image_result and notice.image_result.generated_images %}
            생성된 이미지: {{ notice.image_result.generated_images|length }}장
            {% if notice.image_result.truncated %} (20장 제한으로 일부 생략){% endif %}
        {% else %}
            이미지가 아직 생성되지 않았습니다.
        {% endif %}
    </div>
    {% if notice.image_result and notice.image_result.result_dir %}
    <div class="status">
        결과 폴더: {{ notice.image_result.result_dir }}
    </div>
    {% endif %}
</div>
{% endfor %}
"""


def _prepare_notice_for_view(notice: dict) -> dict:
    notice = dict(notice)
    generated = notice.get("image_result", {}).get("generated_images") or []
    image_result = notice.get("image_result") or {}
    if not isinstance(image_result, dict):
        image_result = {}
    thumb_title_override = image_result.get("thumbnail_title")
    if not isinstance(thumb_title_override, str):
        thumb_title_override = ""
    thumb_date_override = image_result.get("thumbnail_date")
    if not isinstance(thumb_date_override, str):
        thumb_date_override = ""

    preview_items = []
    for img_path in generated:
        if not img_path:
            continue
        abs_path = _to_abs(img_path)
        if not os.path.exists(abs_path):
            continue
        try:
            v = int(os.path.getmtime(abs_path))
        except Exception:
            v = 0
        preview_items.append(
            {
                "url": f"/media/{_to_rel(abs_path)}?v={v}",
                "name": os.path.basename(abs_path),
                "path": img_path,
            }
        )
    notice["preview_urls"] = preview_items
    notice["thumb_title_value"] = thumb_title_override or (notice.get("title") or "")
    notice["thumb_date_value"] = thumb_date_override or (notice.get("date") or "")

    board_id = notice.get("board_id") or "default"
    content = notice.get("content") or ""
    notice["display_content"] = make_display_content(board_id, content)
    return notice


def _build_board_groups(notices: list[dict], notices_per_page: int = 5) -> list[dict]:
    board_groups_dict: dict[str, dict] = {}
    for raw_notice in notices:
        notice = _prepare_notice_for_view(raw_notice)
        board_id = notice.get("board_id") or "default"
        board_name = notice.get("board_name") or board_id
        group = board_groups_dict.setdefault(
            board_id,
            {
                "board_id": board_id,
                "board_name": board_name,
                "notices": [],
                "count": 0,
            },
        )
        group["notices"].append(notice)

    board_groups = []
    for group in board_groups_dict.values():
        notices_list = group["notices"]
        pages: list[list[dict]] = []
        for idx in range(0, len(notices_list), notices_per_page):
            pages.append(notices_list[idx : idx + notices_per_page])
        initial_page = pages[0] if pages else []
        group["page_count"] = len(pages)
        group["page_range"] = list(range(len(pages)))
        group["count"] = len(notices_list)
        group["initial_page_html"] = render_template_string(
            NOTICE_CARDS_TEMPLATE,
            notices=initial_page,
        )
        board_groups.append(group)

    board_groups.sort(key=lambda x: (x.get("board_name") or x.get("board_id")))
    return board_groups


def _render_board_page_html(board_id: str, page_idx: int, notices_per_page: int = 5) -> str:
    notices = load_notices()
    prepared = [_prepare_notice_for_view(item) for item in notices if (item.get("board_id") or "default") == board_id]
    start = page_idx * notices_per_page
    end = start + notices_per_page
    page_items = prepared[start:end]
    return render_template_string(NOTICE_CARDS_TEMPLATE, notices=page_items)


@app.route("/")
def index():
    notices = load_notices()
    links = load_links()
    last_updated = "-"
    if links:
        last_times = [info.get("last_checked") for info in links.values() if info.get("last_checked")]
        if last_times:
            last_updated = max(last_times)

    with_images = 0
    for notice in notices:
        generated = notice.get("image_result", {}).get("generated_images") or []
        if generated:
            with_images += 1
    board_groups = _build_board_groups(notices)

    return render_template_string(
        TEMPLATE,
        notices=notices,
        board_groups=board_groups,
        total=len(notices),
        with_images=with_images,
        last_updated=last_updated,
    )


@app.route("/admin/operations/<operation>", methods=["POST"])
def run_admin_operation(operation: str):
    # A custom header prevents cross-site HTML form submissions from triggering
    # a server action. Nginx Basic Auth remains the primary access boundary.
    if request.headers.get("X-CNU-Admin-Action") != "run":
        return jsonify({"success": False, "error": "관리 작업 요청이 거부되었습니다."}), 403

    success, output = _run_admin_operation(operation)
    return jsonify({"success": success, "output": output}), (200 if success else 500)


@app.route("/board-page/<board_id>/<int:page_idx>")
def board_page(board_id: str, page_idx: int):
    html = _render_board_page_html(board_id, page_idx)
    return jsonify({"success": True, "html": html, "page": page_idx, "board_id": board_id})


@app.route("/delete-notice/<path:notice_key>", methods=["POST"])
def delete_notice(notice_key: str):
    """공지와 해당 공지의 첨부파일/생성 이미지를 모두 제거한다."""
    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    db_data = _load_json(NOTICE_DB_PATH)
    removed = False
    if isinstance(db_data, dict):
        if notice_key in db_data:
            del db_data[notice_key]
            removed = True
        else:
            for key, item in list(db_data.items()):
                if isinstance(item, dict) and item.get("notice_key") == notice_key:
                    del db_data[key]
                    removed = True
                    break
    elif isinstance(db_data, list):
        original_count = len(db_data)
        db_data = [
            item
            for item in db_data
            if not (
                isinstance(item, dict)
                and (
                    item.get("notice_key") == notice_key
                    or (
                        str(item.get("id")) == str(notice.get("id"))
                        and (item.get("board_id") or "default")
                        == (notice.get("board_id") or "default")
                    )
                )
            )
        ]
        removed = len(db_data) != original_count
    else:
        return jsonify({"success": False, "error": "데이터 파일을 읽을 수 없습니다."}), 500

    if not removed:
        return jsonify({"success": False, "error": "데이터 파일에서 항목을 찾을 수 없습니다."}), 404

    # 공지별 첨부 폴더만 삭제한다. DB에 잘못된 경로가 들어 있어도 프로젝트의
    # attachments 밖은 절대 지우지 않는다.
    board_id = str(notice.get("board_id") or "default")
    notice_id = str(notice.get("id") or notice_key.split("::")[-1])
    attachment_dir = notice.get("attachment_dir") or os.path.join(
        "attachments", board_id, notice_id
    )
    attachments_root = (BASE_PATH / "attachments").resolve()
    attachment_path = Path(_to_abs(str(attachment_dir))).resolve()
    if attachment_path != attachments_root and attachments_root in attachment_path.parents:
        try:
            if attachment_path.exists():
                shutil.rmtree(attachment_path)
        except Exception as exc:
            return jsonify({"success": False, "error": f"첨부파일 삭제 실패: {exc}"}), 500
    elif attachment_path.exists():
        return jsonify({"success": False, "error": "잘못된 첨부파일 경로입니다."}), 400

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)

        # 모니터가 다음 주기에 이미 크롤링된 링크를 다시 처리하거나, 삭제 직전의
        # 작업이 완료되어 공지가 되살아나는 일을 막는다.
        links_data = _load_json(LINKS_PATH)
        if isinstance(links_data, dict):
            link = links_data.get(notice_key)
            if isinstance(link, dict):
                link["hidden"] = True
                links_data[notice_key] = link
                with open(LINKS_PATH, "w", encoding="utf-8") as fh:
                    json.dump(links_data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({"success": False, "error": f"데이터 저장 실패: {exc}"}), 500

    return jsonify({"success": True, "message": "공지와 첨부파일, 생성 이미지를 삭제했습니다."})


@app.route("/download/<path:notice_key>")
def download_images(notice_key: str):
    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        abort(404, description="해당 ID의 공지를 찾을 수 없습니다.")

    images = notice.get("image_result", {}).get("generated_images") or []
    if not images:
        abort(404, description="생성된 이미지가 없습니다.")

    folder_name = (
        notice.get("notice_key")
        or f"{notice.get('board_id', 'board')}::{notice.get('id', 'notice')}"
    ).replace("::", "_")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        position = 1
        for img_path in images:
            if not img_path:
                continue
            abs_path = _to_abs(img_path)
            if os.path.exists(abs_path):
                # 대시보드에서 변경한 순서가 파일명에도 반영되도록 위치 기반으로
                # 일련번호(01, 02, ...)를 부여한다. (원본 파일명을 그대로 쓰면
                # 인스타 업로드 시 파일명 정렬 때문에 순서변경이 무시된다.)
                ext = os.path.splitext(abs_path)[1].lower() or ".jpg"
                arcname = os.path.join(folder_name, f"{position:02d}{ext}")
                zf.write(abs_path, arcname)
                position += 1
    buffer.seek(0)
    filename = f"{folder_name}_images.zip"
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/media/<path:rel_path>")
def media(rel_path: str):
    safe_path = _to_abs(rel_path)
    if os.path.commonpath([str(BASE_PATH), safe_path]) != str(BASE_PATH):
        abort(403)
    if not os.path.exists(safe_path):
        abort(404)
    return send_file(safe_path)


@app.route("/open-folder/<path:notice_key>")
def open_folder(notice_key: str):
    """이미지 폴더를 Finder에서 열기"""
    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    result_dir = notice.get("image_result", {}).get("result_dir")
    if not result_dir:
        return jsonify({"success": False, "error": "이미지 폴더가 없습니다."}), 404

    abs_path = _to_abs(result_dir)
    if not os.path.exists(abs_path):
        return jsonify({"success": False, "error": "폴더가 존재하지 않습니다."}), 404

    try:
        # macOS Finder에서 폴더 열기
        subprocess.run(["open", abs_path], check=True)
        return jsonify({"success": True, "message": "Finder에서 폴더를 열었습니다."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/ai-summarize/<path:notice_key>", methods=["POST"])
def summarize_notice_with_ai(notice_key: str):
    """Create a manual AI rewrite without re-crawling files or images."""
    if not MANUAL_AI_AVAILABLE:
        return jsonify({"success": False, "error": "AI 본문 정리 모듈을 불러올 수 없습니다."}), 500

    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (item for item in notices.values() if item.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    raw_content = (notice.get("raw_content") or notice.get("content") or "").strip()
    if not raw_content:
        return jsonify({"success": False, "error": "정리할 본문이 없습니다."}), 400

    summary = _summarize_complex_content(
        title=(notice.get("title") or "").strip(),
        raw_content=raw_content,
    )
    if not summary:
        return jsonify({"success": False, "error": "AI 응답을 받지 못했습니다. API 설정을 확인해 주세요."}), 502

    updated_notice = dict(notice)
    updated_notice["raw_content"] = raw_content
    updated_notice["content"] = summary
    updated_notice["ai_summary"] = summary
    updated_notice["ai_summary_model"] = FACTCHAT_MODEL
    updated_notice["ai_summary_source"] = "manual"
    updated_notice["ai_summarized_at"] = datetime.now().isoformat()
    # A stored display override would hide the new content in the dashboard.
    updated_notice.pop("display_content", None)

    db_data = _load_json(NOTICE_DB_PATH)
    if isinstance(db_data, dict):
        db_data[notice_key] = updated_notice
    elif isinstance(db_data, list):
        for index, item in enumerate(db_data):
            if item.get("notice_key") == notice_key or item.get("id") == notice.get("id"):
                db_data[index] = updated_notice
                break
        else:
            db_data.append(updated_notice)
    else:
        db_data = {notice_key: updated_notice}

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        return jsonify({"success": False, "error": f"본문 저장 실패: {exc}"}), 500

    return jsonify(
        {
            "success": True,
            "message": "AI 본문 정리가 완료되었습니다.",
            "model": FACTCHAT_MODEL,
        }
    )


@app.route("/recrawl/<path:notice_key>")
def recrawl_notice(notice_key: str):
    """특정 공지를 다시 크롤링"""
    if not CRAWL_AVAILABLE:
        return jsonify({"success": False, "error": "크롤링 모듈을 불러올 수 없습니다."}), 500

    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    url = notice.get("url")
    if not url:
        return jsonify({"success": False, "error": "공지 URL이 없습니다."}), 404

    try:
        import requests

        # 기존 정보 가져오기
        notice_id = notice.get("id")
        board_id = notice.get("board_id")
        board_name = notice.get("board_name")
        board_url = notice.get("board_url")
        title_hint = notice.get("title")
        # 재크롤링 후에도 이미지가 "사라지지" 않도록 기존 이미지 결과/커스텀 필드 보존
        existing_image_result = notice.get("image_result")
        existing_attachment_dir = notice.get("attachment_dir")
        existing_display_content = notice.get("display_content")
        cleared_derived_dirs = _clear_recrawl_derived_files(existing_attachment_dir)

        # 재크롤링 실행
        # 기존 attachment_dir이 있으면 그 부모를 base로 사용해 동일한 폴더 구조 유지
        attachments_base_abs = _to_abs(DEFAULT_ATTACHMENTS_DIR)
        try:
            if existing_attachment_dir:
                existing_abs = _to_abs(existing_attachment_dir)
                parent = os.path.dirname(existing_abs)
                if parent and os.path.exists(parent):
                    attachments_base_abs = parent
            elif board_id:
                attachments_base_abs = _to_abs(os.path.join(DEFAULT_ATTACHMENTS_DIR, str(board_id)))
        except Exception:
            attachments_base_abs = _to_abs(DEFAULT_ATTACHMENTS_DIR)
        session = requests.Session()

        try:
            detail = crawl_notice_detail(
                url,
                notice_id=notice_id,
                session=session,
                download_attachments=True,
                attachments_dir=attachments_base_abs,
                fallback_index=0,
                title_hint=title_hint,
                board_id=board_id,
                board_name=board_name,
                board_url=board_url,
            )
        finally:
            session.close()

        if not detail:
            return jsonify({"success": False, "error": "크롤링에 실패했습니다."}), 500

        # 재크롤링 결과는 본문/첨부를 갱신하고, 변환된 첨부가 있으면 이미지도 재생성한다.
        if isinstance(detail, dict):
            # notice_key를 항상 유지 (dict형 DB에서 key로만 존재할 수 있음)
            detail.setdefault("notice_key", notice_key)
            if existing_attachment_dir:
                detail["attachment_dir"] = existing_attachment_dir
            refreshed_image_result = None
            if IMAGE_GEN_AVAILABLE and detail.get("attachment_dir"):
                try:
                    refreshed_image_result = generate_notice_images(
                        detail,
                        _to_abs(detail["attachment_dir"]),
                        max_images=20,
                    )
                    if isinstance(existing_image_result, dict):
                        for key in ("thumbnail_title", "thumbnail_date", "thumbnail_text"):
                            if existing_image_result.get(key):
                                refreshed_image_result[key] = existing_image_result[key]
                except Exception:
                    refreshed_image_result = None
            if refreshed_image_result:
                detail["image_result"] = refreshed_image_result
            elif existing_image_result:
                detail["image_result"] = existing_image_result
            if existing_display_content:
                detail["display_content"] = existing_display_content

        # notices_db.json 업데이트
        db_data = _load_json(NOTICE_DB_PATH)
        if isinstance(db_data, dict):
            db_data[notice_key] = detail
        elif isinstance(db_data, list):
            # 리스트 형식인 경우 기존 항목 찾아서 업데이트
            found = False
            for i, item in enumerate(db_data):
                if item.get("notice_key") == notice_key or item.get("id") == notice_id:
                    db_data[i] = detail
                    found = True
                    break
            if not found:
                db_data.append(detail)
        else:
            db_data = {notice_key: detail}

        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=2)

        return jsonify({
            "success": True,
            "message": "재크롤링이 완료되었습니다."
            + (f" 파생 파일({', '.join(cleared_derived_dirs)})을 새로 생성했습니다." if cleared_derived_dirs else ""),
            "notice": {
                "id": detail.get("id"),
                "title": detail.get("title"),
                "date": detail.get("date"),
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"오류 발생: {str(e)}"}), 500


@app.route("/upload-images/<path:notice_key>", methods=["POST"])
def upload_images(notice_key: str):
    """이미지를 업로드하고 결과를 재생성"""
    if not IMAGE_GEN_AVAILABLE:
        return jsonify({"success": False, "error": "이미지 생성 모듈을 사용할 수 없습니다."}), 500

    files = request.files.getlist("files")
    if not files:
        return jsonify({"success": False, "error": "업로드할 파일을 선택하세요."}), 400

    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    board_id = notice.get("board_id") or "default"
    notice_id = notice.get("id") or notice_key.split("::")[-1]

    attachment_dir_rel = notice.get("attachment_dir")
    if not attachment_dir_rel:
        attachment_dir_rel = os.path.join("attachments", board_id, notice_id)
        notice["attachment_dir"] = attachment_dir_rel

    attachment_dir_abs = _to_abs(attachment_dir_rel)
    original_root = _ensure_dir(os.path.join(attachment_dir_abs, "original"))
    pdfs_dir = _ensure_dir(os.path.join(attachment_dir_abs, "pdfs"))
    png_dir = _ensure_dir(os.path.join(attachment_dir_abs, "pngs"))
    manual_original_dir = _ensure_dir(os.path.join(original_root, "manual_uploads"))

    saved_any = False
    errors: list[str] = []

    for file_storage in files:
        filename = _sanitize_upload_filename(file_storage.filename)
        if not filename:
            continue

        stem, ext = os.path.splitext(filename)
        ext = ext.lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            errors.append(f"{filename}: 지원하지 않는 확장자입니다.")
            continue

        safe_stem = f"manual_{stem or 'upload'}"
        unique_original = _unique_path(manual_original_dir, safe_stem, ext)
        try:
            file_storage.save(unique_original)
            saved_any = True
        except Exception as exc:
            errors.append(f"{filename}: 저장 실패 ({exc})")
            continue

        try:
            if ext in {".jpg", ".jpeg", ".png", ".webp"}:
                dest_png = _unique_path(
                    png_dir,
                    os.path.splitext(os.path.basename(unique_original))[0],
                    ".png",
                )
                _save_image_as_png(unique_original, dest_png)
            elif ext == ".pdf":
                converted = _convert_pdf_to_pngs(
                    unique_original,
                    png_dir,
                    os.path.splitext(os.path.basename(unique_original))[0],
                )
                if not converted:
                    errors.append(f"{filename}: PDF를 이미지로 변환하지 못했습니다.")
            elif ext in HWP_EXTENSIONS:
                if convert_hwp_to_pdf is None:
                    errors.append(f"{filename}: HWP 변환 모듈을 사용할 수 없습니다.")
                    continue
                converted_pdf = _unique_path(
                    pdfs_dir,
                    os.path.splitext(os.path.basename(unique_original))[0],
                    ".pdf",
                )
                if not convert_hwp_to_pdf(unique_original, converted_pdf):
                    errors.append(f"{filename}: HWP를 PDF로 변환하지 못했습니다.")
                    continue
                converted = _convert_pdf_to_pngs(
                    converted_pdf,
                    png_dir,
                    os.path.splitext(os.path.basename(converted_pdf))[0],
                )
                if not converted:
                    errors.append(f"{filename}: 변환된 PDF를 이미지로 변환하지 못했습니다.")
        except Exception as exc:
            errors.append(f"{filename}: 변환 실패 ({exc})")

    if not saved_any:
        return jsonify({"success": False, "error": "저장된 파일이 없습니다.", "details": errors}), 400

    try:
        result = generate_notice_images(notice, attachment_dir_abs, max_images=20)
    except Exception as exc:
        return jsonify({"success": False, "error": f"이미지 생성 실패: {exc}"}), 500

    db_data = _load_json(NOTICE_DB_PATH)
    updated = False

    def _apply_update(entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        existing_thumb_text = None
        try:
            existing_thumb_text = (entry.get("image_result") or {}).get("thumbnail_text")
        except Exception:
            existing_thumb_text = None
        if isinstance(existing_thumb_text, str) and existing_thumb_text.strip():
            result["thumbnail_text"] = existing_thumb_text.strip()
        entry["image_result"] = result
        entry["attachment_dir"] = _to_rel(attachment_dir_abs)
        return True

    if isinstance(db_data, dict):
        entry = db_data.get(notice_key)
        if entry and _apply_update(entry):
            updated = True
        else:
            for candidate in db_data.values():
                if isinstance(candidate, dict) and candidate.get("notice_key") == notice_key:
                    if _apply_update(candidate):
                        updated = True
                        break
    elif isinstance(db_data, list):
        for item in db_data:
            if isinstance(item, dict) and (
                item.get("notice_key") == notice_key or str(item.get("id")) == notice_id
            ):
                if _apply_update(item):
                    updated = True
                    break
    else:
        return jsonify({"success": False, "error": "데이터 파일을 읽을 수 없습니다."}), 500

    if not updated:
        return jsonify({"success": False, "error": "데이터 파일에서 항목을 찾지 못했습니다."}), 404

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({"success": False, "error": f"결과 저장 실패: {exc}"}), 500

    message = "이미지를 업로드하고 다시 생성했습니다."
    if errors:
        message += f" (일부 파일 오류: {len(errors)}건)"
    return jsonify(
        {
            "success": True,
            "message": message,
            "errors": errors,
            "generated_images": result.get("generated_images", []),
        }
    )


@app.route("/reorder-images/<path:notice_key>", methods=["POST"])
def reorder_images(notice_key: str):
    """이미지 순서 재정렬"""
    payload = request.get_json(silent=True) or {}
    order = payload.get("order")
    if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
        return jsonify({"success": False, "error": "올바른 순서를 전달해주세요."}), 400

    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    generated = (notice.get("image_result") or {}).get("generated_images") or []
    if not generated:
        return jsonify({"success": False, "error": "정렬할 이미지가 없습니다."}), 400

    existing_set = set(generated)
    new_order: list[str] = []
    seen: set[str] = set()

    for path in order:
        if path in existing_set and path not in seen:
            new_order.append(path)
            seen.add(path)

    for path in generated:
        if path not in seen:
            new_order.append(path)
            seen.add(path)

    if not new_order:
        return jsonify({"success": False, "error": "유효한 이미지 순서를 찾을 수 없습니다."}), 400

    if new_order == generated:
        return jsonify({"success": True, "message": "이미지 순서가 이미 최신 상태입니다.", "order": new_order})

    # 실제 파일을 새 순서대로 물리적으로 rename → Finder에서도 순서가 반영된다.
    try:
        renamed_order, rename_map = _renumber_result_files(new_order)
    except Exception as exc:
        return jsonify({"success": False, "error": f"파일 이름 변경 실패: {exc}"}), 500
    if not renamed_order:
        return jsonify({"success": False, "error": "정렬할 이미지 파일을 찾을 수 없습니다."}), 400

    db_data = _load_json(NOTICE_DB_PATH)
    updated = False

    def _update_entry(entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        image_result = entry.setdefault("image_result", {})
        image_result["generated_images"] = renamed_order
        texts = image_result.get("thumbnail_texts")
        if isinstance(texts, dict) and rename_map:
            image_result["thumbnail_texts"] = {
                rename_map.get(k, k): v for k, v in texts.items()
            }
        return True

    if isinstance(db_data, dict):
        entry = db_data.get(notice_key)
        if entry and _update_entry(entry):
            updated = True
        else:
            for candidate in db_data.values():
                if isinstance(candidate, dict) and candidate.get("notice_key") == notice_key:
                    if _update_entry(candidate):
                        updated = True
                        break
    elif isinstance(db_data, list):
        for idx, item in enumerate(db_data):
            if not isinstance(item, dict):
                continue
            if item.get("notice_key") == notice_key or str(item.get("id")) == str(notice.get("id")):
                if _update_entry(item):
                    updated = True
                    break
    else:
        return jsonify({"success": False, "error": "데이터 파일을 읽을 수 없습니다."}), 500

    if not updated:
        return jsonify({"success": False, "error": "데이터 파일에서 항목을 찾을 수 없습니다."}), 404

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({"success": False, "error": f"파일 저장 실패: {exc}"}), 500

    return jsonify(
        {
            "success": True,
            "message": "이미지 순서를 저장했습니다.",
            "order": renamed_order,
        }
    )


@app.route("/delete-image/<path:notice_key>", methods=["POST"])
def delete_image(notice_key: str):
    payload = request.get_json(silent=True) or {}
    image_path = payload.get("image")
    if not image_path:
        return jsonify({"success": False, "error": "삭제할 이미지 경로가 없습니다."}), 400

    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    generated = (notice.get("image_result") or {}).get("generated_images") or []
    if image_path not in generated:
        return jsonify({"success": False, "error": "이미지를 목록에서 찾을 수 없습니다."}), 404

    abs_path = _to_abs(image_path)
    if os.path.commonpath([str(BASE_PATH), abs_path]) != str(BASE_PATH):
        return jsonify({"success": False, "error": "잘못된 경로입니다."}), 400

    if os.path.exists(abs_path):
        try:
            os.remove(abs_path)
        except Exception as exc:
            return jsonify({"success": False, "error": f"파일 삭제 실패: {exc}"}), 500

    remaining = [item for item in generated if item != image_path]

    # 남은 파일을 다시 01,02,... 로 연번 → Finder 정렬에서 빈 번호가 없도록.
    try:
        new_list, rename_map = _renumber_result_files(remaining)
    except Exception as exc:
        return jsonify({"success": False, "error": f"파일 이름 변경 실패: {exc}"}), 500

    db_data = _load_json(NOTICE_DB_PATH)
    updated = False

    def _apply(entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        image_result = entry.setdefault("image_result", {})
        image_result["generated_images"] = new_list
        texts = image_result.get("thumbnail_texts")
        if isinstance(texts, dict):
            texts.pop(image_path, None)
            if rename_map:
                image_result["thumbnail_texts"] = {
                    rename_map.get(k, k): v for k, v in texts.items()
                }
        return True

    if isinstance(db_data, dict):
        entry = db_data.get(notice_key)
        if entry and _apply(entry):
            updated = True
        else:
            for candidate in db_data.values():
                if isinstance(candidate, dict) and candidate.get("notice_key") == notice_key:
                    if _apply(candidate):
                        updated = True
                        break
    elif isinstance(db_data, list):
        for item in db_data:
            if isinstance(item, dict) and (item.get("notice_key") == notice_key or item.get("id") == notice.get("id")):
                if _apply(item):
                    updated = True
                    break
    else:
        return jsonify({"success": False, "error": "데이터 파일을 읽을 수 없습니다."}), 500

    if not updated:
        return jsonify({"success": False, "error": "데이터 파일에서 항목을 찾을 수 없습니다."}), 404

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({"success": False, "error": f"데이터 저장 실패: {exc}"}), 500

    return jsonify({"success": True, "message": "이미지를 삭제했습니다.", "order": new_list})


@app.route("/update-thumbnail-text/<path:notice_key>", methods=["POST"])
def update_thumbnail_text(notice_key: str):
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    if not isinstance(text, str):
        text = str(text)
    text = text.strip()
    if len(text) > 200:
        text = text[:200]

    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    db_data = _load_json(NOTICE_DB_PATH)
    updated = False

    def _apply(entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        image_result = entry.setdefault("image_result", {})
        if text:
            image_result["thumbnail_text"] = text
        else:
            if "thumbnail_text" in image_result:
                try:
                    del image_result["thumbnail_text"]
                except Exception:
                    pass
        return True

    if isinstance(db_data, dict):
        entry = db_data.get(notice_key)
        if entry and _apply(entry):
            updated = True
        else:
            for candidate in db_data.values():
                if isinstance(candidate, dict) and candidate.get("notice_key") == notice_key:
                    if _apply(candidate):
                        updated = True
                        break
    elif isinstance(db_data, list):
        for item in db_data:
            if isinstance(item, dict) and (
                item.get("notice_key") == notice_key or str(item.get("id")) == str(notice.get("id"))
            ):
                if _apply(item):
                    updated = True
                    break
    else:
        return jsonify({"success": False, "error": "데이터 파일을 읽을 수 없습니다."}), 500

    if not updated:
        return jsonify({"success": False, "error": "데이터 파일에서 항목을 찾을 수 없습니다."}), 404

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({"success": False, "error": f"데이터 저장 실패: {exc}"}), 500

    return jsonify({"success": True, "message": "텍스트를 저장했습니다."})


@app.route("/update-thumbnail-header/<path:notice_key>", methods=["POST"])
def update_thumbnail_header(notice_key: str):
    """생성된 01.jpg(썸네일) 안의 제목/날짜를 재생성해서 덮어쓰기"""
    if not IMAGE_GEN_AVAILABLE:
        return jsonify({"success": False, "error": "이미지 생성 모듈을 사용할 수 없습니다."}), 500

    payload = request.get_json(silent=True) or {}
    title = payload.get("title", "")
    date = payload.get("date", "")
    if not isinstance(title, str):
        title = str(title)
    if not isinstance(date, str):
        date = str(date)
    title = title.strip()
    date = date.strip()
    if len(title) > 120:
        title = title[:120]
    if len(date) > 40:
        date = date[:40]

    notices = load_notice_dict()
    notice = notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    # attachment_dir 확보 (없으면 기본 규칙으로 생성)
    board_id = notice.get("board_id") or "default"
    notice_id = notice.get("id") or notice_key.split("::")[-1]
    attachment_dir_rel = notice.get("attachment_dir")
    if not attachment_dir_rel:
        attachment_dir_rel = os.path.join("attachments", str(board_id), str(notice_id))
        notice["attachment_dir"] = attachment_dir_rel
    attachment_dir_abs = _to_abs(attachment_dir_rel)
    os.makedirs(attachment_dir_abs, exist_ok=True)

    # DB 업데이트 + 01.jpg 재생성
    db_data = _load_json(NOTICE_DB_PATH)
    updated = False
    out_rel = ""

    def _apply(entry: dict) -> bool:
        nonlocal out_rel
        if not isinstance(entry, dict):
            return False
        image_result = entry.setdefault("image_result", {})
        # override 저장 (비우면 제거)
        if title:
            image_result["thumbnail_title"] = title
        else:
            if "thumbnail_title" in image_result:
                try:
                    del image_result["thumbnail_title"]
                except Exception:
                    pass
        if date:
            image_result["thumbnail_date"] = date
        else:
            if "thumbnail_date" in image_result:
                try:
                    del image_result["thumbnail_date"]
                except Exception:
                    pass

        # 실제 파일 재생성 (01.jpg)
        out_rel = generate_notice_thumbnail_header(
            entry,
            attachment_dir_abs,
            title=title or None,
            date_text=date or None,
            out_filename="01.jpg",
        )

        # generated_images에 01.jpg가 없으면 추가(최상단)
        image_result.setdefault("generated_images", [])
        if isinstance(image_result.get("generated_images"), list):
            gen = image_result.get("generated_images") or []
            if out_rel and out_rel not in gen:
                image_result["generated_images"] = [out_rel] + [p for p in gen if isinstance(p, str)]
        # result_dir도 유지/세팅
        image_result.setdefault("result_dir", _to_rel(os.path.join(attachment_dir_abs, "result")))
        return True

    if isinstance(db_data, dict):
        entry = db_data.get(notice_key)
        if entry and _apply(entry):
            updated = True
        else:
            for candidate in db_data.values():
                if isinstance(candidate, dict) and candidate.get("notice_key") == notice_key:
                    if _apply(candidate):
                        updated = True
                        break
    elif isinstance(db_data, list):
        for item in db_data:
            if isinstance(item, dict) and (
                item.get("notice_key") == notice_key or str(item.get("id")) == str(notice.get("id"))
            ):
                if _apply(item):
                    updated = True
                    break
    else:
        return jsonify({"success": False, "error": "데이터 파일을 읽을 수 없습니다."}), 500

    if not updated:
        return jsonify({"success": False, "error": "데이터 파일에서 항목을 찾을 수 없습니다."}), 404

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({"success": False, "error": f"데이터 저장 실패: {exc}"}), 500

    v = 0
    try:
        if out_rel:
            v = int(os.path.getmtime(_to_abs(out_rel)))
    except Exception:
        v = 0

    return jsonify(
        {
            "success": True,
            "message": "썸네일(01.jpg)을 재생성했습니다.",
            "image": out_rel,
            "v": v,
        }
    )


# ---------------------------------------------------------------------------
# 모바일 앱용 JSON API
#
# nginx가 /api/ 경로는 Basic Auth 없이 통과시키는 대신, 아래 라우트는 모두
# .env의 APP_API_KEY와 일치하는 X-API-Key 헤더를 요구한다.
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_PATH / ".env")
except Exception:
    pass


def _require_api_key() -> None:
    expected = (os.environ.get("APP_API_KEY") or "").strip()
    if not expected:
        abort(503)
    provided = request.headers.get("X-API-Key") or ""
    if not secrets.compare_digest(provided, expected):
        abort(401)


def _api_notice_summary(notice: dict) -> dict:
    generated = (notice.get("image_result") or {}).get("generated_images") or []
    return {
        "notice_key": notice.get("notice_key") or "",
        "title": notice.get("title") or "",
        "board_id": notice.get("board_id") or "default",
        "board_name": notice.get("board_name") or notice.get("board_id") or "",
        "date": notice.get("date") or "",
        "crawled_at": notice.get("crawled_at") or "",
        "url": notice.get("url") or "",
        "image_count": len([p for p in generated if p]),
        "posted": bool(notice.get("posted")),
        "posted_at": notice.get("posted_at") or "",
        # 인스타그램 대조 결과. ig_permalink가 있으면 실제 게시물이 확인된 것이다.
        "ig_permalink": notice.get("ig_permalink") or "",
        "ig_match": notice.get("ig_match") or "",
        "ig_checked_at": notice.get("ig_checked_at") or "",
    }


def _find_notice(notice_key: str) -> dict | None:
    notices = load_notice_dict()
    return notices.get(str(notice_key)) or next(
        (v for v in notices.values() if v.get("notice_key") == notice_key),
        None,
    )


@app.route("/api/notices")
def api_notices():
    _require_api_key()
    notices = load_notices()
    since = (request.args.get("since") or "").strip()
    unposted_only = request.args.get("unposted") in ("1", "true", "yes")
    limit = request.args.get("limit", type=int) or 0

    items = []
    for notice in notices:
        summary = _api_notice_summary(notice)
        if since and (summary["crawled_at"] or "") <= since:
            continue
        if unposted_only and summary["posted"]:
            continue
        items.append(summary)
        if limit and len(items) >= limit:
            break
    return jsonify({"success": True, "count": len(items), "notices": items})


@app.route("/api/notices/<path:notice_key>")
def api_notice_detail(notice_key: str):
    _require_api_key()
    notice = _find_notice(notice_key)
    if not notice:
        return jsonify({"success": False, "error": "공지를 찾을 수 없습니다."}), 404

    generated = (notice.get("image_result") or {}).get("generated_images") or []
    images = []
    for img_path in generated:
        if not img_path:
            continue
        abs_path = _to_abs(img_path)
        if not os.path.exists(abs_path):
            continue
        try:
            v = int(os.path.getmtime(abs_path))
        except Exception:
            v = 0
        images.append(
            {
                "url": f"/api/media/{_to_rel(abs_path)}?v={v}",
                "name": os.path.basename(abs_path),
            }
        )

    board_id = notice.get("board_id") or "default"
    content = notice.get("content") or ""
    image_result = notice.get("image_result") or {}
    detail = _api_notice_summary(notice)
    detail["images"] = images
    detail["copy_text"] = make_display_content(board_id, content)
    detail["content"] = content
    detail["thumb_title"] = image_result.get("thumbnail_title") or notice.get("title") or ""
    detail["thumb_date"] = image_result.get("thumbnail_date") or notice.get("date") or ""
    return jsonify({"success": True, "notice": detail})


_API_THUMB_WIDTHS = (320, 640)


@app.route("/api/media/<path:rel_path>")
def api_media(rel_path: str):
    _require_api_key()
    safe_path = _to_abs(rel_path)
    if os.path.commonpath([str(BASE_PATH), safe_path]) != str(BASE_PATH):
        abort(403)
    if not os.path.exists(safe_path):
        abort(404)

    # ?w=320 → 축소 썸네일을 만들어 디스크에 캐시하고 그걸 서빙 (앱 그리드용)
    width = request.args.get("w", type=int)
    if width in _API_THUMB_WIDTHS and safe_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        cache_dir = os.path.join(DATA_DIR, "thumb_cache")
        os.makedirs(cache_dir, exist_ok=True)
        digest = hashlib.md5(f"{_to_rel(safe_path)}|{width}".encode("utf-8")).hexdigest()
        thumb_path = os.path.join(cache_dir, f"{digest}.jpg")
        try:
            src_mtime = os.path.getmtime(safe_path)
            if not os.path.exists(thumb_path) or os.path.getmtime(thumb_path) < src_mtime:
                with Image.open(safe_path) as img:
                    img = img.convert("RGB")
                    ratio = width / img.width
                    if ratio < 1:
                        img = img.resize((width, max(1, int(img.height * ratio))), Image.LANCZOS)
                    img.save(thumb_path, "JPEG", quality=80)
            return send_file(thumb_path, mimetype="image/jpeg")
        except Exception:
            pass  # 썸네일 생성 실패 시 원본으로 폴백

    return send_file(safe_path)


@app.route("/api/notices/<path:notice_key>/done", methods=["POST"])
def api_mark_posted(notice_key: str):
    _require_api_key()
    payload = request.get_json(silent=True) or {}
    posted = bool(payload.get("posted", True))

    db_data = _load_json(NOTICE_DB_PATH)

    def _apply(entry: dict) -> bool:
        entry["posted"] = posted
        entry["posted_at"] = datetime.now().isoformat(timespec="seconds") if posted else ""
        return True

    updated = False
    if isinstance(db_data, dict):
        entry = db_data.get(str(notice_key))
        if isinstance(entry, dict) and _apply(entry):
            updated = True
        else:
            for candidate in db_data.values():
                if isinstance(candidate, dict) and candidate.get("notice_key") == notice_key:
                    if _apply(candidate):
                        updated = True
                        break
    elif isinstance(db_data, list):
        for item in db_data:
            if isinstance(item, dict) and item.get("notice_key") == notice_key:
                if _apply(item):
                    updated = True
                    break
    else:
        return jsonify({"success": False, "error": "데이터 파일을 읽을 수 없습니다."}), 500

    if not updated:
        return jsonify({"success": False, "error": "데이터 파일에서 항목을 찾을 수 없습니다."}), 404

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({"success": False, "error": f"데이터 저장 실패: {exc}"}), 500

    return jsonify({"success": True, "posted": posted})


@app.route("/api/console/<operation>")
def api_console(operation: str):
    """크롤러/웹 서비스의 상태와 최근 로그를 앱 콘솔 화면에 제공한다.

    임의 명령 실행이 아니라 ADMIN_OPERATION_COMMANDS의 조회성 작업만 허용한다.
    """
    _require_api_key()
    if operation not in ("status", "logs"):
        return jsonify({"success": False, "error": "허용되지 않은 작업입니다."}), 403
    success, output = _run_admin_operation(operation)
    return jsonify({"success": success, "output": output}), (200 if success else 500)


@app.route("/api/refresh", methods=["POST"])
def api_refresh_crawler():
    """크롤러(monitor)에 즉시 수집 신호를 보낸다 — 텔레그램 /r과 동일."""
    _require_api_key()
    success, output = _run_admin_operation("refresh_crawler")
    return jsonify({"success": success, "output": output}), (200 if success else 500)


@app.route("/api/notices/<path:notice_key>/recrawl", methods=["POST"])
def api_recrawl_notice(notice_key: str):
    _require_api_key()
    return recrawl_notice(notice_key)


@app.route("/api/notices/<path:notice_key>/thumbnail", methods=["POST"])
def api_update_thumbnail(notice_key: str):
    """JSON {title, date}로 썸네일(01.jpg) 제목/날짜를 바꿔 재생성한다."""
    _require_api_key()
    return update_thumbnail_header(notice_key)


def _iter_db_entries(db_data: Any):
    """notices_db.json이 dict든 list든 (notice_key, 항목)으로 순회한다."""
    if isinstance(db_data, dict):
        for key, value in db_data.items():
            if isinstance(value, dict):
                yield str(value.get("notice_key") or key), value
    elif isinstance(db_data, list):
        for item in db_data:
            if isinstance(item, dict) and item.get("notice_key"):
                yield str(item["notice_key"]), item


def _within_ig_window(entry: dict, window_start: str) -> bool:
    """공지가 인스타그램 조회 구간(가져온 게시물 중 가장 오래된 날짜 이후)에 있는지."""
    if not window_start:
        return True
    notice_day = (entry.get("date") or entry.get("crawled_at") or "")[:10]
    return bool(notice_day) and notice_day >= window_start[:10]


@app.route("/api/instagram/status")
def api_instagram_status():
    _require_api_key()
    if not INSTAGRAM_SYNC_AVAILABLE:
        return jsonify({"success": False, "configured": False, "error": "모듈을 사용할 수 없습니다."}), 500
    status = instagram_sync.token_status(DATA_DIR)
    return jsonify({"success": True, **status})


@app.route("/api/instagram/sync", methods=["POST"])
def api_instagram_sync():
    """인스타그램 게시물을 가져와 어떤 공지가 실제로 올라갔는지 대조한다."""
    _require_api_key()
    if not INSTAGRAM_SYNC_AVAILABLE:
        return jsonify({"success": False, "error": "인스타그램 동기화 모듈을 사용할 수 없습니다."}), 500

    payload = request.get_json(silent=True) or {}
    raw_max = payload.get("max_items")
    max_items = raw_max if isinstance(raw_max, int) and 0 < raw_max <= 1000 else 400

    try:
        token = instagram_sync.refresh_token_if_needed(DATA_DIR)
        account = instagram_sync.fetch_account(token)
        media = instagram_sync.fetch_media(token, max_items=max_items)
    except instagram_sync.InstagramNotConfigured as exc:
        return jsonify({"success": False, "configured": False, "error": str(exc)}), 409
    except instagram_sync.InstagramAPIError as exc:
        return jsonify({"success": False, "configured": True, "error": str(exc)}), 502

    notices = load_notice_dict()
    matches = instagram_sync.match_media(media, notices.values())
    window_start = instagram_sync.oldest_timestamp(media)
    checked_at = datetime.now().isoformat(timespec="seconds")

    db_data = _load_json(NOTICE_DB_PATH)
    if not isinstance(db_data, (dict, list)):
        return jsonify({"success": False, "error": "데이터 파일을 읽을 수 없습니다."}), 500

    verified = 0
    newly_marked = 0
    unverified: list[str] = []

    for key, entry in _iter_db_entries(db_data):
        entry["ig_checked_at"] = checked_at
        match = matches.get(key)
        if match:
            verified += 1
            entry["ig_media_id"] = match["media_id"]
            entry["ig_permalink"] = match["permalink"]
            entry["ig_timestamp"] = match["timestamp"]
            entry["ig_match"] = match["confidence"]
            if not entry.get("posted"):
                entry["posted"] = True
                entry["posted_at"] = match["timestamp"] or checked_at
                newly_marked += 1
            continue

        # 못 찾은 공지는 표시만 지운다. posted는 건드리지 않는다 —
        # 조회 구간보다 오래된 게시물이거나 캡션을 크게 고쳤을 수 있다.
        for field in ("ig_media_id", "ig_permalink", "ig_timestamp", "ig_match"):
            entry.pop(field, None)
        if entry.get("posted") and _within_ig_window(entry, window_start):
            unverified.append(key)

    try:
        with open(NOTICE_DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(db_data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        return jsonify({"success": False, "error": f"데이터 저장 실패: {exc}"}), 500

    summary = {
        "account": account.get("username") or "",
        "account_media_count": account.get("media_count") or 0,
        "fetched_media": len(media),
        "window_start": window_start,
        "verified": verified,
        "newly_marked": newly_marked,
        "unverified": len(unverified),
        "unverified_keys": unverified[:50],
        "synced_at": checked_at,
    }
    instagram_sync.save_sync_summary(DATA_DIR, summary)
    return jsonify({"success": True, **summary})


def main() -> None:
    parser = argparse.ArgumentParser(description="공지 크롤링 대시보드")
    parser.add_argument("--host", default="127.0.0.1", help="바인딩 호스트 (기본: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8002, help="포트 (기본: 8002)")
    parser.add_argument("--data-dir", default="data", help="notice_links.json / notices_db.json 저장 경로")
    parser.add_argument("--debug", action="store_true", help="Flask debug 모드")
    args = parser.parse_args()

    _set_data_dir(args.data_dir)
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        request_handler=DashboardRequestHandler,
    )


if __name__ == "__main__":
    main()
