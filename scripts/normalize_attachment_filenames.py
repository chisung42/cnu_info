#!/usr/bin/env python3
"""Shorten attachment path components and update notice JSON references.

Linux allows at most 255 bytes per filename component.  Some Korean CNU
attachments exceed that even though macOS permits them locally.  This script
preserves a readable prefix, adds a stable hash, and atomically updates every
``attachments/...`` path stored in the notice databases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

MAX_COMPONENT_BYTES = 180


def shortened_component(name: str) -> str:
    if len(name.encode('utf-8')) <= MAX_COMPONENT_BYTES:
        return name
    stem, ext = os.path.splitext(name)
    suffix = f"_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]}{ext}"
    budget = max(1, MAX_COMPONENT_BYTES - len(suffix.encode('utf-8')))
    prefix = stem.encode('utf-8')[:budget].decode('utf-8', 'ignore').rstrip(' ._')
    return f"{prefix or 'attachment'}{suffix}"


def unique_destination(source: Path, shortened: str) -> Path:
    candidate = source.with_name(shortened)
    index = 2
    while candidate.exists():
        stem, ext = os.path.splitext(shortened)
        suffix = f"_{index}{ext}"
        budget = MAX_COMPONENT_BYTES - len(suffix.encode('utf-8'))
        prefix = stem.encode('utf-8')[:budget].decode('utf-8', 'ignore').rstrip(' ._')
        candidate = source.with_name(f"{prefix}{suffix}")
        index += 1
    return candidate


def remap_path(path: Path, moves: list[tuple[Path, Path]]) -> Path:
    result = path
    for old, new in moves:
        try:
            suffix = result.relative_to(old)
        except ValueError:
            continue
        result = new / suffix
    return result


def remap_json(value: Any, moves: list[tuple[Path, Path]]) -> Any:
    if isinstance(value, dict):
        return {key: remap_json(item, moves) for key, item in value.items()}
    if isinstance(value, list):
        return [remap_json(item, moves) for item in value]
    if isinstance(value, str) and value.startswith('attachments/'):
        return str(remap_path(Path(value), moves))
    return value


def normalize(root: Path, data_dir: Path, dry_run: bool) -> tuple[int, int]:
    moves: list[tuple[Path, Path]] = []
    for current, dirs, files in os.walk(root, topdown=False):
        parent = Path(current)
        for name in [*files, *dirs]:
            if len(name.encode('utf-8')) <= MAX_COMPONENT_BYTES:
                continue
            source = parent / name
            destination = unique_destination(source, shortened_component(name))
            moves.append((source, destination))
            if not dry_run:
                source.rename(destination)

    updated_records = 0
    if moves and not dry_run:
        for filename in ('notices_db.json', 'notice_links.json'):
            path = data_dir / filename
            if not path.is_file():
                continue
            original = json.loads(path.read_text(encoding='utf-8'))
            updated = remap_json(original, moves)
            if updated != original:
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, path)
                updated_records += 1
    return len(moves), updated_records


def main() -> int:
    parser = argparse.ArgumentParser(description='긴 첨부 파일명을 서버 호환 형식으로 정리합니다.')
    parser.add_argument('--root', type=Path, default=Path('attachments'))
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    renamed, updated = normalize(args.root, args.data_dir, args.dry_run)
    status = '변경 예정' if args.dry_run else '변경 완료'
    print(f'{status}: 파일·폴더 이름 {renamed}개, JSON {updated}개')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
