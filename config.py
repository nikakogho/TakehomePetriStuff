"""Shared environment + model configuration. Import this before creating models."""

from pathlib import Path

from dotenv import load_dotenv
from inspect_ai.model import GenerateConfig, Model, get_model

load_dotenv(Path(__file__).parent / ".env")

GPT5 = "openai/azure/gpt-5-Alignment-Interview"
SONNET = "openrouter/anthropic/claude-sonnet-5.5"


def gpt5(config: GenerateConfig | None = None) -> Model:
    """The provided GPT-5 deployment, used exactly as given: Chat Completions at the
    api-version from .env (2025-01-01-preview). inspect would otherwise route gpt-5 through
    the Responses API, which that api-version doesn't support."""
    return get_model(GPT5, responses_api=False, config=config or GenerateConfig())
