#!/usr/bin/env python3
"""
단일 공지 게시물에 대해 Instagram 업로드용 이미지를 생성하는 유틸리티.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
import qrcode

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_PATH = SCRIPT_DIR.parent
BASE_DIR = str(BASE_PATH)


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

SUPPORTED_SOURCE_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_PAGE_NUMBER_SUFFIX_PATTERN = re.compile(r"(?:[_-])(?P<digits>\d{2,4})$")
LOGO_PATH = BASE_PATH / "assets" / "cnu_logo_white.png"


def _load_font(size: int) -> ImageFont.ImageFont:
    # Deployment runs on Linux, while local development commonly runs on macOS.
    # Prefer an installed Korean Noto face on Linux; otherwise Pillow falls back
    # to its bitmap default font and Hangul becomes tofu squares.
    candidates = [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 1),
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
        ("/System/Library/Fonts/AppleGothic.ttf", 0),
        ("/System/Library/Fonts/Arial.ttf", 0),
    ]
    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size, index=index)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split(' ')
    lines: list[str] = []
    current_line = ""
    for word in words:
        test_line = current_line + (" " if current_line else "") + word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line.rstrip())
            current_line = word
    if current_line:
        lines.append(current_line.rstrip())
    return lines


def _save_jpeg(image: Image.Image, path: str) -> None:
    image.save(path, format="JPEG", quality=95)


def _draw_thumbnail_brand(image: Image.Image) -> None:
    if not LOGO_PATH.exists():
        return
    try:
        with Image.open(LOGO_PATH) as logo:
            logo = logo.convert("RGBA")
            logo.thumbnail((1140, 1140))
            alpha = logo.getchannel("A")
            alpha = alpha.point(lambda value: int(value * 0.18))
            logo.putalpha(alpha)
            x = (image.width - logo.width) // 2
            y = (image.height - logo.height) // 2 - 40
            image.paste(logo, (x, y), logo)
    except Exception:
        return


def _collect_images_from_dir(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    images: list[str] = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        if name.lower().endswith(SUPPORTED_SOURCE_IMAGE_EXTENSIONS):
            images.append(path)
    return images


def _collect_source_images(attachment_root: str) -> list[str]:
    priority_dirs = [
        os.path.join(attachment_root, "pngs"),
        os.path.join(attachment_root, "original"),
    ]
    collected: list[str] = []
    seen: set[str] = set()
    for directory in priority_dirs:
        for path in _collect_images_from_dir(directory):
            if path in seen:
                continue
            collected.append(path)
            seen.add(path)
    return collected


def _open_image_as_rgb(path: str) -> Image.Image | None:
    try:
        with Image.open(path) as img:
            return img.convert("RGB")
    except Exception as exc:
        print(f"[경고] 이미지 열기 실패: {path} ({exc})")
        return None


def _derive_image_group_key(path: str) -> tuple[str, str]:
    directory, filename = os.path.split(path)
    stem, _ = os.path.splitext(filename)
    match = _PAGE_NUMBER_SUFFIX_PATTERN.search(stem)
    if match:
        digits = match.group("digits")
        if digits and digits.startswith("0"):
            stem = stem[: match.start()]
    normalized_stem = stem.lower() if stem else filename.lower()
    return (directory, normalized_stem)


def _order_images_by_group(image_paths: list[str]) -> list[str]:
    if not image_paths:
        return []
    groups: list[dict] = []
    group_index: dict[tuple[str, str], int] = {}
    for idx, path in enumerate(image_paths):
        key = _derive_image_group_key(path)
        if key not in group_index:
            group_index[key] = len(groups)
            groups.append(
                {
                    "key": key,
                    "paths": [],
                    "first_index": idx,
                }
            )
        groups[group_index[key]]["paths"].append(path)
    groups.sort(key=lambda item: (len(item["paths"]), item["first_index"]))
    ordered: list[str] = []
    for group in groups:
        ordered.extend(group["paths"])
    return ordered


def _normalize_date_text(raw: str | None) -> str:
    if not raw:
        return "날짜 정보 없음"
    value = str(raw).strip()
    if not value:
        return "날짜 정보 없음"

    for separator in (" ", "T"):
        if separator in value:
            candidate = value.split(separator)[0].strip()
            if candidate:
                value = candidate
                break

    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 8:
        year = digits[0:4]
        month = digits[4:6]
        day = digits[6:8]
        if year and month and day:
            return f"{year}-{month}-{day}"

    return value


def generate_notice_thumbnail_header(
    notice: dict,
    attachment_root: str,
    *,
    title: str | None = None,
    date_text: str | None = None,
    out_filename: str = "01.jpg",
) -> str:
    """
    썸네일(첫 이미지)용 헤더 이미지만 재생성.
    - 결과 파일은 {attachment_root}/result/{out_filename} 로 저장됨
    - 반환값은 상대경로(_to_rel) 문자열
    """
    final_title = (title if isinstance(title, str) and title.strip() else (notice.get("title") or "제목 없음")).strip()
    final_date = (
        _normalize_date_text(date_text)
        if isinstance(date_text, str) and date_text.strip()
        else _normalize_date_text(notice.get("date"))
    )

    img_width, img_height = 1080, 1350
    header_font = _load_font(90)
    date_font = _load_font(60)

    attachment_root_abs = _to_abs(attachment_root)
    attachment_path = Path(attachment_root_abs)
    result_dir_path = attachment_path / "result"
    result_dir_path.mkdir(parents=True, exist_ok=True)

    header_img = Image.new("RGB", (img_width, img_height), "white")
    _draw_thumbnail_brand(header_img)
    header_draw = ImageDraw.Draw(header_img)
    max_text_width = img_width - 200
    title_lines = wrap_text(header_draw, final_title, header_font, max_text_width)
    line_height = 100
    date_line_height = 80
    title_to_date_gap = 20
    total_text_height = len(title_lines) * line_height + title_to_date_gap + date_line_height
    start_y = (img_height - total_text_height) // 2

    for i, line in enumerate(title_lines):
        bbox = header_draw.textbbox((0, 0), line, font=header_font)
        text_width = bbox[2] - bbox[0]
        x = (img_width - text_width) // 2
        y = start_y + i * line_height
        header_draw.text((x, y), line, fill="black", font=header_font)

    date_bbox = header_draw.textbbox((0, 0), final_date, font=date_font)
    date_width = date_bbox[2] - date_bbox[0]
    date_x = (img_width - date_width) // 2
    date_y = start_y + len(title_lines) * line_height + title_to_date_gap
    header_draw.text((date_x, date_y), final_date, fill="black", font=date_font)

    header_path = result_dir_path / out_filename
    _save_jpeg(header_img, str(header_path))
    return _to_rel(str(header_path))


def generate_notice_images(
    notice: dict,
    attachment_root: str,
    *,
    max_images: int = 20,
) -> dict:
    """
    단일 공지에 대한 결과 이미지를 생성하고 경로 정보를 반환.
    반환값: {
        "result_dir": str,
        "generated_images": [str, ...],
        "truncated": bool,
        "source_image_count": int,
        "source_png_count": int,  # 하위 호환용
        "processed_image_count": int,
        "skipped_images": [str, ...],
    }
    """
    if max_images < 2:
        raise ValueError("max_images는 최소 2 이상이어야 합니다.")

    title = (notice.get("title") or "제목 없음").strip()
    date_text = _normalize_date_text(notice.get("date"))
    url = notice.get("url") or "URL 없음"

    img_width, img_height = 1080, 1350
    header_font = _load_font(90)
    body_font = _load_font(70)
    date_font = _load_font(60)

    attachment_root_abs = _to_abs(attachment_root)
    attachment_path = Path(attachment_root_abs)
    result_dir_path = attachment_path / "result"
    result_dir_path.mkdir(parents=True, exist_ok=True)

    source_images = _collect_source_images(attachment_root_abs)
    png_count = sum(1 for path in source_images if path.lower().endswith(".png"))
    source_images = _order_images_by_group(source_images)
    max_mid_images = max(0, max_images - 2)
    mid_images = source_images[:max_mid_images]
    truncated = len(source_images) > len(mid_images)
    failed_images: list[str] = []

    generated_files: list[str] = []

    # 01: 제목 + 날짜
    header_img = Image.new("RGB", (img_width, img_height), "white")
    # 신규 공지, 재크롤링, 파일 업로드로 생성되는 모든 썸네일에 CNU 브랜딩을
    # 기본 적용한다. 대시보드에서 별도로 "썸네일 적용"을 누를 필요가 없다.
    _draw_thumbnail_brand(header_img)
    header_draw = ImageDraw.Draw(header_img)
    max_text_width = img_width - 200
    title_lines = wrap_text(header_draw, title, header_font, max_text_width)
    line_height = 100
    date_line_height = 80
    title_to_date_gap = 20
    total_text_height = len(title_lines) * line_height + title_to_date_gap + date_line_height
    start_y = (img_height - total_text_height) // 2

    for i, line in enumerate(title_lines):
        bbox = header_draw.textbbox((0, 0), line, font=header_font)
        text_width = bbox[2] - bbox[0]
        x = (img_width - text_width) // 2
        y = start_y + i * line_height
        header_draw.text((x, y), line, fill="black", font=header_font)

    date_bbox = header_draw.textbbox((0, 0), date_text, font=date_font)
    date_width = date_bbox[2] - date_bbox[0]
    date_x = (img_width - date_width) // 2
    date_y = start_y + len(title_lines) * line_height + title_to_date_gap
    header_draw.text((date_x, date_y), date_text, fill="black", font=date_font)

    header_path = result_dir_path / "01.jpg"
    _save_jpeg(header_img, str(header_path))
    generated_files.append(str(header_path))

    # 본문 이미지 (첨부 PDF 변환 및 기본 이미지 자산 사용)
    content_index = 2
    for image_path in mid_images:
        orig_img = _open_image_as_rgb(image_path)
        if orig_img is None:
            failed_images.append(image_path)
            continue
        orig_width, orig_height = orig_img.size

        scale = min(img_width / orig_width, img_height / orig_height)
        new_width = int(orig_width * scale)
        new_height = int(orig_height * scale)

        pad_left = (img_width - new_width) // 2
        pad_top = (img_height - new_height) // 2

        resized = orig_img.resize((new_width, new_height))
        canvas = Image.new("RGB", (img_width, img_height), "white")
        canvas.paste(resized, (pad_left, pad_top))

        filename = f"{content_index:02d}.jpg"
        out_path = result_dir_path / filename
        _save_jpeg(canvas, str(out_path))
        generated_files.append(str(out_path))
        content_index += 1
        orig_img.close()

    # 마지막 QR 이미지
    final_index = min(max_images, max(2, content_index))
    final_filename = f"{final_index:02d}.jpg"

    final_img = Image.new("RGB", (img_width, img_height), "white")
    final_draw = ImageDraw.Draw(final_img)

    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_width, qr_height = qr_img.size
    qr_x = (img_width - qr_width) // 2
    qr_y = 150
    final_img.paste(qr_img, (qr_x, qr_y))

    closing_text = "스크린샷을 통해 원본 게시물을 확인할 수 있습니다"
    final_lines = wrap_text(final_draw, closing_text, body_font, img_width - 200)
    final_line_height = 80
    text_start = qr_y + qr_height + 50
    for i, line in enumerate(final_lines):
        bbox = final_draw.textbbox((0, 0), line, font=body_font)
        text_width = bbox[2] - bbox[0]
        x = (img_width - text_width) // 2
        y = text_start + i * final_line_height
        final_draw.text((x, y), line, fill="black", font=body_font)

    final_path = result_dir_path / final_filename
    _save_jpeg(final_img, str(final_path))
    generated_files.append(str(final_path))

    return {
        "result_dir": _to_rel(str(result_dir_path)),
        "generated_images": [_to_rel(path) for path in generated_files],
        "truncated": truncated,
        "source_image_count": len(source_images),
        "source_png_count": png_count,
        "processed_image_count": max(0, content_index - 2),
        "skipped_images": [_to_rel(path) for path in failed_images],
        "max_images": max_images,
    }


def _load_notices_db(path: str) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        converted: dict[str, dict] = {}
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                converted[str(item["id"])] = item
        return converted
    raise ValueError("알 수 없는 notices DB 형식입니다.")


def _write_notices_db(path: str, data: dict[str, dict]) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _select_targets(db: dict[str, dict], target_ids: Iterable[str] | None) -> list[dict]:
    if not target_ids:
        return list(db.values())
    return [db[target_id] for target_id in target_ids if target_id in db]


def main() -> None:
    parser = argparse.ArgumentParser(description="공지 게시물 이미지 생성")
    parser.add_argument(
        "--notices-db",
        default=os.path.join(BASE_DIR, "data", "notices_db.json"),
        help="공지 상세 정보 JSON 파일 경로 (기본: data/notices_db.json)",
    )
    parser.add_argument(
        "--notice-id",
        action="append",
        help="특정 notice ID만 처리 (여러 번 지정 가능)",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=20,
        help="공지당 생성할 최대 이미지 수 (기본 20)",
    )
    args = parser.parse_args()

    notices_db = _load_notices_db(args.notices_db)
    targets = _select_targets(notices_db, args.notice_id)

    if not targets:
        print("처리할 notice가 없습니다.")
        return

    for notice in targets:
        attachment_dir = notice.get("attachment_dir")
        attachment_dir_abs = _to_abs(attachment_dir)
        if not attachment_dir or not os.path.isdir(attachment_dir_abs):
            print(f"[건너뜀] 첨부 디렉터리 없음: {notice.get('id')}")
            continue
        try:
            result = generate_notice_images(
                notice,
                attachment_dir,
                max_images=max(3, args.max_images),
            )
            notice["image_result"] = result
            if attachment_dir:
                notice["attachment_dir"] = _to_rel(attachment_dir_abs)
            notices_db[str(notice["id"])] = notice
            print(f"[완료] {notice.get('id')} → {result['result_dir']}")
        except Exception as exc:
            print(f"[실패] {notice.get('id')}: {exc}")

    _write_notices_db(args.notices_db, notices_db)


if __name__ == "__main__":
    main()
