# Bayesly — project brief for Claude Code

## What this project is
Bayesly is an automated, English-language AI/tech news brand for an international audience.
- Channels: **Threads (primary)**, **X (secondary)**, **Beehiiv newsletter** (weekly digest).
- Monetization (later): affiliate links and sponsorships. Do NOT build ads/monetization code now.
- Owner: Artem (designer / AI creator, Lviv). Owner's English is B1 — all public copy must be reviewed by a human before it goes out.

This is a **separate repo from Amberon**. Do not import code from Amberon and do not touch its repo.

## Brand voice ("Bayesly" = Bayes + -ly)
Skeptical, calm, precise. The core idea is a Bayesian update: **what we thought before → what changed → what we think now.**
- Every post answers: what happened, why it matters, how confident are we.
- Say "we don't know yet" when it's true. No hype words ("revolutionary", "game-changer", "insane").
- No emojis spam (max 1 per post, usually 0). No hashtags on Threads, max 1–2 on X.
- Short sentences. One idea per post. Numbers and sources beat adjectives.
- Never invent facts, quotes, benchmarks or prices. If a source doesn't say it, the post doesn't say it.
- Every post has an author voice line: one sentence of opinion/uncertainty from "us", clearly marked as opinion.

### Post template (Threads / X)
```
[Headline fact in one sentence.]

Before: [what was assumed / previous state]
Now: [what changed, with a number if available]
Confidence: [low / medium / high] — [one-line reason]

Source: [link]
```
Vary the shape so the feed doesn't look robotic; the template is a skeleton, not a cage.

### Weekly rubric: "Tested"
Once a week one post/newsletter section where a tool or claim is actually tested (or honestly marked "not tested, here's what the vendor claims"). Format: Claim → What we tried → Result → Verdict (Worth it / Wait / Skip). Never fake a test.

## Content pipeline (target architecture)
1. **Collect** — RSS / official blogs / release notes / arXiv / GitHub releases. Config-driven list of sources in `config/sources.yaml`.
2. **Dedupe + score** — drop duplicates and rumours; score by novelty, source reliability, relevance to AI/tech.
3. **Draft** — Gemini writes ~80% of the draft using the voice rules above (`prompts/draft.md`). The prompt must receive the source text and must refuse to add facts that aren't in it.
4. **Human review** — drafts land in a review queue (file/JSON or simple CLI). Nothing is published without approval in phase 1.
5. **Publish** — adapters for Threads and X (`adapters/threads.py`, `adapters/x.py`), each behind a common interface `publish(post) -> result`. Dry-run mode is the default.
6. **Newsletter** — weekly digest generated from the week's approved posts, pushed to Beehiiv (draft only, human sends).
7. **Metrics** — log post id, time, platform, and later impressions/likes/replies/follows for the day-14 review.

## Phases (follow in order, don't skip ahead)
- **Phase 0 (now):** repo skeleton, config, prompts, dry-run pipeline that produces drafts to a local folder. Owner posts manually.
- **Phase 1 (days 1–14):** owner posts manually from drafts; we only improve draft quality and log what was posted.
- **Phase 2 (after the day-14 metrics check):** semi-automation — publish adapters with approval step.
- **Phase 3:** full automation only if quality and metrics justify it. Keep a kill switch.

## Tech preferences
- Language: Python 3.11+ (unless the owner says otherwise). Keep dependencies minimal and boring.
- Config via `.env` + YAML. **Never commit secrets.** Provide `.env.example`.
- Everything idempotent: re-running the pipeline must not double-post. Store posted ids.
- Structured logs; every external call has a timeout, retry with backoff, and a clear error.
- Tests for: dedupe, scoring, prompt assembly, publish adapters (mocked). No tests that hit real APIs by default.
- Platform APIs change (Threads API, X API pricing/limits, Beehiiv API): **check the current official docs before implementing** and note limits in `docs/platform-notes.md`. Don't assume from memory.

## Rules for you (Claude Code)
- Ask before: adding paid services, anything that posts publicly, anything that touches real accounts.
- Default to dry-run. A real publish must require an explicit flag like `--live`.
- Never fabricate source data, metrics, or test results.
- Keep changes small and commit in logical steps with clear messages.
- When unsure about brand voice, open `prompts/voice.md` and follow it; if it's missing, create it from the "Brand voice" section above first.
- Communicate with the owner in Ukrainian or Russian; all public-facing content, prompts and code comments in English.

## Open items (owner does manually)
- Handles registered: Threads/Instagram **@bayesly.ai**, X **@bayesly_ai** (see docs/accounts.md). Still open: domain (bayesly.com/.ai/.co) and bayesly.beehiiv.com.
- Logo/brand mark is being designed separately (lime-green, bold single-colour style).
- Day-14 metrics check decides whether to move to Phase 2.

## First task
Create the repo skeleton from "Content pipeline" and "Phases → Phase 0": folders, `config/sources.yaml` with ~10 reliable AI/tech sources, `prompts/voice.md` and `prompts/draft.md`, a dry-run CLI (`python -m bayesly draft --limit 5`) that collects → dedupes → drafts with Gemini → writes review files to `out/drafts/`, plus `.env.example`, `README.md` and basic tests. Then stop and report.
