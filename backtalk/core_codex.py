# backtalk: OpenAI Codex CLI core.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Codex CLI adapter using `codex exec --json` and resumable thread IDs."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil

from backtalk.config import CFG, DISCIPLINE
from backtalk.core_base import AgentCore, CoreCapabilities, CoreError, sentences
from backtalk.vlog import log


class CodexBrain(AgentCore):
    provider_id = "codex"
    display_name = "OpenAI Codex CLI"
    capabilities = CoreCapabilities(
        streaming=False,
        resume=True,
        interrupt=True,
        clear=True,
        compact=False,
        model_switch=True,
        effort=True,
        context_usage=False,
        spoken_tool_permissions=False,
        tools=True,
    )

    def __init__(self, model: str | None = None, can_use_tool=None,
                 resume_id: str | None = None):
        cfg = CFG.get("core") or {}
        self.cfg = cfg
        self.binary = str(cfg.get("binary") or "codex")
        self.model = str(model or cfg.get("model") or "").strip()
        self.effort = str(cfg.get("effort") or CFG.get("effort") or "").strip()
        self.permission_mode = str(cfg.get("permission_mode")
                                   or CFG.get("permission_mode") or "ask")
        self.extra_args = [str(x) for x in (cfg.get("extra_args") or [])]
        self.session = {"turns": 0, "out_tokens": 0, "in_tokens": 0,
                        "cost": 0.0}
        self._resume_id = resume_id
        self._thread_id: str | None = None
        self._active_proc: asyncio.subprocess.Process | None = None
        self._closed = False
        self.session_file = os.path.join(
            CFG["signals_dir"], ".backtalk_session_codex")

    async def start(self):
        if not shutil.which(self.binary):
            raise CoreError(
                f"Codex CLI executable {self.binary!r} was not found. "
                "Install Codex CLI and run `codex login` first."
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), 15)
        except Exception as exc:
            raise CoreError(f"Could not start Codex CLI: {exc}") from exc
        if proc.returncode:
            msg = (err or out).decode("utf-8", "replace").strip()
            raise CoreError(f"Codex CLI check failed: {msg[:300]}")
        version = out.decode("utf-8", "replace").strip()
        log(f"[core:codex] {version or 'binary ready'}")

    def _permission_args(self) -> list[str]:
        """Map Backtalk permission semantics to headless Codex exec.

        Codex exec is noninteractive, so Backtalk cannot relay Codex's native
        approval prompt. Keep every Codex turn sandboxed and explicitly disable
        escalation. A denied action is returned to the model as a tool failure
        rather than blocking forever waiting for terminal input.

        Codex 0.114.0 does not accept --approve-for-me on `codex exec`, which
        is why this uses stable sandbox/config flags instead of that newer or
        surface-specific option.
        """
        mode = str(CFG.get("permission_mode") or self.permission_mode)
        sandbox = "read-only" if mode in ("read-only", "readonly", "read_only") \
            else "workspace-write"
        return ["--sandbox", sandbox, "-c", 'approval_policy="never"']

    def _command(self) -> list[str]:
        cmd = [self.binary, "exec", "--json", "--skip-git-repo-check",
               "-C", CFG["agent_dir"]]
        if self.model:
            cmd += ["-m", self.model]
        for path in CFG.get("extra_dirs") or []:
            cmd += ["--add-dir", path]
        cmd += self._permission_args()
        if self.effort:
            effort = "xhigh" if self.effort == "max" else self.effort
            cmd += ["-c", f'model_reasoning_effort="{effort}"']
        cmd += self.extra_args
        rid = self._thread_id or self._resume_id
        if rid:
            cmd += ["resume", rid, "-"]
        else:
            cmd += ["-"]
        return cmd

    def _remember_thread(self, thread_id: str):
        self._thread_id = thread_id
        self._resume_id = None
        if not CFG.get("resume_last_session"):
            return
        try:
            Path(self.session_file).write_text(thread_id)
        except OSError as exc:
            log(f"[core:codex] could not persist thread id: {exc}")

    def _clear_resume_state(self):
        """Forget provider-owned thread state without touching agent memory."""
        self._thread_id = None
        self._resume_id = None
        try:
            Path(self.session_file).unlink(missing_ok=True)
        except OSError as exc:
            log(f"[core:codex] could not remove stale thread id: {exc}")

    @staticmethod
    def _stale_resume_error(detail: str) -> bool:
        """Recognize Codex errors meaning a persisted thread can no longer resume."""
        text = detail.lower()
        return (
            "no rollout found for thread id" in text
            or "thread/resume failed" in text
            or ("resume" in text and "rollout" in text and "not found" in text)
        )

    def _voice_prompt(self, utterance: str) -> str:
        return (
            "You are being used through Backtalk, a spoken interface. "
            "Follow the project AGENTS.md for identity and operating rules.\n\n"
            + DISCIPLINE + "\n\nUser said:\n" + utterance
        )

    async def _finish_process(self):
        proc = self._active_proc
        if not proc:
            return
        try:
            await asyncio.wait_for(proc.wait(), 1.5)
        except asyncio.TimeoutError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), 2.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        finally:
            self._active_proc = None

    async def ask_stream(self, utterance: str):
        if self._closed:
            raise CoreError("Codex core is closed")
        attempted_resume = bool(self._thread_id or self._resume_id)
        cmd = self._command()
        log("[core:codex] " + " ".join(shlex.quote(x) for x in cmd[:-1]))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=CFG["agent_dir"],
        )
        self._active_proc = proc
        assert proc.stdin is not None
        proc.stdin.write(self._voice_prompt(utterance).encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        stderr_task = asyncio.create_task(proc.stderr.read())
        final_text = ""
        completed = False
        error_text = ""
        usage = None
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    log(f"[core:codex] non-json output: {line[:300]}")
                    continue
                etype = event.get("type")
                if etype == "thread.started":
                    tid = event.get("thread_id")
                    if tid:
                        self._remember_thread(str(tid))
                elif etype == "item.completed":
                    item = event.get("item") or {}
                    if item.get("type") == "agent_message":
                        final_text = str(item.get("text") or final_text)
                    elif item.get("type") == "error":
                        log(f"[core:codex] item error: {str(item.get('message') or '')[:300]}")
                elif etype == "turn.completed":
                    usage = event.get("usage") or {}
                    completed = True
                    break
                elif etype in ("turn.failed", "error"):
                    err = event.get("error") or event
                    if isinstance(err, dict):
                        error_text = str(err.get("message") or err)
                    else:
                        error_text = str(err)
                    break
        finally:
            await self._finish_process()
            try:
                stderr = (await asyncio.wait_for(stderr_task, 1.0)).decode(
                    "utf-8", "replace").strip()
            except Exception:
                stderr = ""

        if not completed:
            detail = error_text or stderr or "Codex turn ended without turn.completed"
            if attempted_resume and self._stale_resume_error(detail):
                stale_id = self._thread_id or self._resume_id or "unknown"
                log(
                    f"[core:codex] stale resume thread {stale_id}; "
                    "starting a fresh provider thread"
                )
                self._clear_resume_state()
                async for sentence in self.ask_stream(utterance):
                    yield sentence
                return
            raise CoreError(detail[:600])

        self.session["turns"] += 1
        if usage:
            self.session["in_tokens"] += int(usage.get("input_tokens") or 0)
            self.session["out_tokens"] += int(usage.get("output_tokens") or 0)
        for sentence in sentences(final_text):
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
        if self._active_proc:
            try:
                await asyncio.wait_for(self._active_proc.wait(), timeout)
            except Exception:
                pass
            self._active_proc = None

    async def stop(self):
        self._closed = True
        await self.reset_turn()

    async def command(self, cmd: str) -> str:
        cmd = cmd.strip()
        if cmd == "/clear":
            self._clear_resume_state()
            return "cleared"
        if cmd == "/compact":
            return "error: compact is not exposed by the Codex exec adapter"
        if cmd.startswith("/model "):
            self.model = cmd.split(" ", 1)[1].strip()
            return f"model set to {self.model}"
        if cmd.startswith("/effort "):
            self.effort = cmd.split(" ", 1)[1].strip()
            return f"effort set to {self.effort}"
        return f"error: unsupported Codex voice-console command {cmd}"

    async def context_usage(self):
        return None

    async def set_permission_mode(self, backtalk_mode: str):
        self.permission_mode = backtalk_mode
