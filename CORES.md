# Pluggable agent cores

Backtalk treats the reasoning runtime as a replaceable core. Audio capture, Whisper STT, TTS, push-to-talk, visual signals, and the voice console stay outside the core.

## Core contract

A core must provide these lifecycle methods through the Backtalk facade:

- `start()`
- `ask_stream(prompt)`
- `interrupt()`
- `reset_turn()`
- `stop()`

Optional capabilities include session resume, clear/compact, model switching, effort control, context usage, and runtime-native tool permission gates. Missing optional capabilities must fail explicitly instead of pretending to work.

## Three integration lanes

There are now three ways to attach a reasoning runtime:

1. Use a built-in adapter such as `claude` or `codex`.
2. Use `generic-cli` and point it at any executable wrapper that reads stdin and writes assistant text to stdout.
3. Drop in a native Python adapter and select it with `core.adapter`, without editing Backtalk's registry.

## Select a built-in core

Add a `core` object to `backtalk.json`.

### Claude Code

```json
{
  "core": {
    "provider": "claude"
  }
}
```

Claude remains supported through `claude_agent_sdk`, but it is one adapter rather than the architecture itself.

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

An empty model means use the Codex CLI's configured or default model. Backtalk runs `codex exec --json`, captures the `thread.started` ID, and uses `codex exec resume` on later voice turns. Codex runs in `agent_dir`, so `AGENTS.md` remains the portable identity and instruction source.

The initial Codex adapter stays sandboxed. Read-only mode maps to Codex's read-only sandbox. Other Backtalk permission modes use Codex's automatic review lane because a headless voice process cannot safely relay an interactive terminal approval prompt yet. Capability negotiation therefore reports `spoken_tool_permissions=false` for Codex.

Install Codex before launching voice:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

On a headless machine, authenticate with:

```bash
codex login --device-auth
```

Then verify the adapter without touching the microphone:

```bash
uv run python -m backtalk.core_probe --check
```

## Generic CLI

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

The wrapper receives the prompt on stdin and must write only the assistant-facing response to stdout. It may maintain its own persistent session, invoke an SDK, talk to a local daemon, or translate to another harness.

## Drop-in Python adapter

For a native integration, create a local Python file that implements the core contract and select it directly:

```json
{
  "core": {
    "provider": "my-runtime",
    "adapter": "/home/me/universal-agent/custom-cores/my_runtime.py:MyRuntimeBrain"
  }
}
```

You can also use an importable Python module:

```json
{
  "core": {
    "provider": "my-runtime",
    "adapter": "my_package.runtime:MyRuntimeBrain"
  }
}
```

The class constructor must accept the same integration arguments as the built-in cores:

```python
MyRuntimeBrain(model=None, can_use_tool=None, resume_id=None)
```

At minimum it must implement `start`, `ask_stream`, `interrupt`, and `stop`. Inheriting `AgentCore` from `backtalk.core_base` is recommended because it provides safe defaults for optional methods.

This is the native drag-and-drop lane. The adapter file can live outside the Backtalk repository, so updating Backtalk does not overwrite the custom integration.

## Probe before launch

```bash
uv run python -m backtalk.core_probe --json
uv run python -m backtalk.core_probe --check
```

The probe reports provider, model, capability flags, session file, and whether the selected runtime can start.

## Design rule

The shell owns identity, memory, voice, UI, and lifecycle. `AGENTS.md` is the portable canonical instruction file. A core is only a reasoning and tool-execution engine. Provider-specific files may exist as compatibility shims, but no provider is allowed to become the source of truth for the agent itself.
