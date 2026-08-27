"""공지 이미지를 인스타그램에 직접 게시한다.

인스타그램 콘텐츠 게시 API는 파일 업로드를 받지 않는다. 공개된 URL을 주면
Meta 서버가 직접 그 URL로 이미지를 가져간다. 그래서 두 가지가 필요하다.

- ``instagram_business_content_publish`` 권한이 있는 액세스 토큰
- 인스타그램이 접근할 수 있는 **공개 HTTPS 이미지 URL** (유효한 인증서 필요)

이 서버는 Tailscale Funnel로 공개 노출돼 있으므로 그 주소를 쓴다. 다만 ``/api/``는
API 키를 요구하므로, 게시할 때만 쓰는 서명된 임시 공개 경로(``/pub/media/...``)를
따로 만든다. 서명은 APP_API_KEY로 만든 HMAC이고 기본 2시간 뒤 만료된다.

API 제약(2026년 기준):
- 캐러셀은 최대 10장. 인스타그램 앱에서는 20장까지 되지만 API는 10장이다.
- JPEG만 가능하다.
- 캡션은 2200자, 해시태그 30개까지.
- 계정당 24시간에 100건까지 게시할 수 있다.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any
from urllib.parse import quote

import requests

GRAPH_BASE = "https://graph.instagram.com"

# 캐러셀 상한. 앱에서는 20장까지 올라가지만 API는 10장만 받는다.
MAX_CAROUSEL_ITEMS = 10
CAPTION_LIMIT = 2200
# 서명된 이미지 URL의 유효 시간. 게시 시점에만 필요하므로 짧게 둔다.
SIGNED_URL_TTL = 2 * 60 * 60
# 컨테이너가 준비되기를 기다리는 최대 시간.
CONTAINER_TIMEOUT = 120


class InstagramPublishError(RuntimeError):
    """게시 과정에서 실패한 상태."""


# ---------------------------------------------------------------------------
# 서명된 공개 이미지 URL
# ---------------------------------------------------------------------------


def _secret() -> bytes:
    key = (os.environ.get("APP_API_KEY") or "").strip()
    if not key:
        raise InstagramPublishError("APP_API_KEY가 없어 이미지 URL에 서명할 수 없습니다.")
    return key.encode("utf-8")


def sign_path(rel_path: str, expires: int) -> str:
    message = f"{expires}:{rel_path}".encode("utf-8")
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()[:32]


def verify_signature(rel_path: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(sign_path(rel_path, expires), signature)


def public_base_url() -> str:
    """인스타그램이 접근할 수 있는 이 서버의 공개 주소."""
    return (
        os.environ.get("PUBLIC_BASE_URL")
        or "https://moon-p151emx.tail70d104.ts.net"
    ).rstrip("/")


def signed_media_url(rel_path: str, ttl: int = SIGNED_URL_TTL) -> str:
    expires = int(time.time()) + ttl
    signature = sign_path(rel_path, expires)
    return f"{public_base_url()}/pub/media/{expires}/{signature}/{quote(rel_path)}"


# ---------------------------------------------------------------------------
# 인스타그램 게시
# ---------------------------------------------------------------------------


def trim_caption(caption: str) -> str:
    if len(caption) <= CAPTION_LIMIT:
        return caption
    return caption[: CAPTION_LIMIT - 1].rstrip() + "…"


def _post(path: str, params: dict) -> dict:
    try:
        resp = requests.post(f"{GRAPH_BASE}/{path}", data=params, timeout=60)
        payload = resp.json() if resp.content else {}
    except Exception as exc:
        raise InstagramPublishError(f"인스타그램 API 호출 실패: {exc}") from exc
    if resp.status_code != 200:
        error = payload.get("error") or {}
        message = error.get("error_user_msg") or error.get("message") or resp.text[:200]
        raise InstagramPublishError(f"인스타그램 오류: {message}")
    return payload


def _get(path: str, params: dict) -> dict:
    try:
        resp = requests.get(f"{GRAPH_BASE}/{path}", params=params, timeout=30)
        payload = resp.json() if resp.content else {}
    except Exception as exc:
        raise InstagramPublishError(f"인스타그램 API 호출 실패: {exc}") from exc
    if resp.status_code != 200:
        error = payload.get("error") or {}
        message = error.get("error_user_msg") or error.get("message") or resp.text[:200]
        raise InstagramPublishError(f"인스타그램 오류: {message}")
    return payload


def _create_container(token: str, params: dict) -> str:
    params = {**params, "access_token": token}
    result = _post("me/media", params)
    container_id = result.get("id")
    if not container_id:
        raise InstagramPublishError("컨테이너 ID를 받지 못했습니다.")
    return str(container_id)


def _wait_ready(token: str, container_id: str) -> None:
    """Meta가 이미지를 가져와 처리하기를 기다린다."""
    deadline = time.time() + CONTAINER_TIMEOUT
    delay = 2.0
    last = ""
    while time.time() < deadline:
        info = _get(
            container_id,
            {"fields": "status_code,status", "access_token": token},
        )
        status = str(info.get("status_code") or "")
        last = str(info.get("status") or status)
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramPublishError(f"이미지 처리 실패({status}): {last}")
        time.sleep(delay)
        delay = min(delay * 1.4, 8.0)
    raise InstagramPublishError(f"이미지 처리가 제한 시간 안에 끝나지 않았습니다: {last}")


def publish_images(
    token: str,
    image_urls: list[str],
    caption: str,
    dry_run: bool = False,
) -> dict:
    """이미지 여러 장을 캐러셀로 게시한다.

    dry_run이면 컨테이너 준비까지만 하고 실제 게시는 하지 않는다.
    인스타그램이 우리 이미지를 가져올 수 있는지 확인할 때 쓴다.
    """
    if not image_urls:
        raise InstagramPublishError("게시할 이미지가 없습니다.")

    used = image_urls[:MAX_CAROUSEL_ITEMS]
    skipped = len(image_urls) - len(used)
    caption = trim_caption(caption)

    if len(used) == 1:
        container_id = _create_container(token, {"image_url": used[0], "caption": caption})
        _wait_ready(token, container_id)
    else:
        children: list[str] = []
        for url in used:
            child = _create_container(token, {"image_url": url, "is_carousel_item": "true"})
            children.append(child)
        for child in children:
            _wait_ready(token, child)
        container_id = _create_container(
            token,
            {
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": caption,
            },
        )
        _wait_ready(token, container_id)

    result: dict[str, Any] = {
        "container_id": container_id,
        "image_count": len(used),
        "skipped_images": skipped,
        "dry_run": dry_run,
    }
    if dry_run:
        result["published"] = False
        return result

    published = _post(
        "me/media_publish",
        {"creation_id": container_id, "access_token": token},
    )
    media_id = str(published.get("id") or "")
    result["published"] = True
    result["media_id"] = media_id

    if media_id:
        try:
            info = _get(media_id, {"fields": "permalink,timestamp", "access_token": token})
            result["permalink"] = info.get("permalink") or ""
            result["timestamp"] = info.get("timestamp") or ""
        except InstagramPublishError:
            result["permalink"] = ""
            result["timestamp"] = ""
    return result


def publishing_quota(token: str) -> dict:
    """24시간 게시 한도와 사용량."""
    data = (_get("me/content_publishing_limit",
                 {"fields": "config,quota_usage", "access_token": token}).get("data") or [{}])[0]
    config = data.get("config") or {}
    return {
        "quota_total": config.get("quota_total") or 0,
        "quota_usage": data.get("quota_usage") or 0,
    }
