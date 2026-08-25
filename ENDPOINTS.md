# Remote browser endpoint

Backtalk can now run as a headless voice service. The endpoint device owns only microphone capture, speaker playback, and the browser UI. Peter keeps Whisper STT, Kokoro or ElevenLabs TTS, the selected core, session state, identity, and memory.

## Run locally on the agent host

```bash
cd ~/universal-agent/backtalk
.venv/bin/python -m backtalk.endpoint_server
```

The server binds to `127.0.0.1:8787` by default. This is deliberate. Do not bind it to the public internet just to make the browser microphone work.

## Expose privately over Tailscale HTTPS

Browser microphone APIs require a secure context on phones. Put Tailscale Serve in front of the loopback server:

```bash
tailscale serve --bg 8787
```

Tailscale terminates HTTPS and proxies the tailnet-only URL to `http://127.0.0.1:8787`. Open the HTTPS `*.ts.net` URL shown by `tailscale serve status` on the phone. The endpoint can then be installed to the home screen as a PWA.

## Data path

```text
phone microphone
  -> MediaRecorder
  -> HTTPS over tailnet
  -> ffmpeg decode on Peter
  -> Whisper STT
  -> WarmBrain selected core
  -> Kokoro / ElevenLabs synthesis
  -> WAV over HTTPS
  -> phone Web Audio output
```

No VM microphone or speaker device is required.

## API

- `GET /api/health` and `GET /api/status`: endpoint/core state and basic host memory information.
- `POST /api/turn`: browser audio body in any ffmpeg-decodable format. Returns transcript, assistant reply, and a short-lived WAV URL.
- `POST /api/text`: JSON `{ "text": "..." }`, useful for diagnostics without a microphone.
- `POST /api/interrupt`: stops the active core turn and tells the endpoint to return to idle.
- `GET /api/audio/<token>`: short-lived synthesized reply audio.

## Security boundary

The backend listens on loopback by default. Tailscale Serve should be the network boundary. Tailnet ACLs determine which devices can reach the endpoint. This first endpoint does not implement a second application password on top of Tailscale identity.

Do not use Tailscale Funnel for the normal personal endpoint. Funnel is public internet exposure and is unnecessary here.

## Hardware profile

The endpoint avoids VM audio passthrough but still performs local inference. The largest sustained loads are Whisper transcription and Kokoro synthesis. Models warm in a background thread after the endpoint server starts. `GET /api/status` reports total and available system memory so future endpoint clients can surface resource pressure.

Only one reasoning turn is admitted at a time per endpoint process. Multiple phones can connect, but simultaneous turns serialize through the same agent session instead of racing the core.
