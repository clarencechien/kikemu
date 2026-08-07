#!/usr/bin/env python3
"""Extract the actually-spoken English term list from the frozen references.

Rule (mechanical): every maximal run of latin tokens in the reference is a
term instance (e.g. "drug discovery", "one-hot vector", "GAN"). Instances
are counted; runs made only of English stop/filler words (know, so, ok...)
are excluded — they are code-switched fillers, not terms.

Judgment rules for scoring (frozen here):
  strict   — case-insensitive exact match of the term string (hyphen/space
             preserved as-is in reference).
  tolerant — strict, or hyphen<->space folding, or the frozen variants below.
Also stores each term's classification for over-translation scoring:
  no_translate  — model/architecture names, proper nouns, acronyms
  may_translate — generic technical words
Classification is done by a frozen mechanical rule: acronyms (all-caps) and
multi-word capitalized names -> no_translate; single lowercase common tech
words -> may_translate; ambiguous ones resolved by a hand list, frozen now.
"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "corpus" / "reference"
OUT = ROOT / "corpus" / "terms.json"

LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9'\- ]*[A-Za-z0-9]|[A-Za-z]")
STOP = {"know", "so", "ok", "okay", "yes", "no", "oh", "eh", "hmm", "a", "the", "and", "or", "of", "to", "is"}

# frozen hand rulings for ambiguous classification
NO_TRANSLATE_HAND = {"covid-19", "google", "openai", "chatgpt", "gpt", "bert", "gan",
                     "transformer", "seq2seq", "tesla", "apple"}
# adjacent-term runs merged by punctuation stripping; split them (frozen)
SPLIT_HAND = {"transformer chatgpt": ["Transformer", "ChatGPT"]}
VARIANTS_HAND = {
    "covid-19": ["covid 19", "covid"],
    "one-hot": ["one hot", "onehot"],
    "seq2seq": ["seq to seq", "sequence to sequence"],
    "chatgpt": ["chat gpt"],
}


def classify(term: str) -> str:
    t = term.lower()
    if t in NO_TRANSLATE_HAND:
        return "no_translate"
    words = term.split()
    if any(w.isupper() and len(w) >= 2 for w in words):
        return "no_translate"          # acronyms: GAN, AI, RNN, CNN...
    if len(words) >= 2 and all(w[0].isupper() for w in words):
        return "no_translate"          # capitalized multiword names
    return "may_translate"


def main():
    out = {}
    for seg in ["S1", "S2", "S3"]:
        text = (REF / f"{seg}.txt").read_text()
        runs = [r.strip() for r in LATIN_RUN.findall(text)]
        runs = [r for r in runs if r.lower() not in STOP and len(r) >= 2]
        # drop math-formula fragments (y = ax + b read aloud): runs where
        # every word is <=2 chars, unless the run is an all-caps acronym (AI)
        runs = [
            r for r in runs
            if not (all(len(w) <= 2 for w in r.split()) and not r.isupper())
        ]
        expanded = []
        for r in runs:
            expanded.extend(SPLIT_HAND.get(r.lower(), [r]))
        runs = expanded
        counts = Counter(r.lower() for r in runs)
        canon = {}
        for r in runs:                 # first-seen casing is canonical
            canon.setdefault(r.lower(), r)
        terms = []
        for low, n in counts.most_common():
            surface = canon[low]
            variants = [low.replace("-", " "), low.replace(" ", "")]
            variants += VARIANTS_HAND.get(low, [])
            variants = sorted({v for v in variants if v and v != low})
            terms.append({
                "term": surface,
                "count": n,
                "variants": variants,
                "class": classify(surface),
            })
        out[seg] = terms
        print(f"{seg}: {len(terms)} distinct terms, {sum(t['count'] for t in terms)} instances")
        print("  top:", [f"{t['term']}x{t['count']}" for t in terms[:12]])
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
