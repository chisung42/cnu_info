"""인스타그램 계정의 실제 게시물과 수집한 공지를 대조한다.

Instagram API with Instagram Login을 사용한다. 필요한 준비물:

- 인스타그램 프로페셔널 계정(비즈니스 또는 크리에이터). 페이스북 페이지 연결은 필요 없다.
- Meta 앱. 본인 계정만 읽으므로 개발 모드 + Instagram 테스터 역할이면 앱 심사가 필요 없다.
- ``instagram_business_basic`` 권한이 포함된 장기 액세스 토큰.

토큰은 ``data/instagram_token.json``에 저장한다. 장기 토큰은 60일 뒤 만료되므로
30일이 지나면 자동으로 갱신한다. 최초 토큰은 이 파일에 직접 넣거나 ``.env``의
``INSTAGRAM_ACCESS_TOKEN``으로 넘기면 첫 실행 때 파일로 옮긴다.

게시물 본문(캡션)은 대시보드의 '본문 복사'와 같은 텍스트이므로, 캡션을 정규화해
공지 본문과 맞춰 보면 어떤 공지가 실제로 올라갔는지 판별할 수 있다.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Iterable

import requests

try:
    from scripts.notice_display import make_display_content
except ImportError:  # 서버는 scripts/ 안에서 실행한다
    from notice_display import make_display_content


GRAPH_BASE = "https://graph.instagram.com"
TOKEN_FILENAME = "instagram_token.json"
SYNC_FILENAME = "instagram_sync.json"

# 캡션 앞부분이 이만큼 같으면 같은 공지로 본다.
PREFIX_LEN = 60
# 접두사가 어긋날 때(운영자가 캡션을 손봤을 때) 허용하는 최소 유사도.
FUZZY_THRESHOLD = 0.78
# 이보다 짧은 텍스트는 공지를 구별할 정보가 없다고 보고 대조에서 뺀다.
# 게시판 머리말만 남는 경우(본문이 이미지에만 있는 공지)가 서로 뒤바뀌는 것을 막는다.
MIN_MATCH_LEN = 25
# 토큰을 며칠마다 갱신할지. 만료(60일)보다 넉넉히 앞선다.
REFRESH_AFTER_DAYS = 30
MEDIA_FIELDS = "id,caption,permalink,timestamp,media_type"


class InstagramNotConfigured(RuntimeError):
    """토큰이 없어 인스타그램을 조회할 수 없는 상태."""


class InstagramAPIError(RuntimeError):
    """인스타그램 API가 오류를 반환한 상태."""


# ---------------------------------------------------------------------------
# 토큰 관리
# ---------------------------------------------------------------------------


def _token_path(data_dir: str) -> str:
    return os.path.join(data_dir, TOKEN_FILENAME)


def _sync_path(data_dir: str) -> str:
    return os.path.join(data_dir, SYNC_FILENAME)


def _read_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_token(data_dir: str) -> dict:
    """저장된 토큰을 읽는다. 없으면 .env의 값으로 초기화한다."""
    stored = _read_json(_token_path(data_dir))
    if stored.get("access_token"):
        return stored

    seed = (os.environ.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
    if not seed:
        return {}
    stored = {
        "access_token": seed,
        "refreshed_at": datetime.now().isoformat(timespec="seconds"),
        "source": "env",
    }
    _write_json(_token_path(data_dir), stored)
    return stored


def refresh_token_if_needed(data_dir: str) -> str:
    """필요하면 장기 토큰을 갱신하고 유효한 토큰 문자열을 돌려준다."""
    stored = load_token(data_dir)
    token = (stored.get("access_token") or "").strip()
    if not token:
        raise InstagramNotConfigured(
            "인스타그램 액세스 토큰이 없습니다. data/instagram_token.json 또는 "
            ".env의 INSTAGRAM_ACCESS_TOKEN을 설정하세요."
        )

    refreshed_at = stored.get("refreshed_at") or ""
    try:
        last = datetime.fromisoformat(refreshed_at)
    except ValueError:
        last = None

    if last and datetime.now() - last < timedelta(days=REFRESH_AFTER_DAYS):
        return token

    try:
        resp = requests.get(
            f"{GRAPH_BASE}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=20,
        )
        payload = resp.json() if resp.content else {}
    except Exception:
        # 갱신만 실패한 경우다. 기존 토큰이 아직 유효할 수 있으니 그대로 시도한다.
        return token

    new_token = payload.get("access_token")
    if resp.status_code != 200 or not new_token:
        # 갱신이 거부되어도 기존 토큰이 아직 살아 있을 수 있으므로 그대로 쓴다.
        return token

    stored.update(
        {
            "access_token": new_token,
            "refreshed_at": datetime.now().isoformat(timespec="seconds"),
            "expires_in": payload.get("expires_in"),
            "source": "refresh",
        }
    )
    _write_json(_token_path(data_dir), stored)
    return new_token


def _raise_api_error(exc: Exception) -> None:
    raise InstagramAPIError(f"인스타그램 API 호출 실패: {exc}") from exc


def token_status(data_dir: str) -> dict:
    """앱 화면에 보여줄 토큰/동기화 상태."""
    stored = load_token(data_dir)
    refreshed_at = stored.get("refreshed_at") or ""
    expires_at = ""
    if refreshed_at:
        try:
            expires_at = (
                datetime.fromisoformat(refreshed_at) + timedelta(days=60)
            ).isoformat(timespec="seconds")
        except ValueError:
            expires_at = ""
    last_sync = _read_json(_sync_path(data_dir))
    return {
        "configured": bool(stored.get("access_token")),
        "refreshed_at": refreshed_at,
        "expires_at": expires_at,
        "last_sync": last_sync,
    }


# ---------------------------------------------------------------------------
# 인스타그램 조회
# ---------------------------------------------------------------------------


def _get(path: str, params: dict) -> dict:
    try:
        resp = requests.get(f"{GRAPH_BASE}/{path}", params=params, timeout=30)
        payload = resp.json() if resp.content else {}
    except Exception as exc:
        _raise_api_error(exc)
    if resp.status_code != 200:
        message = (payload.get("error") or {}).get("message") or resp.text[:200]
        raise InstagramAPIError(f"인스타그램 API 오류 {resp.status_code}: {message}")
    return payload


def fetch_account(token: str) -> dict:
    return _get("me", {"fields": "username,media_count", "access_token": token})


def fetch_media(token: str, max_items: int = 400) -> list[dict]:
    """최신 게시물부터 max_items개까지 가져온다."""
    items: list[dict] = []
    url_params: dict[str, Any] = {
        "fields": MEDIA_FIELDS,
        "limit": 100,
        "access_token": token,
    }
    payload = _get("me/media", url_params)
    while True:
        batch = payload.get("data") or []
        items.extend(item for item in batch if isinstance(item, dict))
        if len(items) >= max_items:
            return items[:max_items]
        next_url = ((payload.get("paging") or {}).get("next") or "").strip()
        if not next_url:
            return items
        try:
            resp = requests.get(next_url, timeout=30)
            payload = resp.json() if resp.content else {}
        except Exception as exc:
            _raise_api_error(exc)
        if resp.status_code != 200:
            return items


# ---------------------------------------------------------------------------
# 캡션 ↔ 공지 대조
# ---------------------------------------------------------------------------


def normalize_for_match(text: str | None) -> str:
    """해시태그와 공백 차이를 지워 비교 가능한 형태로 만든다."""
    if not text:
        return ""
    stripped = re.sub(r"#\S+", " ", text)
    return re.sub(r"\s+", " ", stripped).strip()


def _notice_caption(notice: dict) -> str:
    return make_display_content(
        notice.get("board_id") or "default", notice.get("content") or ""
    )


def _notice_match_texts(notice: dict) -> list[str]:
    """이 공지의 캡션이 될 수 있는 형태들.

    생성 캡션은 제목을 넣지 않지만, 운영자가 직접 제목을 적어 올리는 경우가 많다.
    본문이 이미지에만 있는 공지는 제목이 유일한 단서이므로 제목을 붙인 형태도 함께 본다.
    """
    board_id = notice.get("board_id") or "default"
    content = notice.get("content") or ""
    title = (notice.get("title") or "").strip()

    variants = [make_display_content(board_id, content)]
    if title:
        joined = f"{title}\n{content}" if content else title
        variants.append(make_display_content(board_id, joined))
    # AI 정리 전 원문으로 올렸을 수도 있다.
    raw = notice.get("raw_content") or ""
    if raw and raw != content:
        variants.append(make_display_content(board_id, raw))

    texts: list[str] = []
    for variant in variants:
        norm = normalize_for_match(variant)
        if len(norm) >= MIN_MATCH_LEN and norm not in texts:
            texts.append(norm)
    return texts


def _build_index(
    notices: Iterable[dict],
) -> tuple[dict[str, list[tuple[str, str]]], list[tuple[str, str]], dict[str, list[str]]]:
    """(접두사 인덱스, 전체 후보, 동일 텍스트를 공유하는 공지 목록)을 만든다."""
    index: dict[str, list[tuple[str, str]]] = {}
    entries: list[tuple[str, str]] = []
    keys_by_text: dict[str, list[str]] = {}
    for notice in notices:
        key = notice.get("notice_key")
        if not key:
            continue
        for norm in _notice_match_texts(notice):
            entry = (str(key), norm)
            entries.append(entry)
            index.setdefault(norm[:PREFIX_LEN], []).append(entry)
            shared = keys_by_text.setdefault(norm, [])
            if str(key) not in shared:
                shared.append(str(key))
    return index, entries, keys_by_text


def _best_of(
    candidates: list[tuple[str, str]], caption: str, floor: float = 0.0
) -> tuple[tuple[str, str] | None, float]:
    """caption과 가장 비슷한 후보를 찾는다. floor 이하 후보는 빠르게 건너뛴다."""
    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(caption)
    best: tuple[str, str] | None = None
    best_ratio = floor
    for entry in candidates:
        matcher.set_seq1(entry[1])
        if matcher.real_quick_ratio() < best_ratio or matcher.quick_ratio() < best_ratio:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best, best_ratio = entry, ratio
    return best, best_ratio


def match_media(media: list[dict], notices: Iterable[dict]) -> tuple[dict[str, dict], dict]:
    """게시물 목록을 공지에 대조한다.

    ({notice_key: 매칭정보}, 통계)를 돌려준다. 통계의 unmatched_media는 어떤 공지와도
    맞지 않은 게시물 수다 — 직접 올린 게시물이 대부분이므로 오류가 아니다.
    """
    index, entries, keys_by_text = _build_index(notices)
    matches: dict[str, dict] = {}
    unmatched_media = 0
    captionless = 0

    for item in media:
        caption = normalize_for_match(item.get("caption"))
        if len(caption) < MIN_MATCH_LEN:
            captionless += 1
            continue

        matched: tuple[str, str] | None = None
        confidence = ""
        bucket = index.get(caption[:PREFIX_LEN])
        if bucket:
            matched = bucket[0] if len(bucket) == 1 else _best_of(bucket, caption)[0]
            confidence = "exact"
        else:
            matched = _best_of(entries, caption, floor=FUZZY_THRESHOLD)[0]
            if matched:
                confidence = "fuzzy"

        if not matched:
            unmatched_media += 1
            continue

        info = {
            "media_id": item.get("id") or "",
            "permalink": item.get("permalink") or "",
            "timestamp": item.get("timestamp") or "",
            "confidence": confidence,
        }
        # 본문이 완전히 같은 공지가 여러 건이면(같은 글이 두 번 수집된 경우) 모두 인정한다.
        # 그러지 않으면 한 건만 확인되고 나머지가 계속 '미업로드'로 남는다.
        for key in keys_by_text.get(matched[1], [matched[0]]):
            existing = matches.get(key)
            if existing and existing.get("confidence") == "exact" and confidence != "exact":
                continue
            matches[key] = info

    stats = {
        "unmatched_media": unmatched_media,
        "captionless_media": captionless,
    }
    return matches, stats


def oldest_timestamp(media: list[dict]) -> str:
    stamps = [str(item.get("timestamp") or "") for item in media if item.get("timestamp")]
    return min(stamps) if stamps else ""


def save_sync_summary(data_dir: str, summary: dict) -> None:
    _write_json(_sync_path(data_dir), summary)
