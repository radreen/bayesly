"""Command line entry point: python -m bayesly draft --limit 5"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from bayesly.config import load_settings
from bayesly.gemini import GeminiClient, GeminiError
from bayesly.logs import setup_logging
from bayesly.models import Item
from bayesly.pipeline import DEFAULT_MAX_PER_SOURCE, RunResult, collect_and_rank, run_draft
from bayesly.score import select

log = logging.getLogger("bayesly")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bayesly", description="Bayesly: drafts and (with --live) autoposting.")
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="collect -> dedupe -> score -> draft -> out/drafts/")
    draft.add_argument("--limit", type=int, default=5, help="max drafts to write (default 5)")
    draft.add_argument(
        "--skip-llm",
        action="store_true",
        help="don't call Gemini; write review files with source + score only (not marked as drafted)",
    )

    draft.add_argument("--render", action="store_true", help="also render carousel PNGs for new drafts")
    draft.add_argument("--palette", choices=["lime", "blue"], default="lime", help="accent colour for --render")

    render = sub.add_parser("render", help="render carousel PNGs for drafts in out/drafts/")
    render.add_argument("paths", nargs="*", help="draft .json files (default: all drafts without images)")
    render.add_argument("--day", help="only drafts from this folder, e.g. 2026-11-06")
    render.add_argument("--palette", choices=["lime", "blue"], default="lime")
    render.add_argument("--force", action="store_true", help="re-render even if images exist")

    auto = sub.add_parser("autopost", help="publish the best fresh draft to all platforms (dry-run unless --live)")
    auto.add_argument("--live", action="store_true", help="REALLY publish. Without it nothing is posted or sent.")
    auto.add_argument("--count", type=int, default=None, help="max posts this run (default POSTS_PER_RUN)")
    auto.add_argument("--draft", type=int, default=0, metavar="N", help="first fetch news and write up to N new drafts")
    auto.add_argument("--palette", choices=["lime", "blue"], default="lime")

    tg = sub.add_parser("telegram-sync", help="apply Publish/Skip taps and /pause /resume from Telegram")
    tg.add_argument("--live", action="store_true", help="required: consumes updates and changes state")
    rt = sub.add_parser("refresh-tokens", help="renew the 60-day Threads token")
    rt.add_argument("--live", action="store_true", help="required: really calls the refresh endpoints")
    rt.add_argument("--github-secrets", action="store_true",
                    help="store the new tokens as GitHub Actions secrets via `gh secret set` (CI only)")
    sub.add_parser("accounts", help="read-only: show which Threads/Instagram accounts the tokens in .env belong to")
    sub.add_parser("pause", help="kill switch: stop all publishing (creates state/PAUSED)")
    sub.add_parser("resume", help="remove the kill switch")
    sub.add_parser("status", help="paused?, held posts, what was posted")

    preview = sub.add_parser("rank", help="print the current ranking; writes nothing, calls no LLM")
    preview.add_argument("--top", type=int, default=20, help="how many rows to print (default 20)")
    preview.add_argument("--limit", type=int, default=5, help="draft limit to simulate for the * marks")

    for p in (draft, preview):
        p.add_argument("--sources", default="", help="comma-separated source ids from config/sources.yaml")
        p.add_argument(
            "--max-per-source",
            type=int,
            default=DEFAULT_MAX_PER_SOURCE,
            help=f"max picks from one source (default {DEFAULT_MAX_PER_SOURCE})",
        )
    return parser


def print_ranking(ranked: list[Item], picked: list[Item], top: int) -> None:
    picked_ids = {id(i) for i in picked}
    for item in ranked[:top]:
        mark = "*" if id(item) in picked_ids else " "
        extras = {k: v for k, v in item.score_breakdown.items() if k not in {"reliability", "relevance", "novelty"}}
        b = item.score_breakdown
        print(
            f"{mark} {item.score:.3f}  {item.source.id:<20} {item.title[:70]:<70}  "
            f"rel={b['reliability']} topic={b['relevance']} new={b['novelty']} {extras or ''}"
        )
    print("\n* = would be drafted with the current --limit / --max-per-source")


def find_drafts(out_dir: Path, day: str | None) -> list[Path]:
    root = out_dir / day if day else out_dir
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def render_paths(paths: list[Path], palette: str, force: bool) -> tuple[int, int]:
    """Render each draft; a failure is reported and skipped. Returns (ok, failed)."""
    from bayesly.render import RenderError, render_record

    ok = failed = 0
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not record.get("draft"):
                continue  # --skip-llm review files: nothing to render
            images = render_record(path, palette=palette, force=force)
            print(f"  {path.name}: {len(images)} images")
            ok += 1
        except RenderError as exc:
            print(f"  {path.name}: render failed: {exc}", file=sys.stderr)
            failed += 1
    return ok, failed


def make_generator(settings) -> GeminiClient:
    return GeminiClient(
        settings.gemini_api_key,
        settings.gemini_model,
        settings.gemini_api_revision,
        timeout=settings.gemini_timeout,
        fallback_models=settings.gemini_fallback_models,
        max_retries=settings.http_max_retries,
    )


def show_accounts() -> int:
    """Read-only GETs that print account ids/usernames (never tokens), for filling *_USER_ID."""
    from bayesly import net
    from bayesly.publish.base import load_publish_config, scrub

    cfg = load_publish_config()
    checks = [
        ("threads", cfg.threads_token, f"{cfg.threads_base}/me", {"fields": "id,username"}),
        ("instagram", cfg.instagram_token, f"{cfg.instagram_base}/me/assigned_instagram_accounts", {"fields": "id,username"}),
    ]
    for name, token, url, params in checks:
        if not token:
            print(f"{name}: no token in .env")
            continue
        try:
            data = net.request("GET", url, timeout=20, max_retries=1, params={**params, "access_token": token}).json()
        except (net.ExternalCallError, ValueError) as exc:
            print(f"{name}: error: {scrub(str(exc), token)[:300]}")
            continue
        rows = data.get("data", [data])
        for row in rows:
            print(f"{name}: id={row.get('id')} username={row.get('username')}")
    return 0


def refresh_tokens(args) -> int:
    """Never prints a token. Locally: tells you to update .env; in CI: writes GitHub secrets."""
    import subprocess

    from bayesly.publish.base import PublishError, load_publish_config
    from bayesly.publish.meta import refresh_token
    from bayesly.telegram import Telegram, TelegramError

    if not args.live:
        print("refresh-tokens calls Meta and replaces tokens; run it with --live", file=sys.stderr)
        return 2
    cfg = load_publish_config()
    # Instagram (Unified login) uses a system-user token: nothing to refresh here.
    targets = {"threads": ("THREADS_ACCESS_TOKEN", cfg.threads_token)}
    problems = []
    for platform, (env_name, token) in targets.items():
        if platform not in cfg.platforms or not token:
            continue
        try:
            new, expires = refresh_token(platform, token)
        except PublishError as exc:
            problems.append(f"{platform}: {exc}")
            continue
        if args.github_secrets:
            done = subprocess.run(["gh", "secret", "set", env_name], input=new, text=True, capture_output=True)
            if done.returncode != 0:
                problems.append(f"{platform}: gh secret set failed: {done.stderr.strip()[:200]}")
                continue
            print(f"{platform}: refreshed, valid {expires // 86400} days, secret {env_name} updated")
        else:
            env_path = Path(".env")
            text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
            lines = [l for l in text.splitlines() if not l.startswith(f"{env_name}=")] + [f"{env_name}={new}"]
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"{platform}: refreshed, valid {expires // 86400} days, written to .env")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        try:
            Telegram(cfg.telegram_token, cfg.telegram_chat_id).send(
                "Bayesly: token refresh FAILED. Posting stops when the token expires.\n" + "\n".join(problems))
        except TelegramError:
            pass
        return 1
    return 0


def run_publishing(args, settings) -> int:
    from bayesly.autopost import HeldStore, build_autoposter, is_paused, set_paused
    from bayesly.publish.base import Ledger, load_publish_config

    cfg = load_publish_config()
    if args.command == "pause":
        set_paused(settings, True, "via CLI")
        print("paused: nothing will be published until `python -m bayesly resume`")
        return 0
    if args.command == "resume":
        set_paused(settings, False)
        print("resumed" + (" (but BAYESLY_PAUSED is set in the environment)" if cfg.paused_env else ""))
        return 0
    if args.command == "status":
        held = HeldStore(settings.state_dir).load()
        print(f"paused: {is_paused(settings, cfg)}  platforms: {', '.join(cfg.platforms)}")
        for rid, h in held.items():
            print(f"  held {rid}: {h.get('status')}  {'; '.join((h.get('reasons') or [])[:2])}")
        print(f"posted drafts: {len(Ledger(settings.state_dir)._load())}")
        return 0

    live = bool(getattr(args, "live", False))
    poster = build_autoposter(settings, cfg, live=live, palette=getattr(args, "palette", "lime"))
    if args.command == "telegram-sync":
        if not live:
            print("telegram-sync changes state; run it with --live", file=sys.stderr)
            return 2
        for line in poster.sync_telegram() or ["no updates"]:
            print(line)
        return 0

    # autopost
    if is_paused(settings, cfg):
        print("paused (kill switch). Nothing done.")
        return 0
    if args.draft:
        try:
            result = run_draft(settings, limit=args.draft, generator=make_generator(settings))
        except GeminiError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"drafted {len(result.written)} new, failed {len(result.failed)}")
        render_paths(result.written, args.palette, force=False)
    count = args.count if args.count is not None else cfg.posts_per_run
    print(("LIVE" if live else "DRY-RUN") + f": up to {count} post(s) -> {', '.join(cfg.platforms)}")
    report = poster.run(count)
    for line in report.lines():
        print(f"  {line}")
    failed = any(not r.ok for _, results in report.published for r in results)
    return 1 if failed or report.errors else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_level)

    if args.command == "refresh-tokens":
        return refresh_tokens(args)
    if args.command == "accounts":
        return show_accounts()
    if args.command in {"autopost", "telegram-sync", "pause", "resume", "status"}:
        return run_publishing(args, settings)

    if args.command == "render":
        paths = [Path(p) for p in args.paths] or find_drafts(settings.out_dir, args.day)
        ok, failed = render_paths(paths, args.palette, args.force)
        print(f"rendered={ok} failed={failed}")
        return 1 if failed and not ok else 0

    if args.max_per_source < 1:
        print("--max-per-source must be >= 1", file=sys.stderr)
        return 2
    source_ids = {s.strip() for s in args.sources.split(",") if s.strip()} or None

    if args.command == "rank":
        ranked = collect_and_rank(settings, RunResult(), source_ids=source_ids)
        print_ranking(ranked, select(ranked, args.limit, args.max_per_source), args.top)
        return 0

    if args.command == "draft":
        if args.limit < 1:
            print("--limit must be >= 1", file=sys.stderr)
            return 2
        generator = None
        if not args.skip_llm:
            try:
                generator = make_generator(settings)
            except GeminiError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        result = run_draft(
            settings,
            limit=args.limit,
            generator=generator,
            max_per_source=args.max_per_source,
            source_ids=source_ids,
        )
        print(
            f"collected={result.collected} unique={result.unique} ranked={result.ranked} "
            f"written={len(result.written)} failed={len(result.failed)}"
        )
        for path in result.written:
            print(f"  {path.with_suffix('.md')}")
        if args.render and result.written and generator is not None:
            render_paths(result.written, args.palette, force=False)
        return 1 if result.failed and not result.written else 0
    return 2
