# SPDX-License-Identifier: AGPL-3.0-or-later
"""Headless end-to-end core smoke test. No microphone or TTS required."""
from __future__ import annotations

import argparse
import asyncio

from backtalk.brain import WarmBrain


async def _ask(brain: WarmBrain, prompt: str) -> str:
    parts = []
    async for sentence in brain.ask_stream(prompt):
        parts.append(sentence)
    return " ".join(parts).strip()


async def run(prompt: str, second_prompt: str | None) -> int:
    brain = WarmBrain()
    await brain.start()
    try:
        first = await _ask(brain, prompt)
        if not first:
            print("FAIL: first core turn returned no assistant text")
            return 1
        print(f"turn 1: {first}")

        if second_prompt:
            second = await _ask(brain, second_prompt)
            if not second:
                print("FAIL: second core turn returned no assistant text")
                return 1
            print(f"turn 2: {second}")
    finally:
        await brain.stop()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prompt",
        default="Reply with exactly the single word READY.",
    )
    parser.add_argument(
        "--second-prompt",
        default="Reply with exactly the single word STILL_READY.",
        help="second turn validates session continuity; pass an empty string to skip",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.prompt, args.second_prompt or None)))


if __name__ == "__main__":
    main()
