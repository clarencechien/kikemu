#!/usr/bin/env python3
"""Fetch audio-travel article pages from osaka-info.jp, parse (section, text, mp3)
tuples, download mp3s for selected segments, and build corpus/manifest.json.

Rate-limited, custom UA, no parallel fetching (per handoff licensing notes).
"""
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
RAW = CORPUS / "raw"
PAGES = CORPUS / "pages"

UA = "Mozilla/5.0 (kikemu-eval; private evaluation; contact clarence.chien@gmail.com)"
BASE = "https://osaka-info.jp/local_journey/audio-travel/"
DELAY_S = 3.0

ARTICLES = [
    "higashiosaka_01",
    "higashiosaka_02",
    "higashiosaka_03",
    "sakai_01-02",
    "sakai_03-04",
    "sakai_05-06",
    "ikeda_01",
    "ikeda_02",
    "ikeda_03",
]


def get(url: str) -> requests.Response:
    time.sleep(DELAY_S)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r


def parse_article(html: str, slug: str) -> dict:
    """Return {title, sections:[{header, text, mp3}]} for one article page.

    Page structure: inside <section class=wpcontainer>, each guide section is
    <h2>header</h2> <p>...</p>* <figure class=wp-block-audio><audio src=mp3>.
    The paragraphs BEFORE an audio figure (since the previous figure/h2) are
    that audio's narration text.
    """
    soup = BeautifulSoup(html, "html.parser")
    title = soup.find("title").get_text().split(" - ")[0].strip()
    sec = soup.find("main").find("section", class_="wpcontainer")

    sections = []
    header = None
    paras: list[str] = []
    for child in sec.children:
        name = getattr(child, "name", None)
        if name == "h2":
            header = child.get_text(" ", strip=True)
            paras = []
        elif name == "p":
            t = child.get_text("", strip=True)
            # Skip boilerplate paragraphs (app promo / disclaimers)
            if t and not t.startswith("※") and "ON THE TRIP" not in t[:40]:
                if header is not None:
                    paras.append(t)
        elif name == "figure" and child.find("audio"):
            mp3 = child.find("audio")["src"]
            if header is not None:
                sections.append({"header": header, "text": "\n".join(paras), "mp3": mp3})
            paras = []
    return {"slug": slug, "title": title, "sections": sections}


def main():
    PAGES.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    out = []
    for slug in ARTICLES:
        cache = PAGES / f"{slug}.html"
        if cache.exists():
            html = cache.read_text(encoding="utf-8")
        else:
            print(f"fetch {slug} ...", file=sys.stderr)
            html = get(BASE + slug + "/").text
            cache.write_text(html, encoding="utf-8")
        art = parse_article(html, slug)
        out.append(art)
        for i, s in enumerate(art["sections"]):
            print(f"{slug} [{i}] {s['header']}  text={len(s['text'])}ch  mp3=...{s['mp3'][-16:]}")
    (CORPUS / "articles.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    # Detect trap #2: same mp3 used by multiple sections
    seen = {}
    for art in out:
        for s in art["sections"]:
            seen.setdefault(s["mp3"], []).append(f"{art['slug']}:{s['header']}")
    for mp3, users in seen.items():
        if len(users) > 1:
            print(f"WARNING shared mp3 ...{mp3[-16:]} used by: {users}")


if __name__ == "__main__":
    main()
