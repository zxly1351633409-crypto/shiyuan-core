from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "runtime-data"}
SKIP_SUFFIXES = {
    ".7z",
    ".db",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".npz",
    ".pdf",
    ".png",
    ".pyc",
    ".sqlite",
    ".sqlite3",
    ".zip",
}

STANDARD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Windows user profile path", re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+")),
    (
        "private IPv4 address",
        re.compile(
            r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|"
            r"192\.168\.(?:\d{1,3}\.)\d{1,3}|"
            r"172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})\b"
        ),
    ),
    ("mainland China mobile number", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    (
        "credential-shaped token",
        re.compile(
            r"(?i)(?:gh[pousr]_[A-Za-z0-9_]{20,}|"
            r"github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|"
            r"bearer\s+[A-Za-z0-9._-]{20,})"
        ),
    ),
)


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == "MANIFEST.sha256":
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def audit(root: Path, deny_terms: list[str]) -> list[str]:
    findings: list[str] = []
    patterns = list(STANDARD_PATTERNS)
    patterns.extend(
        (f"custom deny term {index + 1}", re.compile(re.escape(term), re.IGNORECASE))
        for index, term in enumerate(deny_terms)
        if term
    )
    for path in iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in patterns:
                if pattern.search(line):
                    findings.append(f"{relative}:{line_number}: {label}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a public release contains common identity or secret markers."
    )
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument(
        "--deny-term",
        action="append",
        default=[],
        help="Case-insensitive private term to reject. Repeat as needed.",
    )
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    findings = audit(root, arguments.deny_term)
    if findings:
        print("Public release audit failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Public release audit passed: no standard private markers found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
