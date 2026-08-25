# backtalk: remote browser endpoint server.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tailnet-friendly remote voice endpoint.

The browser owns microphone and speaker hardware. Peter owns STT, TTS, memory,
and the selected reasoning core. This server intentionally binds to loopback by
default and is meant to sit behind Tailscale Serve for HTTPS.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import io
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import wave

import numpy as np

from backtalk.brain import SESSION_FILE, WarmBrain
from backtalk.config import CFG
from backtalk import ears, mouth, signals
from backtalk.vlog import log

REPO = Path(__file__).resolve().parent.parent
WEB_ROOT = REPO / "endpoint"
MAX_AUDIO_BYTES = 16 * 1024 * 1024
AUDIO_TTL_S = 10 * 60


def _resume_id() -> str | None:
    if not CFG.get("resume_last_session"):
        return None
    try:
        value = Path(SESSION_FILE).read_text().strip()
        return value or None
    except OSError:
        return None


def _decode_audio(blob: bytes) -> np.ndarray:
    """Browser audio (webm/mp4/etc.) -> mono int16 PCM at Whisper's 16 kHz."""
    if not blob:
        raise ValueError("empty audio upload")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for browser audio decoding")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ac", "1", "-ar", str(ears.RATE), "pipe:1"],
        input=blob,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if proc.returncode:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"could not decode browser audio: {detail[:300]}")
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).copy()
    if pcm.size < ears.RATE // 10:
        raise ValueError("audio upload was too short")
    return pcm


def _wav_bytes(text: str) -> bytes:
    """Render reply text to one mono PCM WAV without opening a sound device."""
    chunks: list[np.ndarray] = []
    rate: int | None = None
    for sample_rate, pcm in mouth.synth_stream(text):
        if rate is None:
            rate = int(sample_rate)
        elif int(sample_rate) != rate:
            raise RuntimeError("TTS engine changed sample rate mid-reply")
        chunks.append(np.asarray(pcm, dtype=np.int16))
    if not chunks or not rate:
        raise RuntimeError("TTS returned no audio")
    audio = np.concatenate(chunks)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(audio.tobytes())
    return out.getvalue()


def _system_memory() -> dict[str, int | None]:
    values: dict[str, int | None] = {"total_mb": None, "available_mb": None}
    try:
        data = Path("/proc/meminfo").read_text().splitlines()
        parsed = {}
        for line in data:
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            parsed[key] = int(raw.strip().split()[0])
        values["total_mb"] = parsed.get("MemTotal", 0) // 1024
        values["available_mb"] = parsed.get("MemAvailable", 0) // 1024
    except Exception:
        pass
    return values


@dataclass
class StoredAudio:
    created: float
    data: bytes


class EndpointRuntime:
    def __init__(self):
        self.state = "starting"
        self.state_lock = threading.Lock()
        self.audio_lock = threading.Lock()
        self.audio: dict[str, StoredAudio] = {}
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.start_error: Exception | None = None
        self.brain: WarmBrain | None = None
        self.turn_lock: asyncio.Lock | None = None
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        if not self.ready.wait(30):
            raise RuntimeError("core runtime did not start within 30 seconds")
        if self.start_error:
            raise RuntimeError(f"core runtime failed: {self.start_error}")

    def _set_state(self, state: str):
        with self.state_lock:
            self.state = state
        try:
            signals.set_state(state)
        except Exception:
            pass

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.brain = WarmBrain(resume_id=_resume_id())
            self.turn_lock = asyncio.Lock()
            self.loop.run_until_complete(self.brain.start())
            self._set_state("idle")
        except Exception as exc:
            self.start_error = exc
        finally:
            self.ready.set()
        if not self.start_error:
            self.loop.run_forever()

    async def _ask(self, text: str) -> str:
        assert self.brain is not None and self.turn_lock is not None
        async with self.turn_lock:
            self._set_state("thinking")
            parts = []
            try:
                async for sentence in self.brain.ask_stream(text):
                    parts.append(sentence)
            finally:
                if self.state == "thinking":
                    self._set_state("idle")
            return " ".join(parts).strip()

    def ask(self, text: str, timeout: float = 600) -> str:
        future = asyncio.run_coroutine_threadsafe(self._ask(text), self.loop)
        return future.result(timeout=timeout)

    def interrupt(self):
        if not self.brain:
            return
        future = asyncio.run_coroutine_threadsafe(self.brain.interrupt(), self.loop)
        try:
            future.result(timeout=10)
        finally:
            self._set_state("idle")

    def stop(self):
        if self.brain and self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.brain.stop(), self.loop)
            try:
                future.result(timeout=10)
            except Exception:
                pass
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def warm_voice_async(self):
        def worker():
            try:
                self._set_state("warming")
                ears.warm()
                mouth.warm()
                self._set_state("idle")
                log("[endpoint] STT and TTS warm")
            except Exception as exc:
                log(f"[endpoint] voice warm failed: {exc}")
                self._set_state("idle")
        threading.Thread(target=worker, daemon=True).start()

    def store_audio(self, data: bytes) -> str:
        now = time.time()
        token = secrets.token_urlsafe(18)
        with self.audio_lock:
            self.audio = {
                key: value for key, value in self.audio.items()
                if now - value.created < AUDIO_TTL_S
            }
            self.audio[token] = StoredAudio(now, data)
        return token

    def get_audio(self, token: str) -> bytes | None:
        now = time.time()
        with self.audio_lock:
            item = self.audio.get(token)
            if not item or now - item.created >= AUDIO_TTL_S:
                self.audio.pop(token, None)
                return None
            return item.data

    def status(self) -> dict:
        with self.state_lock:
            state = self.state
        brain = self.brain
        return {
            "ok": self.start_error is None,
            "name": CFG.get("name") or "Assistant",
            "state": state,
            "provider": getattr(brain, "provider", None) if brain else None,
            "provider_name": getattr(brain, "provider_name", None) if brain else None,
            "model": getattr(brain, "model", None) or "provider default" if brain else None,
            "memory": "portable",
            "host": os.uname().nodename if hasattr(os, "uname") else "agent",
            "memory_system": _system_memory(),
        }


