"""Settings from .env + sources from YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from bayesly.models import Source

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(repr=False)  # never print secrets in logs/tracebacks
    gemini_model: str
    gemini_api_revision: str
    gemini_fallback_models: list
    http_timeout: float
    gemini_timeout: float
    http_max_retries: int
    log_level: str
    sources_path: Path
    prompts_dir: Path
    out_dir: Path
    state_dir: Path


def _dir(env: str, default: Path) -> Path:
    raw = os.getenv(env, "").strip()
    if not raw:
        return default
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip(),
        gemini_fallback_models=[m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.8-flash,gemini-2.5-flash").split(",") if m.strip()],
        gemini_api_revision=os.getenv("GEMINI_API_REVISION", "2026-05-20").strip(),
        http_timeout=float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
        gemini_timeout=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "120")),
        http_max_retries=int(os.getenv("HTTP_MAX_RETRIES", "3")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        sources_path=ROOT / "config" / "sources.yaml",
        prompts_dir=ROOT / "prompts",
        # CI keeps the queue inside state/ so it survives between GitHub Actions runs.
        out_dir=_dir("OUT_DIR", ROOT / "out" / "drafts"),
        state_dir=_dir("STATE_DIR", ROOT / "state"),
    )


def load_sources(path: Path) -> list[Source]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources: list[Source] = []
    seen_ids: set[str] = set()
    for raw in data.get("sources", []):
        src = Source(
            id=raw["id"],
            name=raw["name"],
            url=raw["url"],
            kind=raw.get("kind", "official_blog"),
            reliability=float(raw.get("reliability", 0.5)),
            max_items=int(raw.get("max_items", 10)),
            cover_image=bool(raw.get("cover_image", not raw["id"].startswith(("arxiv", "gh-")))),
        )
        if src.id in seen_ids:
            raise ValueError(f"duplicate source id in {path}: {src.id}")
        if not 0.0 <= src.reliability <= 1.0:
            raise ValueError(f"reliability must be 0..1 for {src.id}")
        seen_ids.add(src.id)
        sources.append(src)
    return sources
