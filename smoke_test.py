"""Minimal check that the Azure GPT-5 endpoint responds via inspect_ai."""

import asyncio

from inspect_ai.model import get_model

from config import GPT5


async def main() -> None:
    model = get_model(GPT5)
    output = await model.generate("What is the capital of France?")
    print(output.completion)


if __name__ == "__main__":
    asyncio.run(main())
