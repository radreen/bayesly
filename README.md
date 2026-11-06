# Bayesly

Skeptical, calm, precise AI/tech news. Every post is a Bayesian update:
**what we thought before → what changed → what we think now.**

Status: **Phase 0** — the pipeline only writes drafts to a local folder.
Nothing is published automatically. The owner reviews and posts manually.

## Pipeline (Phase 0)

```
config/sources.yaml ──collect──▶ dedupe ──▶ score ──▶ draft (Gemini) ──▶ out/drafts/YYYY-MM-DD/
```

| Step    | Module               | What it does |
|---------|----------------------|--------------|
| Collect | `bayesly/collect.py` | Fetches RSS/Atom feeds. A broken feed is logged and skipped. |
| Dedupe  | `bayesly/dedupe.py`  | Same normalized URL, similar titles (Jaccard ≥ 0.8), or already drafted → dropped. Keeps the more reliable source. |
| Score   | `bayesly/score.py`   | Drops rumour headlines ("reportedly", "leak", …) and pre-releases (rc/beta). Score = 0.40·reliability + 0.35·relevance + 0.25·novelty, then: −0.35 patch release (x.y.Z), −0.15 how-to / vendor tutorial / case study, −0.10 thin text, +0.10 announcement wording ("releases", "now available", …). At most 2 picks per source per run. |
| Draft   | `bayesly/prompt.py`, `bayesly/gemini.py` | Builds the prompt from `prompts/draft.md` + `prompts/voice.md` + source text, calls Gemini (Interactions API). |
| Review  | `bayesly/review.py`  | Writes `<id>-<slug>.md` (for a human) and `.json` (status `pending_review`), plus lint warnings (length, hashtags, hype words, missing link). |

Drafts are grounded only in the text the feed provides (no page scraping yet).
If a feed only has a title, the draft says so and is flagged `insufficient_source`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
copy .env.example .env            # then put your GEMINI_API_KEY in .env
```

## Usage

```bash
python -m bayesly draft --limit 5
```

Options:
- `--limit N` — max drafts per run (default 5).
- `--sources openai-news,gh-ollama` — only these source ids.
- `--max-per-source N` — max picks from one source per run (default 2), so one busy blog can't fill the queue.
- `--skip-llm` — no Gemini call; writes review files with source text and score only. These are not marked as drafted.

Preview the ranking without writing anything or calling Gemini:

```bash
python -m bayesly rank --top 20
```

Rows marked `*` are what `draft` would pick; extra columns show penalties/bonuses.

Re-running is safe: drafted item ids are stored in `state/drafted.json` and skipped next time.
Delete that file to start fresh.

Logs are JSON lines on stderr. Set `LOG_LEVEL=DEBUG` in `.env` for more.

## Images (carousel PNGs)

Each draft can be turned into a 1080×1350 carousel using the templates in `design/`.

```bash
pip install -e ".[render]"                 # Playwright; uses your installed Edge/Chrome
python -m bayesly draft --limit 5 --render  # draft + images in one go
python -m bayesly render                    # images for all drafts that don't have them yet
python -m bayesly render --day 2026-11-06 --palette blue --force
```

Images land next to the draft: `<id>-<slug>-01.png`, `-02.png`, … and are listed in the `.md` review file.

Slides are picked from the model's `card` (see `prompts/draft.md`):

| Slide | When |
|---|---|
| Cover | always |
| Before → Now | the card has both `before` and `now` |
| Big number | the card has a number **and that exact number appears in the source text** (otherwise dropped, with a warning) |
| Confidence | always (dial + evidence for/against) |
| Closing ("We don't know yet" / "What to watch") | open questions exist, or the opinion line has no other slide |

Long text is shrunk to fit; if it still doesn't fit, the review file gets a warning.
Fonts load from Google Fonts, so rendering needs internet (otherwise fallback fonts + a warning).

Design gallery with all templates: open `design/posts.html` in a browser.

## Tests

```bash
pytest
```

Tests never call real APIs (HTTP is mocked).

## Layout

```
bayesly/        pipeline code
config/         sources.yaml
prompts/        voice.md (brand voice), draft.md (drafting prompt)
design/         bayesly.css / bayesly.js (post design system), posts.html (template gallery)
docs/           platform-notes.md (API limits, checked against official docs)
tests/          pytest
out/drafts/     generated review files (gitignored)
state/          drafted ids (gitignored)
```

## Not built yet (by design)

- Publish adapters for Threads / X — Phase 2, after the day-14 metrics check. Real publishing will require `--live`.
- Beehiiv weekly digest, metrics log — later phases.
