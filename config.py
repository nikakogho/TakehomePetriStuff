"""Shared environment + model configuration. Import this before creating models."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# inspect_ai routes GPT-5 through the Responses API (preserves reasoning across
# agentic turns), which Azure only serves on api-version >= 2025-03-01-preview.
os.environ["AZUREAI_OPENAI_API_VERSION"] = "2025-04-01-preview"

GPT5 = "openai/azure/gpt-5-Alignment-Interview"
SONNET = "openrouter/anthropic/claude-sonnet-5.5"
