"""Settings from .env + sources from YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

from bayesly.models import Source

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    gemini_api_revision: str
    http_timeout: float
    http_max_retries: int
    log_level: str
    sources_path: Path
    prompts_dir: Path
    out_dir: Path
    state_dir: Path


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
        gemini_api_revision=os.getenv("GEMINI_API_REVISION", "2026-05-20").strip(),
        http_timeout=float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
        http_max_retries=int(os.getenv("HTTP_MAX_RETRIES", "3")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        sources_path=ROOT / "config" / "sources.yaml",
        prompts_dir=ROOT / "prompts",
        out_dir=ROOT / "out" / "drafts",
        state_dir=ROOT / "state",
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
        )
        if src.id in seen_ids:
            raise ValueError(f"duplicate source id in {path}: {src.id}")
        if not 0.0 <= src.reliability <= 1.0:
            raise ValueError(f"reliability must be 0..1 for {src.id}")
        seen_ids.add(src.id)
        sources.append(src)
    return sources
