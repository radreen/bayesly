"""Command line entry point: python -m bayesly draft --limit 5"""

from __future__ import annotations

import argparse
import logging
import sys

from bayesly.config import load_settings
from bayesly.gemini import GeminiClient, GeminiError
from bayesly.logs import setup_logging
from bayesly.models import Item
from bayesly.pipeline import DEFAULT_MAX_PER_SOURCE, RunResult, collect_and_rank, run_draft
from bayesly.score import select

log = logging.getLogger("bayesly")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bayesly", description="Bayesly drafting pipeline (Phase 0, dry-run).")
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="collect -> dedupe -> score -> draft -> out/drafts/")
    draft.add_argument("--limit", type=int, default=5, help="max drafts to write (default 5)")
    draft.add_argument(
        "--skip-llm",
        action="store_true",
        help="don't call Gemini; write review files with source + score only (not marked as drafted)",
    )

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_level)

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
                generator = GeminiClient(
                    settings.gemini_api_key,
                    settings.gemini_model,
                    settings.gemini_api_revision,
                    timeout=settings.http_timeout,
                    max_retries=settings.http_max_retries,
                )
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
        return 1 if result.failed and not result.written else 0
    return 2
