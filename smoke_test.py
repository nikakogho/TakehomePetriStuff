"""Minimal check that the Azure GPT-5 endpoint responds via inspect_ai."""

import asyncio

from config import gpt5


async def main() -> None:
    model = gpt5()
    output = await model.generate("What is the capital of France?")
    print(output.completion)


if __name__ == "__main__":
    asyncio.run(main())
