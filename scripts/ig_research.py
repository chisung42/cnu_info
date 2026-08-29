"""연구용 인스타그램 지표 수집기.

논문 분석에 필요한 데이터를 체계적으로 쌓는다. 대시보드/앱 기능과 분리해 두었고,
`instagram_sync`의 캡션 대조 로직을 재사용해 게시물을 원본 공지에 연결한다.

서브커맨드
    backfill   전체 게시물 목록과 현재 누적 지표를 한 번 훑어 저장한다(재실행 가능).
    snapshot   최근 게시물만 다시 훑어 시계열 표본을 쌓는다(주기 실행용).
    account    계정 단위 지표(팔로워 수 등)를 하루 단위로 남긴다.
    export     분석용 CSV를 만든다.

지표는 덮어쓰지 않고 매번 한 행씩 append 한다. 조회 시점(age_hours)을 함께 남기므로
분석 단계에서 1h/6h/24h/72h/7d 창에 가장 가까운 표본을 골라 쓸 수 있다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import instagram_sync as igs

GRAPH_BASE = "https://graph.instagram.com"

# 게시물 단위로 받아 두는 필드. like/comment 수는 인사이트와 별개로 목록에서도 나온다.
MEDIA_FIELDS = (
    "id,caption,permalink,timestamp,media_type,media_product_type,"
    "like_count,comments_count"
)
# 캐러셀 게시물에서 받을 수 있는 인사이트.
INSIGHT_METRICS = (
    "reach,views,saved,shares,likes,comments,total_interactions,profile_visits,follows"
)

# 게시 후 이 기간 안의 글만 snapshot 대상으로 본다. 지표는 대부분 초기에 결정된다.
SNAPSHOT_WINDOW_DAYS = 14
# 호출 사이 간격(초). 시간당 호출 한도에 걸리지 않게 여유를 둔다.
CALL_INTERVAL = 0.35
# 한도 초과를 뜻하는 Graph API 오류 코드.
RATE_LIMIT_CODES = {4, 17, 32, 613}


def research_dir(data_dir: str) -> str:
    path = os.path.join(data_dir, "research")
    os.makedirs(path, exist_ok=True)
    return path


class RateLimited(RuntimeError):
    """시간당 호출 한도에 걸린 상태. 이어서 다시 실행하면 된다."""


def api_get(path: str, params: dict, token: str) -> dict:
    merged = dict(params)
    merged["access_token"] = token
    resp = requests.get(f"{GRAPH_BASE}/{path}", params=merged, timeout=30)
    try:
        payload = resp.json() if resp.content else {}
    except ValueError:
        payload = {}
    if resp.status_code != 200:
        error = payload.get("error") or {}
        if error.get("code") in RATE_LIMIT_CODES:
            raise RateLimited(error.get("message") or "rate limited")
        raise igs.InstagramAPIError(
            f"{resp.status_code} code={error.get('code')} {error.get('message')}"
        )
    return payload


def fetch_all_media(token: str) -> list[dict]:
    """계정의 모든 게시물을 페이징해서 가져온다."""
    items: list[dict] = []
    params: dict = {"fields": MEDIA_FIELDS, "limit": 100}
    payload = api_get("me/media", params, token)
    while True:
        items.extend(item for item in (payload.get("data") or []) if isinstance(item, dict))
        next_url = ((payload.get("paging") or {}).get("next") or "").strip()
        if not next_url:
            return items
        resp = requests.get(next_url, timeout=30)
        if resp.status_code != 200:
            return items
        payload = resp.json() if resp.content else {}
        time.sleep(CALL_INTERVAL)


def fetch_insights(media_id: str, token: str) -> dict:
    payload = api_get(f"{media_id}/insights", {"metric": INSIGHT_METRICS}, token)
    values = {}
    for entry in payload.get("data") or []:
        vals = entry.get("values") or []
        if vals:
            values[entry.get("name")] = vals[0].get("value")
    return values


def fetch_child_count(media_id: str, token: str) -> int:
    payload = api_get(f"{media_id}/children", {"fields": "id"}, token)
    return len(payload.get("data") or [])


def load_notices(data_dir: str) -> list[dict]:
    path = os.path.join(data_dir, "notices_db.json")
    with open(path, "r", encoding="utf-8") as fh:
        db = json.load(fh)
    notices = []
    for key, value in db.items():
        if isinstance(value, dict):
            item = dict(value)
            item.setdefault("notice_key", key)
            notices.append(item)
    return notices


def link_media_to_notices(media: list[dict], notices: list[dict]) -> dict[str, dict]:
    """media_id -> {notice_key, confidence}. instagram_sync의 대조 결과를 뒤집는다."""
    matches, _stats = igs.match_media(media, notices)
    by_media: dict[str, dict] = {}
    for notice_key, info in matches.items():
        media_id = info.get("media_id")
        if not media_id:
            continue
        # 같은 본문이 여러 공지로 수집된 경우 한 게시물에 여러 키가 붙는다. 모두 남긴다.
        slot = by_media.setdefault(
            media_id, {"notice_keys": [], "confidence": info.get("confidence", "")}
        )
        slot["notice_keys"].append(notice_key)
    return by_media


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        if text.endswith("+0000"):
            text = text[:-5] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def append_jsonl(path: str, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def collect(data_dir: str, only_recent: bool, with_children: bool, limit: int) -> dict:
    """게시물 목록을 새로 받고, 대상 게시물의 인사이트를 한 행씩 기록한다."""
    token = igs.refresh_token_if_needed(data_dir)
    out = research_dir(data_dir)
    index_path = os.path.join(out, "media_index.json")
    metrics_path = os.path.join(out, "metrics.jsonl")

    media = fetch_all_media(token)
    notices = load_notices(data_dir)
    links = link_media_to_notices(media, notices)

    index = igs._read_json(index_path)
    now = datetime.now(timezone.utc)

    for item in media:
        media_id = item.get("id")
        if not media_id:
            continue
        entry = index.get(media_id, {})
        entry.update(
            {
                "media_id": media_id,
                "permalink": item.get("permalink") or "",
                "timestamp": item.get("timestamp") or "",
                "media_type": item.get("media_type") or "",
                "media_product_type": item.get("media_product_type") or "",
                "caption": item.get("caption") or "",
                "notice_keys": links.get(media_id, {}).get("notice_keys", []),
                "match_confidence": links.get(media_id, {}).get("confidence", ""),
            }
        )
        index[media_id] = entry

    targets = []
    for item in media:
        posted = parse_ts(item.get("timestamp") or "")
        if not posted:
            continue
        age_hours = (now - posted).total_seconds() / 3600.0
        if only_recent and age_hours > SNAPSHOT_WINDOW_DAYS * 24:
            continue
        targets.append((item, age_hours))

    # 오래된 것부터 채운다. 중간에 한도에 걸려도 다음 실행이 이어받는다.
    targets.sort(key=lambda pair: pair[1], reverse=True)
    if only_recent:
        targets.sort(key=lambda pair: pair[1])

    done = 0
    skipped_done = 0
    rate_limited = False
    for item, age_hours in targets:
        media_id = item["id"]
        entry = index[media_id]
        if not only_recent and entry.get("backfilled_at"):
            skipped_done += 1
            continue
        if limit and done >= limit:
            break
        try:
            values = fetch_insights(media_id, token)
        except RateLimited:
            rate_limited = True
            break
        except igs.InstagramAPIError as exc:
            append_jsonl(
                metrics_path,
                {
                    "media_id": media_id,
                    "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "age_hours": round(age_hours, 3),
                    "error": str(exc),
                },
            )
            entry["backfilled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry["insights_error"] = str(exc)
            done += 1
            time.sleep(CALL_INTERVAL)
            continue

        row = {
            "media_id": media_id,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "posted_at": item.get("timestamp") or "",
            "age_hours": round(age_hours, 3),
            "like_count": item.get("like_count"),
            "comments_count": item.get("comments_count"),
        }
        row.update(values)
        append_jsonl(metrics_path, row)
        entry["backfilled_at"] = row["collected_at"]
        entry.pop("insights_error", None)
        done += 1
        time.sleep(CALL_INTERVAL)

        if with_children and not entry.get("child_count"):
            try:
                entry["child_count"] = fetch_child_count(media_id, token)
            except RateLimited:
                rate_limited = True
                igs._write_json(index_path, index)
                break
            except igs.InstagramAPIError:
                pass
            time.sleep(CALL_INTERVAL)

    igs._write_json(index_path, index)

    linked = sum(1 for e in index.values() if e.get("notice_keys"))
    remaining = sum(1 for e in index.values() if not e.get("backfilled_at"))
    return {
        "media_total": len(media),
        "indexed": len(index),
        "linked_to_notice": linked,
        "collected_now": done,
        "already_done": skipped_done,
        "remaining": remaining,
        "rate_limited": rate_limited,
    }


def snapshot_account(data_dir: str) -> dict:
    token = igs.refresh_token_if_needed(data_dir)
    out = research_dir(data_dir)
    payload = api_get(
        "me",
        {"fields": "username,account_type,media_count,followers_count,follows_count"},
        token,
    )
    row = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "followers_count": payload.get("followers_count"),
        "follows_count": payload.get("follows_count"),
        "media_count": payload.get("media_count"),
        "account_type": payload.get("account_type"),
    }
    append_jsonl(os.path.join(out, "account.jsonl"), row)
    return row


# ---------------------------------------------------------------------------
# 내보내기
# ---------------------------------------------------------------------------

WINDOWS = [1, 6, 24, 72, 168]
METRIC_NAMES = ["views", "reach", "saved", "shares", "likes", "comments",
                "total_interactions", "profile_visits", "follows"]


def export_csv(data_dir: str, path: str) -> dict:
    out = research_dir(data_dir)
    index = igs._read_json(os.path.join(out, "media_index.json"))
    notices = {n["notice_key"]: n for n in load_notices(data_dir)}

    samples: dict[str, list[dict]] = {}
    metrics_path = os.path.join(out, "metrics.jsonl")
    if os.path.exists(metrics_path):
        with open(metrics_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("error"):
                    continue
                samples.setdefault(row.get("media_id", ""), []).append(row)

    # 팔로워 히스토리: 게시 시점의 팔로워 수를 근사한다.
    follower_hist = []
    acct_path = os.path.join(out, "account.jsonl")
    if os.path.exists(acct_path):
        with open(acct_path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = parse_ts(row.get("collected_at", ""))
                if ts and row.get("followers_count") is not None:
                    follower_hist.append((ts, row["followers_count"]))
    follower_hist.sort()

    def followers_at(when: datetime | None):
        if not when or not follower_hist:
            return ""
        best = ""
        for ts, count in follower_hist:
            if ts <= when:
                best = count
            else:
                break
        return best

    header = [
        "media_id", "permalink", "posted_at", "media_type", "child_count",
        "caption_length", "match_confidence", "notice_key", "board_name",
        "notice_title", "title_length", "notice_date", "crawled_at",
        "content_length", "attachment_count", "png_count",
        "latency_source_to_crawl_h", "latency_crawl_to_post_h", "latency_total_h",
        "posted_hour_kst", "posted_weekday", "posted_month",
        "followers_at_post", "n_samples", "last_collected_at", "last_age_hours",
    ]
    for metric in METRIC_NAMES:
        header.append(f"{metric}_last")
    for window in WINDOWS:
        for metric in ["views", "reach", "saved", "shares"]:
            header.append(f"{metric}_{window}h")

    rows = []
    for media_id, entry in index.items():
        posted = parse_ts(entry.get("timestamp", ""))
        keys = entry.get("notice_keys") or []
        notice = notices.get(keys[0]) if keys else None

        notice_date = parse_ts((notice or {}).get("date", "")) if notice else None
        crawled = parse_ts((notice or {}).get("crawled_at", "")) if notice else None

        def hours(a, b):
            if not a or not b:
                return ""
            return round((b - a).total_seconds() / 3600.0, 3)

        media_samples = sorted(samples.get(media_id, []), key=lambda r: r.get("age_hours", 0))
        last = media_samples[-1] if media_samples else {}

        kst_hour = ""
        weekday = ""
        month = ""
        if posted:
            kst = posted.astimezone(timezone.utc)
            kst_hour = (kst.hour + 9) % 24
            weekday = posted.weekday()
            month = posted.strftime("%Y-%m")

        row = {
            "media_id": media_id,
            "permalink": entry.get("permalink", ""),
            "posted_at": entry.get("timestamp", ""),
            "media_type": entry.get("media_type", ""),
            "child_count": entry.get("child_count", ""),
            "caption_length": len(entry.get("caption") or ""),
            "match_confidence": entry.get("match_confidence", ""),
            "notice_key": keys[0] if keys else "",
            "board_name": (notice or {}).get("board_name", ""),
            "notice_title": (notice or {}).get("title", ""),
            "title_length": len((notice or {}).get("title") or "") if notice else "",
            "notice_date": (notice or {}).get("date", ""),
            "crawled_at": (notice or {}).get("crawled_at", ""),
            "content_length": len((notice or {}).get("content") or "") if notice else "",
            "attachment_count": len((notice or {}).get("attachments") or []) if notice else "",
            "png_count": len((notice or {}).get("png_files") or []) if notice else "",
            "latency_source_to_crawl_h": hours(notice_date, crawled),
            "latency_crawl_to_post_h": hours(crawled, posted),
            "latency_total_h": hours(notice_date, posted),
            "posted_hour_kst": kst_hour,
            "posted_weekday": weekday,
            "posted_month": month,
            "followers_at_post": followers_at(posted),
            "n_samples": len(media_samples),
            "last_collected_at": last.get("collected_at", ""),
            "last_age_hours": last.get("age_hours", ""),
        }
        for metric in METRIC_NAMES:
            row[f"{metric}_last"] = last.get(metric, "")
        for window in WINDOWS:
            # 해당 창에 가장 가까운 표본. 창을 크게 벗어나면 비워 둔다.
            best = None
            best_gap = None
            for sample in media_samples:
                gap = abs(sample.get("age_hours", 0) - window)
                if gap <= max(window * 0.35, 1.0) and (best_gap is None or gap < best_gap):
                    best, best_gap = sample, gap
            for metric in ["views", "reach", "saved", "shares"]:
                row[f"{metric}_{window}h"] = (best or {}).get(metric, "")
        rows.append(row)

    rows.sort(key=lambda r: r["posted_at"])
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return {"rows": len(rows), "path": path,
            "linked": sum(1 for r in rows if r["notice_key"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",
                        choices=["backfill", "snapshot", "account", "export", "status"])
    parser.add_argument("--data-dir", default=os.environ.get("CNUINFO_DATA_DIR", "data"))
    parser.add_argument("--limit", type=int, default=0,
                        help="이번 실행에서 인사이트를 받을 게시물 수 상한(0=제한 없음)")
    parser.add_argument("--children", action="store_true",
                        help="캐러셀 장수도 같이 받는다(호출 수가 두 배가 된다)")
    parser.add_argument("--out", default="", help="export 결과 CSV 경로")
    args = parser.parse_args()

    data_dir = args.data_dir
    if args.command == "backfill":
        result = collect(data_dir, only_recent=False,
                         with_children=args.children, limit=args.limit)
    elif args.command == "snapshot":
        result = collect(data_dir, only_recent=True,
                         with_children=args.children, limit=args.limit)
    elif args.command == "account":
        result = snapshot_account(data_dir)
    elif args.command == "export":
        out = args.out or os.path.join(research_dir(data_dir), "posts.csv")
        result = export_csv(data_dir, out)
    else:
        out = research_dir(data_dir)
        index = igs._read_json(os.path.join(out, "media_index.json"))
        metrics_path = os.path.join(out, "metrics.jsonl")
        n_rows = 0
        if os.path.exists(metrics_path):
            with open(metrics_path, "r", encoding="utf-8") as fh:
                n_rows = sum(1 for line in fh if line.strip())
        result = {
            "indexed": len(index),
            "backfilled": sum(1 for e in index.values() if e.get("backfilled_at")),
            "linked_to_notice": sum(1 for e in index.values() if e.get("notice_keys")),
            "with_child_count": sum(1 for e in index.values() if e.get("child_count")),
            "metric_rows": n_rows,
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
