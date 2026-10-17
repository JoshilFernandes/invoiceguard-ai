"""Runtime configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    # "offline" = deterministic rule-based extractor (no API key, used in CI).
    # Anything else is passed to langchain's init_chat_model, e.g.
    #   groq:llama-3.3-70b-versatile | openai:gpt-4.1-mini |
    #   anthropic:claude-sonnet-4-5 | ollama:qwen2.5:7b
    llm: str = field(default_factory=lambda: os.getenv("INVOICEGUARD_LLM", "offline"))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("INVOICEGUARD_DATA_DIR", ROOT / "data")))
    state_dir: Path = field(default_factory=lambda: Path(os.getenv("INVOICEGUARD_STATE_DIR", ROOT / ".state")))
    auto_approve_limit: Decimal = field(default_factory=lambda: Decimal(os.getenv("INVOICEGUARD_AUTO_APPROVE_LIMIT", "5000")))
    risk_review_threshold: int = field(default_factory=lambda: int(os.getenv("INVOICEGUARD_RISK_THRESHOLD", "30")))
    price_tolerance_pct: Decimal = field(default_factory=lambda: Decimal(os.getenv("INVOICEGUARD_PRICE_TOLERANCE_PCT", "2")))
    max_repair_attempts: int = field(default_factory=lambda: int(os.getenv("INVOICEGUARD_MAX_REPAIRS", "2")))
    buyer_vat_id: str = field(default_factory=lambda: os.getenv("INVOICEGUARD_BUYER_VAT_ID", "DE298765432"))

    @property
    def offline(self) -> bool:
        return self.llm.strip().lower() in {"", "offline", "rules"}


def get_settings() -> Settings:
    return Settings()
