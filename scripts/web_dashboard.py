#!/usr/bin/env python3
"""
공지 크롤링 진행 상황을 웹 페이지로 제공하는 대시보드.

- notices_db.json / notice_links.json을 읽어 상태 표시
- 제목/내용 복사 버튼 제공
- 생성된 이미지를 ZIP으로 다운로드할 수 있는 링크 제공
"""

from __future__ import annotations

import argparse
import io
import json
import os
import zipfile
from datetime import datetime
from typing import Any

from flask import Flask, abort, render_template_string, send_file

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_PATH = SCRIPT_DIR.parent
BASE_DIR = str(BASE_PATH)
DATA_DIR = str(BASE_PATH / "data")
NOTICE_DB_PATH = str(BASE_PATH / "data" / "notices_db.json")
LINKS_PATH = str(BASE_PATH / "data" / "notice_links.json")


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

app = Flask(__name__)


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
            parsed = datetime.min
        return (
            -parsed.timestamp(),
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
        .card h2 { margin-top: 0; font-size: 20px; color: #0f172a; }
        .info-line { display: flex; gap: 10px; font-size: 14px; color: #4b5563; margin-bottom: 12px; flex-wrap: wrap; }
        .info-line a { color: #2563eb; text-decoration: none; }
        .info-line a:hover { text-decoration: underline; }
        .btn { display: inline-flex; align-items: center; gap: 6px; margin-right: 10px; background: #2563eb; color: #fff; border: none; border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 14px; }
        .btn.secondary { background: #6b7280; }
        .btn[disabled] { opacity: 0.5; cursor: not-allowed; }
        textarea { width: 100%; min-height: 120px; font-size: 14px; padding: 10px; border-radius: 8px; border: 1px solid #d1d5db; resize: vertical; background: #f9fafb; }
        .preview-section { margin-top: 16px; }
        .preview-title { font-size: 14px; font-weight: 600; color: #1f2937; margin-bottom: 10px; }
        .preview-grid { display: flex; gap: 12px; flex-wrap: wrap; }
        .preview-item { text-align: center; width: 180px; }
        .preview-grid img { width: 100%; border-radius: 10px; box-shadow: 0 1px 3px rgba(15,23,42,0.18); object-fit: cover; }
        .preview-grid span { font-size: 12px; color: #4b5563; display: block; margin-top: 6px; word-break: break-all; }
        .images { font-size: 14px; color: #374151; margin-top: 8px; }
        .status { margin-top: 12px; font-size: 13px; color: #6b7280; }
        .pagination { display: flex; gap: 8px; margin-top: 20px; flex-wrap: wrap; }
        .page-btn { border: none; border-radius: 8px; padding: 8px 12px; background: #e5e7eb; color: #1f2933; cursor: pointer; font-size: 12px; }
        .page-btn.active { background: #2563eb; color: #fff; }
        .page-info { font-size: 13px; color: #6b7280; display: flex; align-items: center; gap: 8px; }
        .toast { position: fixed; right: 20px; bottom: 20px; background: rgba(17,24,39,0.9); color: #fff; padding: 12px 18px; border-radius: 8px; opacity: 0; transition: opacity .3s ease; pointer-events: none; }
        .toast.show { opacity: 1; }
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
            data-pages="{{ group.pages|length }}"
            data-board="{{ group.board_id }}"
        >
            {% for page_items in group.pages %}
            <div class="page {% if loop.first %}active{% endif %}" data-page="{{ loop.index0 }}">
                {% for notice in page_items %}
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
                        <button class="btn" onclick="copyText(`{{ notice.content or '' }}`)">본문 복사</button>
                        {% if notice.image_result and notice.image_result.generated_images %}
                            <a class="btn secondary" href="/download/{{ notice.notice_key }}">이미지 다운로드</a>
                        {% else %}
                            <button class="btn secondary" disabled>이미지 없음</button>
                        {% endif %}
                    </div>
                    {% if notice.preview_urls %}
                    <div class="preview-section">
                        <div class="preview-title">미리보기 ({{ notice.preview_urls|length }}장)</div>
                        <div class="preview-grid">
                            {% for item in notice.preview_urls %}
                            <div class="preview-item">
                                <img src="{{ item.url }}" alt="{{ item.name }}">
                                <span>{{ item.name }}</span>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                    {% endif %}
                    <textarea readonly>{{ notice.content or '' }}</textarea>
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
            </div>
            {% endfor %}
            {% if group.pages|length > 1 %}
            <div class="pagination" data-board="{{ group.board_id }}">
                <div class="page-info">
                    페이지 <span class="current-page">1</span> / {{ group.pages|length }}
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
        document.addEventListener("DOMContentLoaded", function() {
            const tabs = document.querySelectorAll(".tab-btn");
            const panels = document.querySelectorAll(".tab-panel");
            tabs.forEach(tab => {
                tab.addEventListener("click", () => {
                    tabs.forEach(t => t.classList.remove("active"));
                    panels.forEach(p => p.classList.remove("active"));
                    tab.classList.add("active");
                    const target = document.getElementById(tab.dataset.target);
                    if (target) target.classList.add("active");
                });
            });
            panels.forEach(panel => {
                const pagination = panel.querySelector(".pagination");
                if (!pagination) return;
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
                        if (selectedPage) selectedPage.classList.add("active");
                        if (currentPageSpan) currentPageSpan.textContent = String(pageIdx + 1);
                    });
                });
            });
        });
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    notices = load_notices()
    links = load_links()
    last_updated = "-"
    if links:
        last_times = [info.get("last_checked") for info in links.values() if info.get("last_checked")]
        if last_times:
            last_updated = max(last_times)

    board_groups_dict: dict[str, dict] = {}

    with_images = 0
    for notice in notices:
        generated = notice.get("image_result", {}).get("generated_images") or []
        if generated:
            with_images += 1
        preview_items = []
        for img_path in generated[:6]:
            if not img_path:
                continue
            abs_path = _to_abs(img_path)
            if not os.path.exists(abs_path):
                continue
            preview_items.append(
                {
                    "url": f"/media/{_to_rel(abs_path)}",
                    "name": os.path.basename(abs_path),
                }
            )
        notice["preview_urls"] = preview_items
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
        pages: list[list[dict]] = []
        notices_per_page = 5
        notices_list = group["notices"]
        for idx in range(0, len(notices_list), notices_per_page):
            pages.append(notices_list[idx : idx + notices_per_page])
        group["pages"] = pages
        group["count"] = len(notices_list)
        group["page_range"] = list(range(len(pages)))
        board_groups.append(group)

    board_groups.sort(key=lambda x: (x.get("board_name") or x.get("board_id")))

    return render_template_string(
        TEMPLATE,
        notices=notices,
        board_groups=board_groups,
        total=len(notices),
        with_images=with_images,
        last_updated=last_updated,
    )


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
        for img_path in images:
            if not img_path:
                continue
            abs_path = _to_abs(img_path)
            if os.path.exists(abs_path):
                arcname = os.path.join(folder_name, os.path.basename(abs_path))
                zf.write(abs_path, arcname)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="공지 크롤링 대시보드")
    parser.add_argument("--host", default="127.0.0.1", help="바인딩 호스트 (기본: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="포트 (기본: 8000)")
    parser.add_argument("--debug", action="store_true", help="Flask debug 모드")
    args = parser.parse_args()

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()

