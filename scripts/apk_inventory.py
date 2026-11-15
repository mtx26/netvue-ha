#!/usr/bin/env python3
"""Inventaire statique et non invasif d'une APK (ZIP).

Ne lance aucun code de l'APK, ne contacte aucun réseau et ne sort aucun secret.
Les noms de domaines sont de simples indices, pas des connexions constatées.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile


DOMAIN = re.compile(
    rb"(?<![a-zA-Z0-9-])(?:[a-zA-Z0-9][a-zA-Z0-9-]{0,62}\.)+"
    rb"(?:com|net|org|io|cn|cloud|app|dev)(?![a-zA-Z0-9-])",
    re.IGNORECASE,
)
KEYWORDS = ("webrtc", "rtsp", "mqtt", "stun", "turn", "h264", "h265", "p2p", "stream", "aws", "rtc")
MAX_ENTRY_SCAN = 4 * 1024 * 1024
MAX_SCAN_ENTRIES = 80


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_apk(apk: Path) -> dict:
    result = {
        "source": apk.name,
        "size_bytes": apk.stat().st_size,
        "sha256": sha256_file(apk),
        "entry_count": 0,
        "dex_files": [],
        "native_libraries": [],
        "native_architectures": [],
        "interesting_paths": [],
        "domain_clues": [],
        "sampled_binary_entries": 0,
        "limits": [
            "Domaines extraits de chaînes embarquées : ils ne prouvent pas des communications effectives.",
            "Échantillonnage limité à 80 entrées et 4 MiB par entrée.",
            "Le manifeste Android binaire et le code DEX ne sont pas décompilés par cet outil.",
        ],
    }
    with zipfile.ZipFile(apk) as archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        result["entry_count"] = len(files)
        result["dex_files"] = sorted(i.filename for i in files if i.filename.endswith(".dex"))
        libs = sorted(i.filename for i in files if i.filename.startswith("lib/") and i.filename.endswith(".so"))
        result["native_libraries"] = libs
        result["native_architectures"] = sorted({i.split("/")[1] for i in libs if len(i.split("/")) > 2})
        result["interesting_paths"] = sorted(
            i.filename for i in files if any(k in i.filename.lower() for k in KEYWORDS)
        )[:200]

        domains: Counter[str] = Counter()
        inspected = 0
        for entry in files:
            if inspected >= MAX_SCAN_ENTRIES:
                break
            if not (entry.filename.endswith((".dex", ".so", ".json", ".txt", ".xml", ".properties"))):
                continue
            if entry.file_size == 0:
                continue
            with archive.open(entry) as content:
                blob = content.read(MAX_ENTRY_SCAN)
            inspected += 1
            for match in set(DOMAIN.findall(blob)):
                domain = match.decode("ascii", errors="ignore").lower().strip(".")
                # Éviter les chaînes démesurées ou manifestement aberrantes.
                if len(domain) <= 120 and "." in domain:
                    domains[domain] += 1
        result["sampled_binary_entries"] = inspected
        result["domain_clues"] = sorted(domains)[:200]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventaire statique d'une APK sans exécution")
    parser.add_argument("apk", type=Path, help="Chemin vers une APK provenant d'une source vérifiée")
    parser.add_argument("--output", type=Path, help="Fichier JSON de résultats (facultatif)")
    args = parser.parse_args()
    if not args.apk.is_file():
        print(f"APK introuvable : {args.apk}", file=sys.stderr)
        return 2
    try:
        data = inspect_apk(args.apk)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        print(f"Impossible d'analyser ce fichier : {exc}", file=sys.stderr)
        return 1
    report = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Inventaire enregistré : {args.output}")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
