# SPDX-License-Identifier: AGPL-3.0-or-later
"""Headless end-to-end core smoke test. No microphone or TTS required."""
from __future__ import annotations

import argparse
import asyncio

from backtalk.brain import WarmBrain


async def run(prompt: str) -> int:
    brain = WarmBrain()
    await brain.start()
    parts = []
    try:
        async for sentence in brain.ask_stream(prompt):
            parts.append(sentence)
    finally:
        await brain.stop()
    text = " ".join(parts).strip()
    if not text:
        print("FAIL: core returned no assistant text")
        return 1
    print(text)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompt",
        default="Reply with exactly the single word READY.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.prompt)))


if __name__ == "__main__":
    main()
