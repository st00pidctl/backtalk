const $ = (id) => document.getElementById(id);

const stateEl = $("state");
const routeEl = $("route");
const connectionEl = $("connection");
const talkButton = $("talk");
const interruptButton = $("interrupt");
const hintEl = $("hint");
const conversationEl = $("conversation");
const emptyEl = $("empty-state");
const nameEl = $("agent-name");

let stream = null;
let recorder = null;
let busy = false;
let audioContext = null;
let currentSource = null;
let statusTimer = null;
let activePointerId = null;
let holdActive = false;
let startGeneration = 0;
let maxRecordingTimer = null;
let lastServerState = "idle";

function setVisualState(state) {
  const normalized = String(state || "idle").toLowerCase();
  document.body.dataset.state = normalized;
  stateEl.textContent = normalized.toUpperCase();
  interruptButton.disabled = !["thinking", "speaking", "listening", "warming"].includes(normalized);
}

function setHint(message) {
  hintEl.textContent = message || "";
}

function setConnection(ok, label = ok ? "Connected" : "Offline") {
  connectionEl.classList.toggle("online", ok);
  connectionEl.classList.toggle("error", !ok);
  connectionEl.querySelector("span:last-child").textContent = label;
  talkButton.disabled = !ok || busy;
}

function addTurn(role, text) {
  if (emptyEl?.isConnected) emptyEl.remove();
  const wrap = document.createElement("div");
  wrap.className = `turn ${role}`;
  const label = document.createElement("p");
  label.className = "turn-label";
  label.textContent = role === "user" ? "YOU" : role === "error" ? "ERROR" : "AGENT";
  const body = document.createElement("p");
  body.textContent = text;
  wrap.append(label, body);
  conversationEl.appendChild(wrap);
  conversationEl.scrollTop = conversationEl.scrollHeight;
}

function pickMimeType() {
  const choices = [
    "audio/mp4",
    "audio/webm;codecs=opus",
    "audio/webm",
  ];
  return choices.find((type) => window.MediaRecorder?.isTypeSupported?.(type)) || "";
}

async function ensureMic() {
  if (stream && stream.getAudioTracks().some((track) => track.readyState === "live")) return stream;
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone access is unavailable. Open this endpoint over HTTPS.");
  }
  stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  });
  return stream;
}

async function unlockAudio() {
  if (!audioContext) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) throw new Error("Audio playback is unavailable in this browser.");
    audioContext = new AudioCtx();
  }
  if (audioContext.state === "suspended") await audioContext.resume();
}

async function playAudio(url) {
  await unlockAudio();
  if (currentSource) {
    try { currentSource.stop(); } catch (_) {}
    currentSource = null;
  }
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error("Reply audio expired before playback.");
  const data = await response.arrayBuffer();
  const buffer = await audioContext.decodeAudioData(data.slice(0));
  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);
  source.onended = () => {
    if (currentSource === source) currentSource = null;
    setVisualState(lastServerState === "speaking" ? "idle" : lastServerState);
    if (!busy) setHint("Hold to talk again.");
  };
  currentSource = source;
  setVisualState("speaking");
  setHint("Playing reply...");
  source.start();
}

function clearRecordingWatchdog() {
  if (maxRecordingTimer) clearTimeout(maxRecordingTimer);
  maxRecordingTimer = null;
}

function clearHoldVisual() {
  holdActive = false;
  activePointerId = null;
  talkButton.classList.remove("recording");
  clearRecordingWatchdog();
}

function stopRecorderIfActive() {
  clearHoldVisual();
  if (!recorder || recorder.state === "inactive") return;
  setHint("Sending audio to agent...");
  try {
    recorder.requestData?.();
  } catch (_) {}
  recorder.stop();
}

async function beginRecording(event) {
  event.preventDefault();
  if (busy || talkButton.disabled || holdActive) return;

  holdActive = true;
  activePointerId = event.pointerId ?? null;
  const generation = ++startGeneration;
  talkButton.classList.add("recording");
  setVisualState("listening");

  try {
    if (event.pointerId !== undefined) talkButton.setPointerCapture(event.pointerId);
  } catch (_) {}

  const micWasReady = Boolean(stream && stream.getAudioTracks().some((track) => track.readyState === "live"));
  setHint(micWasReady ? "Starting recorder... keep holding." : "Enabling microphone... keep holding.");

  try {
    await unlockAudio();
    const mic = await ensureMic();

    if (generation !== startGeneration || !holdActive) {
      clearHoldVisual();
      setVisualState("idle");
      setHint("Microphone ready. Hold again to talk.");
      return;
    }

    // iOS may consume the original pointer gesture while showing the microphone
    // permission sheet. Do not start an invisible recording after that prompt.
    if (!micWasReady) {
      clearHoldVisual();
      setVisualState("idle");
      setHint("Microphone ready. Hold again to talk.");
      return;
    }

    const mimeType = pickMimeType();
    const localChunks = [];
    const localRecorder = mimeType ? new MediaRecorder(mic, { mimeType }) : new MediaRecorder(mic);
    const startedAt = performance.now();
    recorder = localRecorder;

    localRecorder.ondataavailable = (e) => {
      if (e.data?.size) localChunks.push(e.data);
    };
    localRecorder.onerror = (e) => {
      const detail = e?.error?.message || "MediaRecorder failed.";
      clearHoldVisual();
      busy = false;
      recorder = null;
      setVisualState("idle");
      setHint(detail);
      addTurn("error", detail);
    };
    localRecorder.onstop = () => {
      if (recorder === localRecorder) recorder = null;
      void sendRecording(localRecorder, localChunks, startedAt);
    };

    localRecorder.start(150);
    setHint(`Listening${mimeType ? ` (${mimeType.split(";")[0]})` : ""}... release anywhere when finished.`);
    maxRecordingTimer = setTimeout(() => {
      if (recorder === localRecorder && localRecorder.state !== "inactive") {
        setHint("60 second recording limit reached. Sending...");
        stopRecorderIfActive();
      }
    }, 60000);
  } catch (error) {
    clearHoldVisual();
    recorder = null;
    setVisualState("idle");
    const message = error?.message || "Could not access microphone.";
    setHint(message);
    addTurn("error", message);
  }
}

