# backtalk: generic subprocess core.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Universal escape hatch for any CLI runtime.

Point `core.command` at an executable or wrapper that reads the user prompt
from stdin and writes the assistant response to stdout. A wrapper may own its
own persistence/session logic, which lets Backtalk drive runtimes it has never
heard of without another code change.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil

from backtalk.config import CFG, DISCIPLINE
from backtalk.core_base import AgentCore, CoreCapabilities, CoreError, sentences
from backtalk.vlog import log


class GenericCliBrain(AgentCore):
    provider_id = "generic-cli"
    display_name = "Generic CLI"
    capabilities = CoreCapabilities(
        streaming=False,
        resume=False,
        interrupt=True,
        clear=False,
        compact=False,
        model_switch=False,
        effort=False,
        context_usage=False,
        spoken_tool_permissions=False,
        tools=True,
    )

    def __init__(self, model: str | None = None, can_use_tool=None,
                 resume_id: str | None = None):
        cfg = CFG.get("core") or {}
        command = cfg.get("command") or []
        if isinstance(command, str):
            command = shlex.split(command)
        self.command_argv = [str(x) for x in command]
        self.model = str(model or cfg.get("model") or "generic").strip()
        self.timeout = float(cfg.get("timeout_seconds") or 300)
        self.include_discipline = bool(cfg.get("include_voice_discipline", True))
        self.session = {"turns": 0, "out_tokens": 0, "in_tokens": 0,
                        "cost": 0.0}
        self._active_proc: asyncio.subprocess.Process | None = None
        self._closed = False

    async def start(self):
        if not self.command_argv:
            raise CoreError(
                "generic-cli requires core.command in backtalk.json. "
                "Use a wrapper script if your runtime needs custom session logic."
            )
        executable = self.command_argv[0]
        if not (os.path.isabs(executable) and os.path.exists(executable)) \
                and not shutil.which(executable):
            raise CoreError(f"Generic core executable {executable!r} was not found")
        log(f"[core:generic] ready: {executable}")

    def _prompt(self, utterance: str) -> str:
        if not self.include_discipline:
            return utterance
        return (
            "Follow AGENTS.md in the working directory for identity and rules.\n\n"
            + DISCIPLINE + "\n\nUser said:\n" + utterance
        )

    async def ask_stream(self, utterance: str):
        if self._closed:
            raise CoreError("Generic core is closed")
        argv = [part.replace("{agent_dir}", CFG["agent_dir"])
                .replace("{model}", self.model)
                for part in self.command_argv]
        log("[core:generic] " + " ".join(shlex.quote(x) for x in argv))
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=CFG["agent_dir"],
        )
        self._active_proc = proc
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(self._prompt(utterance).encode("utf-8")),
                self.timeout,
            )
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            raise CoreError(
                f"Generic core timed out after {self.timeout:g} seconds"
            ) from exc
        finally:
            self._active_proc = None
        if proc.returncode:
            detail = err.decode("utf-8", "replace").strip()
            raise CoreError(
                f"Generic core exited {proc.returncode}: {detail[:600]}"
            )
        text = out.decode("utf-8", "replace").strip()
        self.session["turns"] += 1
        for sentence in sentences(text):
            yield sentence

    async def interrupt(self):
        proc = self._active_proc
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

    async def reset_turn(self, timeout: float = 8.0):
        await self.interrupt()

    async def stop(self):
        self._closed = True
        await self.interrupt()

    async def command(self, cmd: str) -> str:
        return (
            "error: generic-cli leaves session/model console operations to "
            "the configured wrapper"
        )
