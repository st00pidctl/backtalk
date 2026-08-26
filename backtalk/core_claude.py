# backtalk: Claude Agent SDK core.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Claude adapter behind Backtalk's provider-neutral core contract.

This preserves the upstream persistent SDK session model while keeping
provider-specific code out of the voice/audio layers.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from backtalk.config import CFG, DISCIPLINE
from backtalk.core_base import AgentCore, CoreCapabilities
from backtalk.vlog import log

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


class ClaudeBrain(AgentCore):
    provider_id = "claude"
    display_name = "Claude Code"
    capabilities = CoreCapabilities(
        streaming=True,
        resume=True,
        interrupt=True,
        clear=True,
        compact=True,
        model_switch=True,
        effort=True,
        context_usage=True,
        spoken_tool_permissions=True,
        tools=True,
    )

    def __init__(self, model: str | None = None, can_use_tool=None,
                 resume_id: str | None = None):
        core_cfg = CFG.get("core") or {}
        # Provider defaults are valid. Never require a shell-pinned Claude
        # model merely because another provider used explicit model fields.
        self.model = model or CFG.get("model") or core_cfg.get("model") or None
        self.binary = str(core_cfg.get("binary") or "").strip() or None
        self._can_use_tool = can_use_tool
        self._resume_id = resume_id
        self._client: ClaudeSDKClient | None = None
        self._dirty = False
        self.session = {"turns": 0, "out_tokens": 0, "in_tokens": 0,
                        "cost": 0.0}
        self.session_file = os.path.join(CFG["signals_dir"], ".backtalk_session")

    def _options(self, resume_id=None):
        # Keep the universal adapter in the safe interactive-permission lane.
        # Backtalk's spoken can_use_tool callback performs the approval step.
        kwargs = dict(
            cwd=CFG["agent_dir"],
            model=self.model,
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": DISCIPLINE},
            include_partial_messages=True,
            permission_mode="default",
            can_use_tool=self._can_use_tool,
            add_dirs=CFG["extra_dirs"],
            skills=CFG["visible_skills"],
            resume=resume_id,
        )
        # The Python Agent SDK bundles a Claude CLI, but when the shell has
        # provisioned an official native Claude Code binary we pin to it so
        # provenance, authentication, and upgrades are operator-visible.
        if self.binary:
            kwargs["cli_path"] = self.binary
        return ClaudeAgentOptions(**kwargs)

    async def start(self):
        resume, self._resume_id = self._resume_id, None
        if resume:
            try:
                self._client = ClaudeSDKClient(options=self._options(resume))
                await self._client.connect()
                log(f"[core:claude] resumed session {resume[:8]}")
                return
            except Exception as exc:
                log(f"[core:claude] resume failed: {str(exc)[:120]}")
                try:
                    if self._client:
                        await self._client.disconnect()
                except Exception:
                    pass
        self._client = ClaudeSDKClient(options=self._options())
        await self._client.connect()

    def _remember_session(self, message):
        if not CFG.get("resume_last_session"):
            return
        sid = getattr(message, "session_id", None)
        if not sid:
            return
        try:
            Path(self.session_file).write_text(str(sid))
        except OSError:
            pass

    def _tally(self, message, count_turn=True):
        try:
            usage = getattr(message, "usage", None) or {}
            if count_turn:
                self.session["turns"] += 1
            self.session["out_tokens"] += int(usage.get("output_tokens") or 0)
            self.session["in_tokens"] += int(usage.get("input_tokens") or 0)
            self.session["in_tokens"] += int(
                usage.get("cache_read_input_tokens") or 0)
            cost = getattr(message, "total_cost_usd", None)
            if cost:
                self.session["cost"] += float(cost)
        except Exception:
            pass

    async def ask_stream(self, utterance: str):
        self._dirty = True
        await self._client.query(utterance)
        buf = ""
        async for msg in self._client.receive_response():
            kind = type(msg).__name__
            if kind == "StreamEvent":
                event = getattr(msg, "event", {}) or {}
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {}) or {}
                    if delta.get("type") == "text_delta":
                        buf += delta.get("text", "")
                        while True:
                            match = _SENTENCE_END.search(buf)
                            if not match:
                                break
                            sentence, buf = (buf[:match.end()].strip(),
                                             buf[match.end():])
                            if sentence:
                                yield sentence
                elif event.get("type") == "content_block_stop":
                    tail = buf.strip()
                    buf = ""
                    if tail:
                        yield tail
            elif kind == "ResultMessage":
                self._dirty = False
                self._tally(msg)
                self._remember_session(msg)
                break
        tail = buf.strip()
        if tail:
            yield tail

    async def command(self, cmd: str) -> str:
        self._dirty = True
        await self._client.query(cmd)
        texts = []

        async def collect():
            async for msg in self._client.receive_response():
                kind = type(msg).__name__
                if kind == "AssistantMessage":
                    for block in getattr(msg, "content", []) or []:
                        text = getattr(block, "text", None)
                        if text:
                            texts.append(text)
                elif kind == "ResultMessage":
                    self._dirty = False
                    self._tally(msg, count_turn=False)
                    self._remember_session(msg)
                    break

        try:
            await asyncio.wait_for(collect(), 90)
        except asyncio.TimeoutError:
            return "error: the command timed out"
        return " ".join(texts).strip()

    async def interrupt(self):
        if self._client:
            await self._client.interrupt()

    async def reset_turn(self, timeout: float = 8.0):
        if not self._client or not self._dirty:
            return
        try:
            await asyncio.wait_for(self._client.interrupt(), 5)
        except Exception:
            pass

        async def drain():
            async for msg in self._client.receive_response():
                if type(msg).__name__ == "ResultMessage":
                    return

        try:
            await asyncio.wait_for(drain(), timeout)
            self._dirty = False
        except Exception:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
            await self.start()
            self._dirty = False

    async def stop(self):
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def context_usage(self):
        try:
            return await self._client.get_context_usage()
        except Exception:
            return None

    async def set_permission_mode(self, backtalk_mode: str):
        # Universal Claude adapter intentionally keeps the live SDK in its
        # normal approval lane. A restart is the boundary for broader policy
        # changes, which avoids hidden privilege escalation in voice mode.
        return None
