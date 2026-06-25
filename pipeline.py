#!/usr/bin/env python3
"""Fréttvaldur — personal Icelandic news filter."""

import json
import os
import pathlib
import sys
import time
import urllib.request
import urllib.error

import feedparser
import trafilatura

ROOT = pathlib.Path(__file__).parent
DOCS = ROOT / "docs"
STATE = ROOT / "state"
DOCS.mkdir(exist_ok=True)
STATE.mkdir(exist_ok=True)

ARTICLES_FILE = DOCS / "articles.json"
SEEN_FILE = STATE / "seen.json"

API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

MAX_PER_RUN = 40
SLEEP_BETWEEN_CALLS = 6.0
MAX_KEPT = 250

CATEGORIES = {
    "arkitektur": "Architecture & design",
    "fasteignir": "Real estate",
    "skipulag":   "Planning & construction",
    "efnahagur":  "Economy & policy",
    "frettir":    "Hard news",
    "menning":    "Culture",
}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def item_id(entry):
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def in_dropped_section(entry, drop_sections):
    tags = [t.get("term", "") for t in entry.get("tags", []) if isinstance(t, dict)]
    blob = " ".join(tags + [entry.get("category", "")]).lower()
    return any(s in blob for s in drop_sections)


def feed_image(entry):
    """Pull an image URL straight from the RSS entry, if present."""
    for key in ("media_content", "media_thumbnail"):
        v = entry.get(key)
        if v and isinstance(v, list) and v[0].get("url"):
            return v[0]["url"]
    for enc in entry.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image") and enc.get("href"):
            return enc["href"]
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            return link.get("href", "")
    return ""


def fetch_article(url, fallback_text=""):
    """Return (text, image) for one article."""
    text, image = fallback_text[:6000], ""
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            t = trafilatura.extract(downloaded, include_comments=False,
                                    favor_precision=True)
            if t and len(t) > 120:
                text = t[:6000]
            try:
                md = trafilatura.extract_metadata(downloaded)
                if md and md.image:
                    image = md.image
            except Exception:
                pass
    except Exception:
        pass
    return text, image


def gemini_post(payload):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent?key={API_KEY}")
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 3:
                time.sleep(12 * (attempt + 1))
                continue
            raise


def gemini_judge(preferences, source, trusted, title, body):
    trusted_note = (
        "This source only publishes Icelandic building-industry news, so it is "
        "PRE-APPROVED: set keep=true. Still categorise, summarise and spin-check it."
        if trusted else
        "Decide keep vs discard against the preferences. When it sits inside one of "
        "the owner's topics but you are unsure, LEAN TOWARD keeping it."
    )
    prompt = f"""You curate a personal Icelandic news feed. Judge ONE article
against the owner's preferences. The article is in Icelandic — read idiom in
context, never translate word-for-word.

=== OWNER PREFERENCES ===
{preferences}
=== END PREFERENCES ===

{trusted_note}

Pick the single best category key from:
arkitektur, fasteignir, skipulag, efnahagur, frettir, menning

Write the summary in clear English, 2-3 sentences, plain language, jargon removed.
Set spin_flag=true ONLY if the headline oversells, misleads, or is one-sided versus
the body; if so, spin_note explains the gap in one sentence (else empty string).

SOURCE: {source}
TITLE: {title}
BODY:
{body}
"""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "keep":      {"type": "boolean"},
                    "category":  {"type": "string"},
                    "summary":   {"type": "string"},
                    "spin_flag": {"type": "boolean"},
                    "spin_note": {"type": "string"},
                    "reason":    {"type": "string"},
                },
                "required": ["keep", "category", "summary", "spin_flag",
                             "spin_note", "reason"],
            },
        },
    }
    data = gemini_post(payload)
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    out = json.loads(text)
    if out.get("category") not in CATEGORIES:
        out["category"] = "frettir"
    return out


def main():
    if not API_KEY:
        sys.exit("GEMINI_API_KEY is not set.")

    config = load_json(ROOT / "feeds.json", {})
    preferences = (ROOT / "preferences.md").read_text(encoding="utf-8")
    drop_sections = [s.lower() for s in config.get("drop_sections", [])]
    seen = set(load_json(SEEN_FILE, []))
    kept = load_json(ARTICLES_FILE, {}).get("items", [])
    kept_ids = {a["id"] for a in kept}

    candidates = []
    print("--- feeds ---")
    for feed in config.get("feeds", []):
        parsed = feedparser.parse(feed["url"])
        n_total = len(parsed.entries)
        n_new = 0
        for entry in parsed.entries:
            eid = item_id(entry)
            if not eid or eid in seen:
                continue
            if not feed.get("trusted") and in_dropped_section(entry, drop_sections):
                seen.add(eid)
                continue
            candidates.append((feed, entry, eid))
            n_new += 1
        flag = "  <-- EMPTY, check this feed URL" if n_total == 0 else ""
        print(f"  {feed['name']}: {n_total} in feed, {n_new} new{flag}")

    candidates = candidates[:MAX_PER_RUN]
    print(f"--- judging {len(candidates)} ---")

    added = 0
    for feed, entry, eid in candidates:
        title = entry.get("title", "").strip()
        text, image = fetch_article(entry.get("link", ""),
                                    fallback_text=entry.get("summary", ""))
        if not image:
            image = feed_image(entry)
        try:
            verdict = gemini_judge(preferences, feed["name"],
                                   feed.get("trusted", False), title, text)
        except Exception as e:
            print(f"  ! skip (retry next run) {title[:50]}: {e}")
            continue

        seen.add(eid)
        if verdict.get("keep") and eid not in kept_ids:
            kept.append({
                "id": eid, "title": title, "link": entry.get("link", ""),
                "source": feed["name"], "image": image,
                "published": entry.get("published", "") or entry.get("updated", ""),
                "category": verdict["category"], "summary": verdict["summary"],
                "spin_flag": verdict["spin_flag"], "spin_note": verdict["spin_note"],
                "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            kept_ids.add(eid)
            added += 1
            print(f"  + KEEP [{verdict['category']}] {title[:55]}")
        else:
            print(f"  - drop {title[:55]}")
        time.sleep(SLEEP_BETWEEN_CALLS)

    kept.sort(key=lambda a: a.get("added_at", ""), reverse=True)
    kept = kept[:MAX_KEPT]

    ARTICLES_FILE.write_text(json.dumps(
        {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "items": kept}, ensure_ascii=False, indent=2), encoding="utf-8")
    SEEN_FILE.write_text(json.dumps(list(seen)[-5000:]), encoding="utf-8")
    print(f"Done. {added} kept this run, {len(kept)} in feed.")


if __name__ == "__main__":
    main()