function releaseRecording(event) {
  if (event?.pointerId !== undefined && activePointerId !== null && event.pointerId !== activePointerId) return;
  event?.preventDefault?.();
  holdActive = false;
  activePointerId = null;
  talkButton.classList.remove("recording");
  clearRecordingWatchdog();

  if (recorder && recorder.state !== "inactive") {
    setHint("Sending audio to agent...");
    try { recorder.requestData?.(); } catch (_) {}
    recorder.stop();
  } else if (!busy) {
    setVisualState("idle");
  }
}

async function sendRecording(localRecorder, localChunks, startedAt) {
  const elapsed = performance.now() - startedAt;
  clearRecordingWatchdog();
  talkButton.classList.remove("recording");

  if (elapsed < 250 || localChunks.length === 0) {
    setVisualState("idle");
    setHint(localChunks.length === 0 ? "No audio was captured. Hold a little longer and try again." : "That press was too short. Hold and speak, then release.");
    return;
  }

  busy = true;
  talkButton.disabled = true;
  setVisualState("thinking");

  const type = localRecorder?.mimeType || localChunks[0]?.type || "application/octet-stream";
  const blob = new Blob(localChunks, { type });
  const seconds = Math.max(0.1, elapsed / 1000).toFixed(1);
  setHint(`Uploaded ${seconds}s recording. Transcribing and thinking...`);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);

  try {
    const response = await fetch("/api/turn", {
      method: "POST",
      headers: { "Content-Type": type },
      body: blob,
      signal: controller.signal,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      throw new Error(`Endpoint returned ${response.status} without JSON.`);
    }
    if (!response.ok) throw new Error(payload.error || `Endpoint returned ${response.status}`);
    if (!payload.transcript) throw new Error("Agent host returned no transcript.");
    if (!payload.reply) throw new Error("Agent core returned no reply.");

    addTurn("user", payload.transcript);
    addTurn("agent", payload.reply);
    setHint("Reply received. Starting audio...");
    await playAudio(payload.audio_url);
  } catch (error) {
    setVisualState("idle");
    const message = error?.name === "AbortError"
      ? "The agent did not finish this turn within 3 minutes."
      : (error?.message || "The turn failed.");
    setHint(message);
    addTurn("error", message);
  } finally {
    clearTimeout(timeout);
    busy = false;
    talkButton.disabled = false;
    if (!currentSource && stateEl.textContent === "IDLE" && !hintEl.textContent) {
      setHint("Hold to talk again.");
    }
  }
}

async function interrupt() {
  try {
    ++startGeneration;
    clearHoldVisual();
    if (currentSource) {
      try { currentSource.stop(); } catch (_) {}
      currentSource = null;
    }
    if (recorder && recorder.state !== "inactive") {
      try { recorder.stop(); } catch (_) {}
    }
    recorder = null;
    await fetch("/api/interrupt", { method: "POST" });
  } catch (_) {
    // A local cut should still feel immediate if the network interrupt fails.
  } finally {
    busy = false;
    talkButton.disabled = false;
    setVisualState("idle");
    setHint("Interrupted. Hold to talk again.");
  }
}

async function pollStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error("offline");
    const status = await response.json();
    setConnection(Boolean(status.ok));
    nameEl.textContent = status.name || "Assistant";
    routeEl.textContent = `${status.host || "agent host"} · ${status.provider_name || status.provider || "core"}`;
    lastServerState = status.state || "idle";

    // While a request is in flight, the server state is more useful than the
    // client's generic "thinking" state. It tells us whether Peter received it.
    if (busy && ["listening", "thinking", "speaking", "warming"].includes(lastServerState)) {
      setVisualState(lastServerState);
      if (lastServerState === "listening") setHint("Agent host received audio. Transcribing...");
      if (lastServerState === "thinking") setHint("Transcript reached the core. Thinking...");
      if (lastServerState === "speaking") setHint("Generating reply audio...");
    } else if (!busy && !currentSource && recorder?.state !== "recording" && !holdActive) {
      setVisualState(lastServerState);
    }
  } catch (_) {
    setConnection(false, "Reconnecting");
  }
}

talkButton.addEventListener("pointerdown", beginRecording, { passive: false });
window.addEventListener("pointerup", releaseRecording, { passive: false });
window.addEventListener("pointercancel", releaseRecording, { passive: false });
talkButton.addEventListener("lostpointercapture", (event) => {
  if (holdActive) releaseRecording(event);
});
talkButton.addEventListener("contextmenu", (event) => event.preventDefault());
interruptButton.addEventListener("click", interrupt);

window.addEventListener("blur", () => {
  if (holdActive || (recorder && recorder.state !== "inactive")) releaseRecording();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && (holdActive || (recorder && recorder.state !== "inactive"))) releaseRecording();
});
window.addEventListener("pagehide", () => {
  ++startGeneration;
  clearRecordingWatchdog();
  if (stream) stream.getTracks().forEach((track) => track.stop());
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" }).catch(() => {});
}

pollStatus();
statusTimer = setInterval(pollStatus, 1000);
