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
let chunks = [];
let pressStarted = 0;
let busy = false;
let audioContext = null;
let currentSource = null;
let statusTimer = null;

function setVisualState(state) {
  const normalized = String(state || "idle").toLowerCase();
  document.body.dataset.state = normalized;
  stateEl.textContent = normalized.toUpperCase();
  interruptButton.disabled = !["thinking", "speaking", "listening"].includes(normalized);
}

function setConnection(ok, label = ok ? "Connected" : "Offline") {
  connectionEl.classList.toggle("online", ok);
  connectionEl.classList.toggle("error", !ok);
  connectionEl.querySelector("span:last-child").textContent = label;
  talkButton.disabled = !ok || busy;
}

function addTurn(role, text) {
  if (emptyEl) emptyEl.remove();
  const wrap = document.createElement("div");
  wrap.className = `turn ${role}`;
  const label = document.createElement("p");
  label.className = "turn-label";
  label.textContent = role === "user" ? "YOU" : "AGENT";
  const body = document.createElement("p");
  body.textContent = text;
  wrap.append(label, body);
  conversationEl.appendChild(wrap);
  conversationEl.scrollTop = conversationEl.scrollHeight;
}

function pickMimeType() {
  const choices = [
    "audio/webm;codecs=opus",
    "audio/mp4",
    "audio/webm",
  ];
  return choices.find((type) => window.MediaRecorder?.isTypeSupported?.(type)) || "";
}

async function ensureMic() {
  if (stream) return stream;
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
    setVisualState("idle");
  };
  currentSource = source;
  setVisualState("speaking");
  source.start();
}

async function startRecording(event) {
  event.preventDefault();
  if (busy || talkButton.disabled) return;
  try {
    await unlockAudio();
    const mic = await ensureMic();
    chunks = [];
    const mimeType = pickMimeType();
    recorder = mimeType ? new MediaRecorder(mic, { mimeType }) : new MediaRecorder(mic);
    recorder.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
    recorder.onstop = sendRecording;
    recorder.start(120);
    pressStarted = performance.now();
    talkButton.classList.add("recording");
    hintEl.textContent = "Listening… release when finished.";
    setVisualState("listening");
    try { talkButton.setPointerCapture(event.pointerId); } catch (_) {}
  } catch (error) {
    hintEl.textContent = error.message || "Could not access microphone.";
    setVisualState("idle");
  }
}

function stopRecording(event) {
  event?.preventDefault?.();
  talkButton.classList.remove("recording");
  if (!recorder || recorder.state === "inactive") return;
  recorder.stop();
}

async function sendRecording() {
  const elapsed = performance.now() - pressStarted;
  if (elapsed < 250 || chunks.length === 0) {
    setVisualState("idle");
    hintEl.textContent = "Press and hold. Release when finished.";
    return;
  }
  busy = true;
  talkButton.disabled = true;
  setVisualState("thinking");
  hintEl.textContent = "Agent is working…";

  try {
    const type = recorder?.mimeType || chunks[0]?.type || "application/octet-stream";
    const blob = new Blob(chunks, { type });
    const response = await fetch("/api/turn", {
      method: "POST",
      headers: { "Content-Type": type },
      body: blob,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Endpoint returned ${response.status}`);
    addTurn("user", payload.transcript);
    addTurn("agent", payload.reply);
    hintEl.textContent = "";
    await playAudio(payload.audio_url);
  } catch (error) {
    setVisualState("idle");
    hintEl.textContent = error.message || "The turn failed.";
  } finally {
    busy = false;
    talkButton.disabled = false;
    recorder = null;
    chunks = [];
    if (!currentSource && stateEl.textContent === "IDLE") {
      hintEl.textContent = "Press and hold. Release when finished.";
    }
  }
}

async function interrupt() {
  try {
    if (currentSource) {
      try { currentSource.stop(); } catch (_) {}
      currentSource = null;
    }
    if (recorder && recorder.state !== "inactive") recorder.stop();
    await fetch("/api/interrupt", { method: "POST" });
  } catch (_) {
    // A local audio cut should still feel immediate even if the network call fails.
  } finally {
    busy = false;
    talkButton.disabled = false;
    setVisualState("idle");
    hintEl.textContent = "Interrupted. Hold to talk again.";
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
    if (!busy && !currentSource && recorder?.state !== "recording") setVisualState(status.state || "idle");
  } catch (_) {
    setConnection(false, "Reconnecting");
  }
}

talkButton.addEventListener("pointerdown", startRecording);
talkButton.addEventListener("pointerup", stopRecording);
talkButton.addEventListener("pointercancel", stopRecording);
talkButton.addEventListener("lostpointercapture", stopRecording);
interruptButton.addEventListener("click", interrupt);

window.addEventListener("pagehide", () => {
  if (stream) stream.getTracks().forEach((track) => track.stop());
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

pollStatus();
statusTimer = setInterval(pollStatus, 1300);
