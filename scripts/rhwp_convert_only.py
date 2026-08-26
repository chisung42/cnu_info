#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

HWP_EXTENSIONS = (".hwp", ".hwpx")
HWP_CFB_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
HWPX_ZIP_MAGIC = b"PK\x03\x04"


def _bundled_rhwp_paths() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    return [
        root / "vendor" / "rhwp" / "target" / "release" / "rhwp",
        root / "cnu_info_codex" / "vendor" / "rhwp" / "target" / "release" / "rhwp",
    ]


def find_rhwp() -> str | None:
    configured = os.environ.get("RHWP_CLI", "").strip()
    candidates: list[str] = []
    if configured and Path(configured).exists():
        candidates.append(configured)

    path_candidate = shutil.which("rhwp")
    if path_candidate:
        candidates.append(path_candidate)

    for bundled in _bundled_rhwp_paths():
        if bundled.exists():
            candidates.append(str(bundled))

    for fallback in (
        Path.home() / ".cargo" / "bin" / "rhwp",
        Path("/opt/homebrew/bin/rhwp"),
        Path("/usr/local/bin/rhwp"),
    ):
        if fallback.exists():
            candidates.append(str(fallback))

    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def validate_hwp_payload(input_path: str) -> tuple[bool, str]:
    lower = input_path.lower()
    if not lower.endswith(HWP_EXTENSIONS):
        return False, "Only .hwp and .hwpx files are supported."

    try:
        with open(input_path, "rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        return False, f"Cannot read input file: {exc}"

    if lower.endswith(".hwp") and head.startswith(HWP_CFB_MAGIC):
        return True, ""
    if lower.endswith(".hwpx") and head.startswith(HWPX_ZIP_MAGIC):
        return True, ""

    preview = head.decode("utf-8", errors="replace").strip()
    return False, f"Input does not look like a valid HWP/HWPX file: {preview or head.hex()}"


def convert_hwp_to_pdf_with_rhwp(input_path: str, output_pdf: str) -> bool:
    valid, reason = validate_hwp_payload(input_path)
    if not valid:
        print(f"[error] {reason}")
        return False

    rhwp = find_rhwp()
    if not rhwp:
        print(
            "[error] rhwp CLI not found. Set RHWP_CLI, install rhwp on PATH, "
            "or build vendor/rhwp (or cnu_info_codex/vendor/rhwp)."
        )
        return False

    output_path = Path(output_pdf)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [rhwp, "export-pdf", input_path, "-o", str(output_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        message = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        print(f"[error] rhwp failed: {message or 'unknown error'}")
        return False

    if not output_path.exists() or output_path.stat().st_size <= 0:
        print("[error] rhwp finished but PDF output was not created.")
        return False

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert one HWP/HWPX file to PDF using rhwp only.")
    parser.add_argument("input", help="Input .hwp or .hwpx file")
    parser.add_argument("-o", "--output", required=True, help="Output PDF path")
    args = parser.parse_args()

    ok = convert_hwp_to_pdf_with_rhwp(args.input, args.output)
    if ok:
        print(f"[ok] {args.output}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
