# Pluggable agent cores

Backtalk now treats the reasoning/runtime layer as a replaceable core. Audio capture, Whisper STT, TTS, push-to-talk, visual signals, and the voice console stay outside the core.

## Core contract

A core must provide these lifecycle methods through the Backtalk facade:

- `start()`
- `ask_stream(prompt)`
- `interrupt()`
- `reset_turn()`
- `stop()`

Optional capabilities include session resume, clear/compact, model switching, effort control, context usage, and runtime-native tool permission gates. Missing optional capabilities must fail explicitly instead of pretending to work.

## Select a core

Add a `core` object to `backtalk.json`.

### Claude Code

```json
{
  "core": {
    "provider": "claude"
  }
}
```

Claude remains supported through `claude_agent_sdk`, but it is now one adapter rather than the architecture itself.

### OpenAI Codex CLI

```json
{
  "agent_dir": "~/my-agent",
  "name": "Assistant",
  "permission_mode": "ask",
  "resume_last_session": true,
  "core": {
    "provider": "codex",
    "binary": "codex",
    "model": "",
    "extra_args": []
  }
}
```

An empty model means use the Codex CLI's configured/default model. Backtalk runs `codex exec --json`, captures the `thread.started` ID, and uses `codex exec resume` on later voice turns so the conversation remains warm. Codex runs in `agent_dir`, so `AGENTS.md` remains the portable identity and instruction source.

The initial Codex adapter stays sandboxed. Read-only mode maps to Codex's read-only sandbox. Other Backtalk permission modes use Codex's automatic review lane because a headless voice process cannot safely relay an interactive terminal approval prompt yet. Capability negotiation therefore reports `spoken_tool_permissions=false` for Codex.

Install and authenticate Codex before launching voice:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex login
```

Then verify the adapter without touching the microphone:

```bash
uv run python -m backtalk.core_probe --check
```

### Generic CLI

Use this for any runtime Backtalk does not know about yet.

```json
{
  "core": {
    "provider": "generic-cli",
    "command": ["/path/to/my-agent-wrapper"],
    "timeout_seconds": 300
  }
}
```

The wrapper receives the prompt on stdin and must write only the assistant-facing response to stdout. It may maintain its own persistent session, invoke an SDK, talk to a local daemon, or translate to another harness. This is the universal escape hatch: adding a new brain does not require changing the voice/UI layers.

## Probe before launch

```bash
uv run python -m backtalk.core_probe --json
uv run python -m backtalk.core_probe --check
```

The probe reports provider, model, capability flags, session file, and whether the selected runtime can start.

## Design rule

The shell owns identity, memory, voice, UI, and lifecycle. `AGENTS.md` is the portable canonical instruction file. A core is only a reasoning and tool-execution engine. Provider-specific files may exist as compatibility shims, but no provider is allowed to become the source of truth for the agent itself.
