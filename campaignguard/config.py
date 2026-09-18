"""Runtime configuration. Secrets come only from environment variables."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"

# Fixed review date so date checks are reproducible. Override with CG_REVIEW_DATE.
DEFAULT_REVIEW_DATE = date.fromisoformat(os.getenv("CG_REVIEW_DATE", "2026-09-15"))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:  # pragma: no cover
        pass


_load_dotenv()


def openai_api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or None


def openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-5-mini")


MAX_TOOL_ROUNDS = int(os.getenv("CG_MAX_TOOL_ROUNDS", "4"))
LLM_TIMEOUT_S = float(os.getenv("CG_LLM_TIMEOUT_S", "60"))
LLM_MAX_RETRIES = int(os.getenv("CG_LLM_MAX_RETRIES", "2"))
MCP_TOOL_TIMEOUT_S = float(os.getenv("CG_MCP_TOOL_TIMEOUT_S", "15"))
# Startup (initialize + tools/list) is slower than a tool call: the server subprocess has to
# import scikit-learn and pandas, which on a cold cache can take tens of seconds.
MCP_CONNECT_TIMEOUT_S = float(os.getenv("CG_MCP_CONNECT_TIMEOUT_S", "90"))
