#!/usr/bin/env python3
"""Repair CNU attachments wrapped by its stray `Error can not open file!!` output.

Usage:
    .venv/bin/python scripts/repair_attachment_files.py --dry-run
    .venv/bin/python scripts/repair_attachment_files.py
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from crawl_notices import (
    _CNU_DOWNLOAD_ERROR,
    _strip_cnu_download_error_wrapper,
    _validate_attachment_bytes,
)


def repair(root: Path, dry_run: bool) -> tuple[int, int, list[str]]:
    repaired = 0
    failed = 0
    errors: list[str] = []

    for path in root.rglob('*'):
        if not path.is_file() or 'original' not in path.parts:
            continue
        ext = path.suffix.lower()
        try:
            data = path.read_bytes()
        except OSError as exc:
            failed += 1
            errors.append(f'{path}: 읽기 실패 ({exc})')
            continue
        if not data.startswith(_CNU_DOWNLOAD_ERROR):
            continue

        cleaned, recovered, reason = _strip_cnu_download_error_wrapper(data, ext)
        valid, validation_error = _validate_attachment_bytes(cleaned, ext)
        if not recovered or not valid:
            failed += 1
            errors.append(f'{path}: 복구 불가 ({reason or validation_error})')
            continue

        if not dry_run:
            temp_path = path.with_name(f'.{path.name}.repairing')
            try:
                temp_path.write_bytes(cleaned)
                os.replace(temp_path, path)
            finally:
                if temp_path.exists():
                    temp_path.unlink()
        repaired += 1

    return repaired, failed, errors


def main() -> int:
    parser = argparse.ArgumentParser(description='CNU 오류 출력이 섞인 첨부 원본을 복구합니다.')
    parser.add_argument('--root', type=Path, default=Path('attachments'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    repaired, failed, errors = repair(args.root, args.dry_run)
    action = '복구 가능' if args.dry_run else '복구 완료'
    print(f'{action}: {repaired}개, 복구 불가: {failed}개')
    for error in errors:
        print(f'[실패] {error}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
