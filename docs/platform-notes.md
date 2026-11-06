# Platform notes

Rule: check the current official docs before implementing anything against a platform API.
Record what was checked, when, and the link.

## Gemini (drafting) — checked 2026-11-06

- Docs: https://aistudio.google.com/docs/text-generation (ai.google.dev now redirects here)
- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/interactions`
- Headers: `x-goog-api-key: <key>`, `Api-Revision: 2026-05-20`, `Content-Type: application/json`
- Body used: `model`, `input` (string), `generation_config.max_output_tokens`, `store: false` (stateless)
- Model in the docs examples: `gemini-3.8-flash` (configurable via `GEMINI_MODEL`)
- Gemini 4 / 3.6+ ignore `temperature`, `top_p`, `top_k` and return deprecation headers — we don't send them.
- Response: interaction object with `steps`; text read from the last non-thought step's `content[].text`.
  The docs page did not show a raw response body — **verify on the first real call** and adjust `extract_text` if needed.
- Pricing / rate limits: **not checked yet.** Check the pricing page before running at volume.

## Threads — NOT CHECKED (Phase 2)

- Post length limit used in lint: 500 characters — from memory, **verify in official docs** before Phase 2.
- API, auth, rate limits: to check before implementing `adapters/threads.py`.

## X — NOT CHECKED (Phase 2)

- Post length limit used in lint: 280 characters (standard account) — **verify**.
- API tier, pricing and limits change often: check before implementing `adapters/x.py`. Paid tier = ask the owner first.

## Beehiiv — NOT CHECKED (later)

- Draft-only creation via API; human sends. Check API availability per plan before building.
