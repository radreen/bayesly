# Platform notes

Rule: check the current official docs before implementing anything against a platform API.
Record what was checked, when, and the link.

## Gemini (drafting) — checked 2026-11-06, verified live 2026-11-14

- Docs: https://aistudio.google.com/docs/text-generation (ai.google.dev now redirects here)
- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/interactions`
- Headers: `x-goog-api-key: <key>`, `Api-Revision: 2026-05-20`, `Content-Type: application/json`
- Body used: `model`, `input` (string), `generation_config.max_output_tokens` (8192: thinking tokens share it), `store: false`
- **Live response shape (2026-11-14):** `{status, usage, steps: [{type: "thought", signature}, {type: "model_output", content: [{type: "text", text}]}], model, ...}`.
  `extract_text` skips `thought` steps and reads `model_output` text — verified.
- **Availability (2026-11-14):** `gemini-3.8-flash`, `gemini-3.7-flash` and `gemini-flash-latest` returned 503 / timed out;
  `gemini-3.5-flash` answered in ~17 s. Default is now `gemini-3.5-flash` with `GEMINI_FALLBACK_MODELS` tried in order.
- Gemini 4 / 3.6+ ignore `temperature`, `top_p`, `top_k` and return deprecation headers — we don't send them.
- Pricing / rate limits: **not checked yet.** Check the pricing page before running at volume.

## Threads — checked 2026-11-14

- Docs: https://developers.facebook.com/documentation/threads/posts.md
- Text: 500 characters (emojis count by UTF-8 byte length).
- Image: JPEG or PNG, ≤ 8 MB, **public `image_url`** (Threads fetches it).
- Carousel: 2–20 items. Steps: item containers (`is_carousel_item=true`) → `media_type=CAROUSEL` container with
  `children` + `text` → `POST /{user-id}/threads_publish` with `creation_id`. Wait ~30 s after creating containers.
- Permissions: `threads_basic`, `threads_content_publish`. Without advanced access, only the app's Threads testers
  can be posted for — fine for our own account (add it as a tester).
- Rate limit: 250 posts per rolling 24 h per profile; a carousel counts as one.
- Cost: free.

## Instagram — checked 2026-11-20 (re-checked against the live App Dashboard)

- Docs: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login
  and https://developers.facebook.com/documentation/instagram-platform/content-publishing
- **New apps can't use "API setup with Instagram login"** (dashboard: "not available for newer apps"); they use
  **Unified login**: host `graph.facebook.com` (docs examples v27.0), a **system-user access token (SUAT)** that
  belongs to the business portfolio; its expiry is chosen in the login configuration. **No Facebook Page needed.**
- IG account id: `GET graph.facebook.com/v27.0/me/assigned_instagram_accounts?fields=id,username` (`/me` returns the
  system user, not the IG account).
- Permissions for publishing: `instagram_basic`, `instagram_content_publish`. Standard Access is enough for accounts
  that have a role on the app (we add @bayesly.ai as Instagram Tester); Advanced Access + Live mode only for others.
- Container status: poll `status_code` (docs: about once a minute, max 5 minutes).
- Carousel: up to 20 items. Item containers (`is_carousel_item=true`) → `media_type=CAROUSEL` container with `children`
  + `caption` → `POST /<IG_ID>/media_publish` with `creation_id`.
- Images: **JPEG only**, public URL at publishing time; all carousel images are cropped to the first image's ratio
  (we send 4:5, 1080×1350).
- Rate limit: the page says 100 API posts per 24 h in one place and 50 in another — check
  `GET /<IG_ID>/content_publishing_limit`; we post ~3/day, far below both.
- Captions: links are not clickable on Instagram, so the caption names the source in text.
- Cost: free.

## Tokens (Threads + Instagram) — checked 2026-11-19
- Threads host in current docs is **graph.threads.com** (code default; override with THREADS_API_BASE).
- Short-lived token: 1 hour. Long-lived: **60 days**. Exchange: `GET graph.threads.com/access_token?grant_type=th_exchange_token&client_secret=..&access_token=..` (server-side).
- Refresh (token ≥ 24 h old, not expired): Threads `GET graph.threads.com/refresh_access_token?grant_type=th_refresh_token`. (Instagram now uses a system-user token from Unified login: no 60-day refresh.) Returns a **new** `access_token` + `expires_in` (~60 days). An expired token can't be refreshed.
- Threads permission grants last 90 days; refreshing extends them only for public profiles (keep @bayesly.ai public).
- We refresh weekly in CI (`refresh-tokens --live --github-secrets`) and write the new token to GitHub Secrets; failures go to Telegram.

## X — checked 2026-11-19

- Docs: https://docs.x.com/x-api/getting-started/pricing
- **Pay per use, no subscription, no minimum spend.** Create a post: **$0.015 without a URL, $0.200 with a URL.**
  Read: $0.005 per post. Media upload is not listed (closest: "Media Metadata" $0.005) — **measure on the first live post**.
- Promo: $20 in API credits after saving a card and making a first purchase.
- **Our rule: X posts carry no URL** (source named in text) → ~3 posts/day ≈ $1.4/month.
- Text limit used in lint: 280 characters (standard account).
- Auth: OAuth 1.0a user context (consumer key/secret + access token/secret of @bayesly_ai) for `POST /2/tweets`
  and media upload — confirm on the first live call.

## TikTok — checked 2026-11-18 (not built yet)

- Docs: https://developers.tiktok.com/doc/content-posting-api-get-started
- Content Posting API supports video and multi-image photo posts; free.
- **Unaudited apps can only post privately**; public posting needs a TikTok audit (not guaranteed for a single-account tool).
- Photos only via `PULL_FROM_URL` from a **verified domain** → needs our own domain first.

## Beehiiv — NOT CHECKED (later)

- Draft-only creation via API; human sends. Check API availability per plan before building.

## Vertical video — checked 2026-11-20
- **Instagram Reels:** `media_type=REELS` + `video_url` (public), MP4/MOV, H.264/HEVC, AAC ≤48 kHz, 23–60 fps,
  3 s–15 min, ≤300 MB; poll `status_code` (~1/min, ≤5 min). We send `share_to_feed=true`.
- **Threads:** `media_type=VIDEO` + `video_url` (public), ≤5 min, ≤1 GB; wait ~30 s before publish.
- **X:** chunked upload v2: `POST /2/media/upload/initialize` (JSON: media_type, total_bytes, media_category=tweet_video)
  → `/{id}/append` (multipart, segment_index, media ≤5 MB) → `/{id}/finalize` → poll `GET /2/media/upload?command=STATUS`.
  Hard limit 1280×1024 per side pair, recommended 720×1280 → we upload a separate 720×1280 copy.
  OAuth 1.0a support for these endpoints is NOT confirmed in the docs (they show OAuth2 user token);
  if it fails, the bot posts the image instead and says so in Telegram.
- **TikTok:** unaudited apps can only post privately → needs the Content Posting API audit first.
- **YouTube Shorts:** `videos.insert`, 100 uploads/day default; needs a Google OAuth setup (not built yet).
- **Hosting:** GitHub raw serves .mp4 as `application/octet-stream`; jsDelivr serves `video/mp4` → video URLs use
  `cdn.jsdelivr.net/gh/<repo>@<commit sha>/<path>` (sha-pinned so the first request is fresh).
- **Voice (Gemini TTS):** free tier = 10 requests/day per model (429 "per day"). One request per video;
  fallback `gemini-3.8-flash-lite-tts` (own quota). If both are out: silent video.

## Stats and comments (read-only, checked 2026-11-21)
- `check --live` also polls comments every 15 min (`bayesly/metrics.py`): Threads `GET /{id}/replies`, Instagram `GET /{id}/comments`, posts of the last 7 days. Each new comment goes to Telegram once, with an "Open post" button; the first run only records existing ones. Seen ids: `ci-state/comments.json`.
- Morning summary once a day after `SUMMARY_HOUR_UTC` (default 6 = 08:00 Kyiv in winter): Threads views/likes/replies/reposts/shares + followers (`threads_insights`), Instagram likes/comments + followers. Daily snapshots in `ci-state/metrics.json` (30 days), deltas vs yesterday.
- Instagram views/reach need `instagram_manage_insights` (not granted). X stats need a paid tier: skipped.
- Threads `topic_tag`: 1–50 chars, no "." or "&", one per post, set on the post container (not carousel items).
- Manual: `python -m bayesly metrics` (send now) or `metrics --print`.
