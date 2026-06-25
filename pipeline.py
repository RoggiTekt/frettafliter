#!/usr/bin/env python3
"""
Fréttafilter — personal Icelandic news filter.

Runs on a schedule (GitHub Actions). For each new article it:
  1. drops anything in a sport/gossip section for free (no AI),
  2. fetches the full article body,
  3. asks Gemini to decide keep/discard + category + summary + spin-check,
     judged against preferences.md,
  4. writes the survivors to docs/articles.json for the web app to read.

You only edit preferences.md. You should not need to touch this file.
"""

import json
import os
import pathlib
import sys
import time
import urllib.request

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

# Keep the run gentle on the free tier and quick: process at most this many
# brand-new articles per run. Anything beyond is picked up next hour.
MAX_PER_RUN = 40
SLEEP_BETWEEN_CALLS = 4.0          # seconds, stays under the free-tier RPM ceiling
MAX_KEPT = 250                     # cap the size of the feed the app reads

CATEGORIES = {
    "arkitektur": "Architecture & design",
    "fasteignir": "Real estate",
    "skipulag":   "Planning & construction",
    "efnahagur":  "Economy & policy",
    "frettir":    "Hard news",
    "menning":    "Culture",
}


# ----------------------------------------------------------------------------- helpers

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


def fetch_body(url, fallback=""):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False,
                                       favor_precision=True)
            if text and len(text) > 120:
                return text[:6000]
    except Exception:
        pass
    return fallback[:6000]


# ----------------------------------------------------------------------------- Gemini

def gemini_judge(preferences, source, trusted, title, body):
    """Return a dict: keep, category, summary, spin_flag, spin_note, reason."""
    trusted_note = (
        "This source only publishes Icelandic building-industry news, so it is "
        "PRE-APPROVED: set keep=true. Still categorise, summarise and spin-check it."
        if trusted else
        "Decide keep vs discard strictly against the preferences."
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

Write the summary in clear English, 2–3 sentences, plain language, jargon removed.
Set spin_flag=true ONLY if the headline oversells or misleads versus the body;
if so, spin_note explains the gap in one sentence (else empty string).

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

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent?key={API_KEY}")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")

    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    out = json.loads(text)
    if out.get("category") not in CATEGORIES:
        out["category"] = "frettir"
    return out


# ----------------------------------------------------------------------------- main

def main():
    if not API_KEY:
        sys.exit("GEMINI_API_KEY is not set.")

    config = load_json(ROOT / "feeds.json", {})
    preferences = (ROOT / "preferences.md").read_text(encoding="utf-8")
    drop_sections = [s.lower() for s in config.get("drop_sections", [])]
    seen = set(load_json(SEEN_FILE, []))
    kept = load_json(ARTICLES_FILE, {}).get("items", [])
    kept_ids = {a["id"] for a in kept}

    # gather brand-new candidates
    candidates = []
    for feed in config.get("feeds", []):
        parsed = feedparser.parse(feed["url"])
        for entry in parsed.entries:
            eid = item_id(entry)
            if not eid or eid in seen:
                continue
            if not feed.get("trusted") and in_dropped_section(entry, drop_sections):
                seen.add(eid)                      # junk section: drop, never AI it
                continue
            candidates.append((feed, entry, eid))

    candidates = candidates[:MAX_PER_RUN]
    print(f"{len(candidates)} new article(s) to judge")

    added = 0
    for feed, entry, eid in candidates:
        seen.add(eid)
        title = entry.get("title", "").strip()
        body = fetch_body(entry.get("link", ""),
                          fallback=entry.get("summary", ""))
        try:
            verdict = gemini_judge(preferences, feed["name"],
                                   feed.get("trusted", False), title, body)
        except Exception as e:
            print(f"  ! skip (API error) {title[:50]}: {e}")
            continue

        if verdict.get("keep") and eid not in kept_ids:
            kept.append({
                "id": eid,
                "title": title,
                "link": entry.get("link", ""),
                "source": feed["name"],
                "published": entry.get("published", "")
                             or entry.get("updated", ""),
                "category": verdict["category"],
                "summary": verdict["summary"],
                "spin_flag": verdict["spin_flag"],
                "spin_note": verdict["spin_note"],
                "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            kept_ids.add(eid)
            added += 1
            print(f"  + KEEP [{verdict['category']}] {title[:60]}")
        else:
            print(f"  - drop {title[:60]}")

        time.sleep(SLEEP_BETWEEN_CALLS)

    # newest first, capped
    kept.sort(key=lambda a: a.get("added_at", ""), reverse=True)
    kept = kept[:MAX_KEPT]

    ARTICLES_FILE.write_text(json.dumps(
        {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "items": kept}, ensure_ascii=False, indent=2), encoding="utf-8")
    SEEN_FILE.write_text(json.dumps(list(seen)[-5000:]), encoding="utf-8")

    print(f"Done. {added} kept this run, {len(kept)} in feed.")


if __name__ == "__main__":
    main()
