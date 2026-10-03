#!/usr/bin/env python3
"""Check relative Markdown links: the target file exists, the anchor exists,
and a link inside a skill stays inside that skill's directory."""

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|<img[^>]*\ssrc=\"([^\"]+)\"")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")

failures = []


def strip_code(text):
    lines = []
    inside = False
    for line in text.splitlines():
        if FENCE.match(line):
            inside = not inside
            lines.append("")
            continue
        lines.append("" if inside else re.sub(r"`[^`]*`", "", line))
    return lines


def slug(heading):
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = re.sub(r"[`*_]", "", heading).strip().lower()
    heading = re.sub(r"[^\w\- ]", "", heading)
    return heading.replace(" ", "-")


def anchors(path):
    found = set()
    counts = {}
    for line in strip_code(path.read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if not match:
            continue
        base = slug(match.group(1))
        index = counts.get(base, 0)
        counts[base] = index + 1
        found.add(base if index == 0 else f"{base}-{index}")
    return found


def skill_root(path):
    parts = path.relative_to(ROOT).parts
    if len(parts) > 2 and parts[0] == "skills":
        return ROOT / parts[0] / parts[1]
    return None


documents = [
    *ROOT.glob("*.md"),
    *ROOT.glob("docs/**/*.md"),
    *ROOT.glob("skills/**/*.md"),
]

for document in sorted(documents):
    relative = document.relative_to(ROOT)
    for number, line in enumerate(strip_code(document.read_text(encoding="utf-8")), 1):
        for match in LINK.finditer(line):
            target = match.group(1) or match.group(2)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                continue
            path_part, _, anchor = target.partition("#")
            resolved = (document.parent / unquote(path_part)).resolve() if path_part else document
            if not resolved.exists():
                failures.append(f"{relative}:{number}: missing target {target}")
                continue
            root = skill_root(document)
            if root is not None and root.resolve() not in (resolved, *resolved.parents):
                failures.append(f"{relative}:{number}: link leaves the skill directory: {target}")
            if anchor and resolved.suffix == ".md" and unquote(anchor) not in anchors(resolved):
                failures.append(f"{relative}:{number}: missing anchor {target}")

if failures:
    for failure in failures:
        print(f"FAIL {failure}")
    print(f"\nLinks summary: {len(failures)} failures")
    sys.exit(1)

print(f"Links summary: {len(documents)} Markdown files checked")
