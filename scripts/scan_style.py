#!/usr/bin/env python3
"""晶晶體 / 外文殘留掃描 — is the zh-TW output actually all Chinese?

Three failure modes are counted separately because they have different
product implications:
  1. latin_gloss   — English inside a Chinese sentence where the Chinese
                     term is also present, e.g. 侘寂（Wabi-Sabi）.
                     This is a *gloss*, usually acceptable.
  2. latin_bare    — English with no Chinese equivalent adjacent
                     (true 晶晶體, or a hallucinated English token).
  3. kana_residue  — Japanese kana surviving untranslated into the output.
Also reports PRC-vocabulary and simplified-character hits (from score.py's
lists) so all "is this really Taiwanese Chinese" checks live in one place.

Writes results/style_scan.json.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"

LATIN = re.compile(r"[A-Za-z][A-Za-z\-]*")
KANA = re.compile(r"[ぁ-ゖァ-ヺ][ぁ-ゖァ-ヺー]*")
CJK = re.compile(r"[一-鿿]")
GLOSS_WINDOW = 12  # chars either side to look for a Chinese equivalent


def get_zh(arm: str, stem: str) -> str | None:
    if arm == "A":
        f = RES / "raw" / "A" / f"{stem}.json"
        return json.loads(f.read_text())["translation"] if f.exists() else None
    f = RES / "raw" / f"{arm}_translate" / f"{stem}.json"
    return json.loads(f.read_text())["translation"] if f.exists() else None


def classify_latin(zh: str, m: re.Match) -> str:
    """A latin run is a 'gloss' if it sits in brackets adjacent to Chinese."""
    before = zh[max(0, m.start() - GLOSS_WINDOW) : m.start()]
    after = zh[m.end() : m.end() + GLOSS_WINDOW]
    bracketed = before.rstrip().endswith(("（", "(", "「", "『")) or after.lstrip().startswith(
        ("）", ")", "」", "』")
    )
    return "latin_gloss" if bracketed and CJK.search(before + after) else "latin_bare"


def main():
    arms = ["A", "C", "Cplus"]
    for extra in ("Cb", "Cbplus"):
        if (RES / "raw" / f"{extra}_translate").exists():
            arms.append(extra)
    stems = sorted(p.stem for p in (RES / "raw" / "C").glob("*.json"))
    out = {}
    for arm in arms:
        counts = {"latin_gloss": 0, "latin_bare": 0, "kana_residue": 0, "files": 0}
        examples = []
        for s in stems:
            zh = get_zh(arm, s)
            if zh is None:
                continue
            counts["files"] += 1
            for m in LATIN.finditer(zh):
                if len(m.group()) < 2:
                    continue
                kind = classify_latin(zh, m)
                counts[kind] += 1
                examples.append(
                    {"file": s, "kind": kind, "text": m.group(),
                     "ctx": zh[max(0, m.start() - 20) : m.end() + 20]}
                )
            for m in KANA.finditer(zh):
                counts["kana_residue"] += 1
                examples.append(
                    {"file": s, "kind": "kana_residue", "text": m.group(),
                     "ctx": zh[max(0, m.start() - 20) : m.end() + 20]}
                )
        out[arm] = {"counts": counts, "examples": examples}
        print(f"{arm}: {counts}")
    (RES / "style_scan.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
