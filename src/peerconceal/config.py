"""Shared environment + model configuration. Import this before creating models.

Loads API keys from the .env file at the repository root (see README: Setup).
"""

from dotenv import load_dotenv
from inspect_ai.model import GenerateConfig, Model, get_model

from peerconceal.paths import ROOT

load_dotenv(ROOT / ".env")

GPT5 = "openai/azure/gpt-5-Alignment-Interview"
SONNET = "openrouter/anthropic/claude-sonnet-5.5"
GEMINI = "openrouter/google/gemini-3.8-flash"


def gpt5(config: GenerateConfig | None = None) -> Model:
    """The provided GPT-5 deployment, used exactly as given: Chat Completions at the
    api-version from .env (2025-01-01-preview). inspect would otherwise route gpt-5 through
    the Responses API, which that api-version doesn't support."""
    return get_model(GPT5, responses_api=False, config=config or GenerateConfig())
