"""Command line entry point: python -m bayesly draft --limit 5"""

from __future__ import annotations

import argparse
import logging
import sys

from bayesly.config import load_settings
from bayesly.gemini import GeminiClient, GeminiError
from bayesly.logs import setup_logging
from bayesly.pipeline import run_draft

log = logging.getLogger("bayesly")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bayesly", description="Bayesly drafting pipeline (Phase 0, dry-run).")
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="collect -> dedupe -> score -> draft -> out/drafts/")
    draft.add_argument("--limit", type=int, default=5, help="max drafts to write (default 5)")
    draft.add_argument("--sources", default="", help="comma-separated source ids from config/sources.yaml")
    draft.add_argument(
        "--skip-llm",
        action="store_true",
        help="don't call Gemini; write review files with source + score only (not marked as drafted)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_level)

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
        source_ids = {s.strip() for s in args.sources.split(",") if s.strip()} or None
        result = run_draft(settings, limit=args.limit, generator=generator, source_ids=source_ids)
        print(
            f"collected={result.collected} unique={result.unique} ranked={result.ranked} "
            f"written={len(result.written)} failed={len(result.failed)}"
        )
        for path in result.written:
            print(f"  {path.with_suffix('.md')}")
        return 1 if result.failed and not result.written else 0
    return 2
