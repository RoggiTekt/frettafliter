# Fréttafilter

A personal Icelandic news feed, filtered by AI to your taste. Polls RÚV, mbl,
Vísir, DV and byggingar.is every hour, drops the junk, summarises the rest, and
serves it as an installable app on your phone. Runs entirely on free tiers.

You edit **one file** to tune it: `preferences.md`.

---

## What you do once (≈15 min)

### 1 — Get a free Gemini API key
- Go to **aistudio.google.com → Get API key**. No credit card.
- Copy the key.

### 2 — Put this project on GitHub
- Make a free account at github.com.
- **New repository** → name it e.g. `frettafilter` → Private is fine → Create.
- Upload all these files (drag the whole folder into the repo's upload page),
  keeping the structure (`docs/`, `state/`, `.github/`). Commit.

### 3 — Add your key as a secret
- Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `GEMINI_API_KEY`   Value: paste your key. Save.

### 4 — Turn on the app page
- Repo → **Settings → Pages** → Source: *Deploy from a branch* →
  Branch `main`, folder `/docs` → Save.
- After a minute your app lives at `https://<your-username>.github.io/frettafilter/`

### 5 — Run it the first time
- Repo → **Actions** tab → "Update feed" → **Run workflow**.
- Watch it finish (a couple of minutes). It commits `docs/articles.json`.
- Open the Pages URL on your phone → Share → **Add to Home Screen**. Done.

From now on it updates itself every hour. Press "Run workflow" any time you're
impatient.

---

## Tuning it
- Edit **`preferences.md`** (right on github.com, even from your phone). Add
  obsessions, add discard examples. Next run uses them.
- In the app, **Meira svona / Ekki sýna** save your reactions. Tap *Sækja
  umsagnir* to copy them, then paste into `preferences.md` so the filter learns.

## If a feed looks empty or wrong
- A source's RSS URL may differ — open `feeds.json` and fix the `url`. (Tell me
  which source and I'll find the correct feed address.)

## Cost
- Gemini free tier (~1,500 requests/day) and GitHub's free tier cover this at
  personal volume. Expect €0.
