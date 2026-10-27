"""Minimal check that the Azure GPT-5 endpoint responds via inspect_ai (MAKES ONE MODEL CALL).

Run from the repo root: python scratch/smoke_test.py
"""

import asyncio

from peerconceal.config import gpt5


async def main() -> None:
    model = gpt5()
    output = await model.generate("What is the capital of France?")
    print(output.completion)


if __name__ == "__main__":
    asyncio.run(main())
