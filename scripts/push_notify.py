"""새 공지가 수집되면 iOS 앱에 푸시 알림을 보낸다.

APNs(Apple Push Notification service)를 토큰 방식으로 직접 호출한다. 필요한 것:

- 유료 Apple 개발자 계정에서 발급한 **APNs 인증 키(.p8)** 와 그 Key ID
- 팀 ID, 앱의 번들 ID

``.env`` 설정값:

```
APNS_KEY_PATH=/srv/cnuinfo/secrets/AuthKey_XXXXXXXXXX.p8
APNS_KEY_ID=XXXXXXXXXX
APNS_TEAM_ID=54J7TC5NH2
APNS_BUNDLE_ID=kr.moonhome.cnuinfo
```

키가 없으면 이 모듈은 조용히 아무것도 하지 않는다. 알림 실패가 수집을 막아서는 안 된다.

기기 토큰은 ``data/push_tokens.json``에 저장한다. APNs가 410(등록 해제)을 돌려주면
그 토큰을 지운다. 앱을 지운 기기에 계속 보내지 않기 위한 것이다.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

TOKENS_FILENAME = "push_tokens.json"
# 개발 빌드(Xcode에서 직접 설치)는 sandbox, TestFlight/App Store는 production을 쓴다.
APNS_HOSTS = {
    "sandbox": "https://api.sandbox.push.apple.com",
    "production": "https://api.push.apple.com",
}
# 같은 공지를 여러 번 알리지 않도록 최근 발송분을 기억한다.
SENT_HISTORY_LIMIT = 300


class PushNotConfigured(RuntimeError):
    """APNs 키가 없어 푸시를 보낼 수 없는 상태."""


# ---------------------------------------------------------------------------
# 기기 토큰 저장
# ---------------------------------------------------------------------------


def _tokens_path(data_dir: str) -> str:
    return os.path.join(data_dir, TOKENS_FILENAME)


def _read(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_state(data_dir: str) -> dict:
    state = _read(_tokens_path(data_dir))
    state.setdefault("devices", {})
    state.setdefault("sent", [])
    return state


def register_device(data_dir: str, token: str, environment: str = "sandbox") -> dict:
    """앱이 받은 기기 토큰을 등록한다."""
    token = (token or "").strip()
    if not token or len(token) > 200:
        raise ValueError("올바른 기기 토큰이 아닙니다.")
    if environment not in APNS_HOSTS:
        environment = "sandbox"

    state = load_state(data_dir)
    state["devices"][token] = {
        "environment": environment,
        "registered_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write(_tokens_path(data_dir), state)
    return {"devices": len(state["devices"]), "environment": environment}


def unregister_device(data_dir: str, token: str) -> bool:
    state = load_state(data_dir)
    if state["devices"].pop((token or "").strip(), None) is None:
        return False
    _write(_tokens_path(data_dir), state)
    return True


def _drop_tokens(data_dir: str, tokens: list[str]) -> None:
    if not tokens:
        return
    state = load_state(data_dir)
    changed = False
    for token in tokens:
        if state["devices"].pop(token, None) is not None:
            changed = True
    if changed:
        _write(_tokens_path(data_dir), state)


def already_sent(data_dir: str, notice_key: str) -> bool:
    return notice_key in (load_state(data_dir).get("sent") or [])


def mark_sent(data_dir: str, notice_key: str) -> None:
    state = load_state(data_dir)
    sent = [k for k in (state.get("sent") or []) if k != notice_key]
    sent.append(notice_key)
    state["sent"] = sent[-SENT_HISTORY_LIMIT:]
    _write(_tokens_path(data_dir), state)


# ---------------------------------------------------------------------------
# APNs 호출
# ---------------------------------------------------------------------------


def _config() -> dict:
    key_path = (os.environ.get("APNS_KEY_PATH") or "").strip()
    key_id = (os.environ.get("APNS_KEY_ID") or "").strip()
    team_id = (os.environ.get("APNS_TEAM_ID") or "").strip()
    bundle_id = (os.environ.get("APNS_BUNDLE_ID") or "kr.moonhome.cnuinfo").strip()
    if not (key_path and key_id and team_id):
        raise PushNotConfigured(
            "APNs 설정이 없습니다. .env에 APNS_KEY_PATH, APNS_KEY_ID, APNS_TEAM_ID를 넣으세요."
        )
    if not os.path.exists(key_path):
        raise PushNotConfigured(f"APNs 키 파일을 찾을 수 없습니다: {key_path}")
    return {"key_path": key_path, "key_id": key_id, "team_id": team_id, "bundle_id": bundle_id}


def is_configured() -> bool:
    try:
        _config()
        return True
    except PushNotConfigured:
        return False


_token_cache: dict[str, Any] = {}


def _provider_token(cfg: dict) -> str:
    """APNs 제공자 토큰(JWT). 유효 시간이 넉넉해 55분마다 새로 만든다."""
    now = int(time.time())
    cached = _token_cache.get("value")
    if cached and _token_cache.get("expires", 0) > now:
        return cached

    import jwt  # PyJWT

    with open(cfg["key_path"], "r", encoding="utf-8") as fh:
        private_key = fh.read()
    value = jwt.encode(
        {"iss": cfg["team_id"], "iat": now},
        private_key,
        algorithm="ES256",
        headers={"kid": cfg["key_id"]},
    )
    _token_cache["value"] = value
    _token_cache["expires"] = now + 55 * 60
    return value


def _client():
    import httpx

    return httpx.Client(http2=True, timeout=20)


def send_to_devices(
    data_dir: str,
    title: str,
    body: str,
    payload_extra: dict | None = None,
    collapse_id: str | None = None,
) -> dict:
    """등록된 모든 기기에 알림을 보낸다. 결과 요약을 돌려준다."""
    cfg = _config()
    state = load_state(data_dir)
    devices = state.get("devices") or {}
    if not devices:
        return {"sent": 0, "failed": 0, "removed": 0, "detail": "등록된 기기가 없습니다."}

    aps: dict[str, Any] = {
        "alert": {"title": title, "body": body},
        "sound": "default",
        "badge": 1,
    }
    message = {"aps": aps}
    if payload_extra:
        message.update(payload_extra)
    encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")

    provider_token = _provider_token(cfg)
    headers = {
        "authorization": f"bearer {provider_token}",
        "apns-topic": cfg["bundle_id"],
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    if collapse_id:
        headers["apns-collapse-id"] = collapse_id[:64]

    sent = 0
    failed = 0
    stale: list[str] = []
    errors: list[str] = []

    with _client() as client:
        for token, info in list(devices.items()):
            host = APNS_HOSTS.get(info.get("environment") or "sandbox", APNS_HOSTS["sandbox"])
            try:
                resp = client.post(
                    f"{host}/3/device/{token}",
                    content=encoded,
                    headers={**headers, "content-type": "application/json"},
                )
            except Exception as exc:
                failed += 1
                errors.append(str(exc)[:100])
                continue

            if resp.status_code == 200:
                sent += 1
                continue

            failed += 1
            reason = ""
            try:
                reason = (resp.json() or {}).get("reason") or ""
            except Exception:
                reason = resp.text[:80]
            errors.append(f"{resp.status_code} {reason}")
            # 앱이 삭제된 기기이거나 환경이 틀린 토큰은 정리한다.
            if resp.status_code == 410 or reason in ("BadDeviceToken", "Unregistered"):
                stale.append(token)

    _drop_tokens(data_dir, stale)
    return {
        "sent": sent,
        "failed": failed,
        "removed": len(stale),
        "errors": errors[:5],
    }


def notify_new_notice(data_dir: str, detail: dict, board_name: str) -> dict:
    """새로 수집된 공지 하나를 알린다. 같은 공지는 다시 알리지 않는다."""
    notice_key = str(detail.get("notice_key") or "")
    if notice_key and already_sent(data_dir, notice_key):
        return {"sent": 0, "failed": 0, "removed": 0, "detail": "이미 알린 공지입니다."}

    title = f"[{board_name}] 새 공지"
    body = (detail.get("title") or "제목 없음").strip()
    image_count = len(((detail.get("image_result") or {}).get("generated_images") or []))
    result = send_to_devices(
        data_dir,
        title,
        body,
        payload_extra={
            "notice_key": notice_key,
            "board_id": detail.get("board_id") or "",
            "image_count": image_count,
        },
        collapse_id=notice_key or None,
    )
    if notice_key and result.get("sent"):
        mark_sent(data_dir, notice_key)
    return result
