#!/usr/bin/env python3
"""Prüft, ob die Version in manifest.json zur ersten Version in CHANGELOG.md passt."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manifest_path = ROOT / "custom_components" / "automationscheck" / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

if manifest.get("domain") != "automationscheck":
    raise SystemExit("manifest.json: domain muss 'automationscheck' sein")

version = manifest.get("version")
if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
    raise SystemExit(f"manifest.json: ungültige Version {version!r}, erwartet x.y.z")

changelog_path = ROOT / "CHANGELOG.md"
if changelog_path.exists():
    headings = re.findall(
        r"^##\s+(\d+\.\d+\.\d+)(?:\s|$)",
        changelog_path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not headings or headings[0] != version:
        actual = headings[0] if headings else "keine Versionsüberschrift"
        raise SystemExit(f"CHANGELOG.md beginnt mit {actual}, manifest.json hat Version {version}")

print(f"Version {version} in manifest.json und CHANGELOG.md stimmt überein.")
