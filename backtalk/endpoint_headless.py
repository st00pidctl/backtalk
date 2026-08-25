# backtalk: headless remote endpoint launcher.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run the browser endpoint without initializing VM audio devices.

The endpoint reuses Backtalk's existing Whisper and TTS model code, but the
browser owns microphone and speaker hardware. Some Linux sounddevice builds
initialize PortAudio/PulseAudio at import time, which is wrong for a headless
VM. Install a tiny in-process sounddevice stub before importing endpoint_server
so model-only functions remain available while any accidental attempt to open
local audio hardware fails loudly.
"""
from __future__ import annotations

import sys
import types


class _HeadlessAudioStream:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "local audio devices are disabled in the headless endpoint; "
            "use the browser endpoint for microphone and speaker I/O"
        )


stub = types.ModuleType("sounddevice")
stub.InputStream = _HeadlessAudioStream
stub.OutputStream = _HeadlessAudioStream
sys.modules["sounddevice"] = stub

from backtalk.endpoint_server import main  # noqa: E402


if __name__ == "__main__":
    main()
