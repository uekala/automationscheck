#!/usr/bin/env python3
"""Validate the published Home Assistant integration version metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manifest_path = ROOT / "custom_components" / "automationscheck" / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

if manifest.get("domain") != "automationscheck":
    raise SystemExit("manifest domain must be 'automationscheck'")

version = manifest.get("version")
if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
    raise SystemExit(f"Invalid Home Assistant manifest version: {version!r}")

changelog_path = ROOT / "CHANGELOG.md"
if changelog_path.exists():
    headings = re.findall(
        r"^##\s+(\d+\.\d+\.\d+)(?:\s|$)",
        changelog_path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not headings or headings[0] != version:
        actual = headings[0] if headings else "no version heading"
        raise SystemExit(f"CHANGELOG.md starts at {actual}, manifest version is {version}")

print(f"Automationscheck manifest version {version} is valid.")
