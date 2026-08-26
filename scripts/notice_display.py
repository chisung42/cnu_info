"""Shared display/copy text helpers for CNU Info notices."""

from __future__ import annotations


NOTICE_HASHTAGS = "#충남대학교 #충남대 #충대 #cnu"

_BOARD_HEADERS = {
    "general": "[충남대학교 일반소식]",
    "academics": "[충남대학교 학사정보]",
    "education": "[충남대학교 교육정보]",
    "startup": "[충남대학교 사업단 창업ㆍ교육]",
    "recruitment": "[충남대학교 채용/초빙]",
    "scholarship": "[충남대학교 장학정보]",
}


def get_board_header(board_id: str) -> str:
    """Return the heading used above a notice body in the dashboard."""
    return _BOARD_HEADERS.get(board_id, f"[{board_id}]")


def make_display_content(board_id: str, content: str | None) -> str:
    """Build the exact text used by the dashboard's '본문 복사' control."""
    board_header = get_board_header(board_id or "default")
    if content:
        return f"{board_header}\n{content}\n\n{NOTICE_HASHTAGS}"
    return f"{board_header}\n\n{NOTICE_HASHTAGS}"
