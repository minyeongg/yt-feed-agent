"""Application configuration.

Loads `config/config.yaml` (non-secret settings) and `.env` (secrets) and
merges them into a single `Config` object. All relative paths in
config.yaml are resolved against the project root (the directory
containing pyproject.toml), regardless of the current working directory.

See docs/05-구현가이드.md, Phase 0 step 2.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


def find_project_root(start: Path | None = None) -> Path:
    """Walk upward from `start` (default: this file) until a directory
    containing pyproject.toml is found. Falls back to the cwd."""
    current = (start or Path(__file__)).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd()


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787


class PollingConfig(BaseModel):
    interval_min: int = 60


class RagConfig(BaseModel):
    level: int = 0
    embedding_model: str = "BAAI/bge-m3"


class CostConfig(BaseModel):
    daily_limit_usd: float = 0.5
    monthly_limit_usd: float = 5.0


class LlmConfig(BaseModel):
    small_model: str = "claude-haiku-4-5-20251001"
    large_model: str = "claude-sonnet-5"


class Config(BaseModel):
    project_root: Path
    data_dir: Path
    db_path: Path

    server: ServerConfig = Field(default_factory=ServerConfig)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    rag: RagConfig = Field(default_factory=RagConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)

    # Secrets, loaded from .env — never stored in config.yaml.
    anthropic_api_key: str | None = None
    youtube_api_key: str | None = None

    # Local server auth token (docs/03-API명세.md §5.2). Generated on
    # first run and persisted with 600 permissions.
    server_token: str = ""


def _read_or_create_server_token(config_dir: Path) -> str:
    token_path = config_dir / "token"
    if token_path.exists():
        return token_path.read_text().strip()

    token = secrets.token_urlsafe(32)
    token_path.write_text(token)
    token_path.chmod(0o600)
    return token


@lru_cache
def load_config() -> Config:
    root = find_project_root()
    config_dir = root / "config"
    config_path = config_dir / "config.yaml"

    load_dotenv(root / ".env")

    raw: dict = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}

    data_dir = root / raw.get("data_dir", "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        project_root=root,
        data_dir=data_dir,
        db_path=data_dir / "ytfa.db",
        server=ServerConfig(**raw.get("server", {})),
        polling=PollingConfig(**raw.get("polling", {})),
        rag=RagConfig(**raw.get("rag", {})),
        cost=CostConfig(**raw.get("cost", {})),
        llm=LlmConfig(**raw.get("llm", {})),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        youtube_api_key=os.getenv("YOUTUBE_API_KEY") or None,
        server_token=_read_or_create_server_token(config_dir),
    )
