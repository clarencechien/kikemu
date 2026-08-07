#!/usr/bin/env python3
"""Build final reference transcripts from page text + 2-engine verification.

Rules (pre-registered, applied uniformly):
  R1. Reference base = official page text of the picked section(s), reading
      glosses (pure-kana parentheticals) removed, punctuation stripped.
  R2. The narrated section title is prepended when at least one verification
      engine's transcript begins with text matching the header (ratio>=0.5).
  R3. For excerpt audio, the reference is trimmed to the spoken subrange
      located by alignment against the Speechmatics verification pass.
  R4. Where BOTH engines insert the exact same string at the same reference
      position, the insertion is adopted (narration adds words not on page).
  R5. All other page-vs-engine differences keep the PAGE orthography.
      Rationale: they are homophone orthography choices by the ASR; adopting
      them would bias the reference toward ASR-preferred spellings.
  All residual diffs are written to corpus/reference/review.md for human
  inspection; corrections beyond R1-R5 go into corpus/reference/overrides.json.
"""
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_reference import normalize, cer, spoken_subrange

ROOT = Path(__file__).resolve().parent.parent
VER = ROOT / "corpus" / "verify"
REF = ROOT / "corpus" / "reference"

HEADER_PREFIX_RE = re.compile(r"^[A-Z]?-?\d+(-\d+)?[｜|:：]\s*|^EX[｜|:：]\s*|^★[｜|:：]?\s*")


def norm_header(h: str) -> str:
    return normalize(HEADER_PREFIX_RE.sub("", h))


def agreed_insertions(page: str, sm: str, gm: str) -> list[tuple[int, str]]:
    def inserts(hyp):
        smx = difflib.SequenceMatcher(None, page, hyp, autojunk=False)
        return {
            i1: hyp[j1:j2]
            for op, i1, i2, j1, j2 in smx.get_opcodes()
            if op == "insert" and i1 > 0 and i2 < len(page)
        }
    a, b = inserts(sm), inserts(gm)
    return sorted((pos, txt) for pos, txt in a.items() if b.get(pos) == txt)


def main():
    REF.mkdir(parents=True, exist_ok=True)
    arts = {a["slug"]: a for a in json.loads((ROOT / "corpus" / "articles.json").read_text())}
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    overrides_path = REF / "overrides.json"
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}

    review = ["# Reference review — residual page-vs-engine diffs\n"]
    meta = {}
    for seg_id, spec in picks.items():
        secs = [arts[spec["slug"]]["sections"][i] for i in spec["sections"]]
        page = normalize("\n".join(s["text"] for s in secs))
        sm = normalize((VER / f"{seg_id}.speechmatics.txt").read_text(), strip_gloss=False)
        gm = normalize((VER / f"{seg_id}.gemini.txt").read_text(), strip_gloss=False)

        # R3: trim to spoken subrange (tolerate full coverage)
        a, b = spoken_subrange(page, sm)
        coverage = (b - a) / len(page)
        if coverage > 0.9:
            a, b = 0, len(page)
        page = page[a:b]

        # R2: narrated title
        header = norm_header(secs[0]["header"])
        title_used = False
        for hyp in (sm, gm):
            r = difflib.SequenceMatcher(None, header, hyp[: len(header) + 4]).ratio()
            if r >= 0.5:
                title_used = True
        ref = (header + page) if title_used else page

        # R4: agreed insertions
        ins = agreed_insertions(ref, sm, gm)
        for pos, txt in reversed(ins):
            ref = ref[:pos] + txt + ref[pos:]

        # Manual overrides (from human review), applied literally
        for old, new in overrides.get(seg_id, []):
            assert old in ref, (seg_id, old)
            ref = ref.replace(old, new, 1)

        (REF / f"{seg_id}.txt").write_text(ref, encoding="utf-8")
        meta[seg_id] = {
            "chars": len(ref),
            "coverage": round(coverage, 3),
            "title_prepended": title_used,
            "agreed_insertions": ins,
            "cer_ref_vs_sm": round(cer(ref, sm), 4),
            "cer_ref_vs_gm": round(cer(ref, gm), 4),
        }
        review.append(f"\n## {seg_id}  (cov={coverage:.2f}, title={title_used}, ins={ins})\n")
        for name, hyp in (("SM", sm), ("GM", gm)):
            smx = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
            for op, i1, i2, j1, j2 in smx.get_opcodes():
                if op != "equal":
                    review.append(f"- {op}: ref='{ref[i1:i2]}' | {name}='{hyp[j1:j2]}'\n")
        print(seg_id, meta[seg_id])

    (REF / "review.md").write_text("".join(review), encoding="utf-8")
    (REF / "reference_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