class EndpointHandler(BaseHTTPRequestHandler):
    server_version = "BacktalkEndpoint/0.1"

    @property
    def runtime(self) -> EndpointRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        log("[endpoint:http] " + (fmt % args))

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise ValueError("invalid content length")
        if length <= 0:
            raise ValueError("request body is empty")
        if length > MAX_AUDIO_BYTES:
            raise ValueError("request body is too large")
        return self.rfile.read(length)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(200, self.runtime.status())
            return
        if path == "/api/status":
            self._json(200, self.runtime.status())
            return
        if path.startswith("/api/audio/"):
            token = path.rsplit("/", 1)[-1]
            data = self.runtime.get_audio(token)
            if data is None:
                self._json(404, {"error": "audio expired or not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
            return
        self._static(path)

    def _static(self, request_path: str):
        rel = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        candidate = (WEB_ROOT / rel).resolve()
        try:
            candidate.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = candidate.read_bytes()
        ctype = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if candidate.name == "index.html" else "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/interrupt":
            self.runtime.interrupt()
            self._json(200, {"ok": True, "state": "idle"})
            return
        if path == "/api/text":
            try:
                payload = json.loads(self._body().decode("utf-8"))
                text = str(payload.get("text") or "").strip()
                if not text:
                    raise ValueError("text is empty")
                reply = self.runtime.ask(text)
                self.runtime._set_state("speaking")
                wav = _wav_bytes(reply)
                token = self.runtime.store_audio(wav)
                self.runtime._set_state("idle")
                self._json(200, {"transcript": text, "reply": reply, "audio_url": f"/api/audio/{token}"})
            except Exception as exc:
                self.runtime._set_state("idle")
                self._json(500, {"error": str(exc)[:500]})
            return
        if path == "/api/turn":
            try:
                blob = self._body()
                self.runtime._set_state("listening")
                pcm = _decode_audio(blob)
                transcript = ears.transcribe(pcm).strip()
                if not transcript:
                    self.runtime._set_state("idle")
                    self._json(422, {"error": "no speech detected"})
                    return
                reply = self.runtime.ask(transcript)
                if not reply:
                    raise RuntimeError("core returned no reply")
                self.runtime._set_state("speaking")
                wav = _wav_bytes(reply)
                token = self.runtime.store_audio(wav)
                self.runtime._set_state("idle")
                self._json(200, {
                    "transcript": transcript,
                    "reply": reply,
                    "audio_url": f"/api/audio/{token}",
                })
            except ValueError as exc:
                self.runtime._set_state("idle")
                self._json(400, {"error": str(exc)[:500]})
            except Exception as exc:
                self.runtime._set_state("idle")
                self._json(500, {"error": str(exc)[:500]})
            return
        self._json(404, {"error": "not found"})


def self_test() -> int:
    missing = [name for name in ("ffmpeg",) if not shutil.which(name)]
    required = ["index.html", "app.js", "styles.css", "manifest.webmanifest", "sw.js"]
    missing += [f"endpoint/{name}" for name in required if not (WEB_ROOT / name).is_file()]
    if missing:
        print("ENDPOINT_SELF_TEST_FAIL missing=" + ",".join(missing))
        return 1
    print("ENDPOINT_SELF_TEST_OK")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Backtalk remote browser endpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-warm", action="store_true", help="do not preload Whisper/Kokoro")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        raise SystemExit(self_test())

    runtime = EndpointRuntime()
    server = ThreadingHTTPServer((args.host, args.port), EndpointHandler)
    server.runtime = runtime  # type: ignore[attr-defined]
    if not args.no_warm:
        runtime.warm_voice_async()
    log(f"[endpoint] listening on http://{args.host}:{args.port}")
    print(f"Remote endpoint ready on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.stop()


if __name__ == "__main__":
    main()
