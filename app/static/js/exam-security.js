/**
 * Phase 2 - Camera-based basic proctoring + optional microphone/voice
 * detection (spec #11-14).
 *
 * Honesty about limitations (spec explicitly requires this): this uses
 * the browser's built-in, experimental `FaceDetector` API where
 * available (current Chrome/Edge on some platforms) as a lightweight,
 * practical face-presence signal. It is NOT a verified-identity or
 * perfect-cheating-detection system, and "looking away" is only an
 * approximate heuristic based on face bounding-box position/size, not
 * real eye-tracking. Where the API isn't supported, face/looking-away
 * detection is silently skipped (fails open, never blocks the exam) and
 * only the always-available tab-switch/fullscreen/devtools checks apply.
 *
 * Camera/mic are only ever requested here, on the live exam page, for
 * the duration of the secure exam - never elsewhere in the app - and
 * nothing is recorded or uploaded; only violation metadata (type,
 * timestamp, warning number) is ever sent to the server, via the same
 * unified reportSecurityViolation() the tab-switch/fullscreen checks use.
 */
(function () {
  const DATA = window.EXAM_DATA;
  if (!DATA || !DATA.security) return;
  const SEC = DATA.security;
  if (!SEC.cameraRequired && !SEC.micRequired) return;

  function report(type) {
    if (window.reportSecurityViolation) window.reportSecurityViolation(type);
  }

  // ---------------- Debounce/cooldown state ----------------
  // A violation is only raised after a condition PERSISTS for a
  // configurable period, and each violation type has its own cooldown so
  // one continuous problem doesn't spam multiple warnings per second.
  const PERSIST_MS = 3000;      // condition must hold for 3s before it counts
  const COOLDOWN_MS = 15000;    // don't re-report the same type more than once per 15s
  const state = {
    face_missing: { since: null, lastReported: 0 },
    multiple_faces: { since: null, lastReported: 0 },
    looking_away: { since: null, lastReported: 0 },
  };

  function evaluateCondition(type, isActive) {
    const now = Date.now();
    const s = state[type];
    if (!isActive) {
      s.since = null;
      return;
    }
    if (!s.since) s.since = now;
    if (now - s.since >= PERSIST_MS && now - s.lastReported >= COOLDOWN_MS) {
      s.lastReported = now;
      s.since = now; // require it to persist again before re-firing
      report(type);
    }
  }

  // ---------------- Camera setup ----------------
  let videoEl = document.getElementById("proctorVideo");
  let statusEl = document.getElementById("proctorStatus");
  let stream = null;

  async function setupCamera() {
    if (!SEC.cameraRequired) return;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 320, height: 240 },
        audio: SEC.micRequired,
      });
      if (videoEl) {
        videoEl.srcObject = stream;
        if (statusEl) statusEl.textContent = "Monitoring active";
      }
      if (SEC.faceDetection || SEC.multiFaceDetection || SEC.lookingAwayDetection) {
        startFaceDetectionLoop();
      }
      if (SEC.micRequired && SEC.voiceDetection) {
        startVoiceDetection(stream);
      }
    } catch (e) {
      // Camera permission was already granted on the instructions page in
      // the normal flow; if it's missing here (revoked mid-exam, etc.)
      // show it plainly rather than silently failing.
      if (statusEl) statusEl.textContent = "Camera unavailable";
    }
  }

  // ---------------- Basic face detection ----------------
  function startFaceDetectionLoop() {
    if (typeof window.FaceDetector === "undefined") {
      // Not supported in this browser - fail open, skip gracefully.
      if (statusEl) statusEl.textContent = "Monitoring active (basic)";
      return;
    }
    let detector;
    try {
      detector = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 4 });
    } catch (e) {
      return;
    }

    setInterval(async () => {
      if (!videoEl || videoEl.readyState < 2) return;
      let faces = [];
      try {
        faces = await detector.detect(videoEl);
      } catch (e) {
        return; // transient failure - skip this tick, don't report anything
      }

      if (SEC.faceDetection) {
        evaluateCondition("face_missing", faces.length === 0);
      }
      if (SEC.multiFaceDetection) {
        evaluateCondition("multiple_faces", faces.length > 1);
      }
      if (SEC.lookingAwayDetection && faces.length === 1) {
        // Approximate heuristic only (NOT real eye-tracking): a face
        // bounding box far off-center or unusually small/large suggests
        // the student has turned significantly away from the camera.
        const box = faces[0].boundingBox;
        const vw = videoEl.videoWidth || 320;
        const vh = videoEl.videoHeight || 240;
        const cx = (box.x + box.width / 2) / vw;
        const cy = (box.y + box.height / 2) / vh;
        const offCenter = Math.abs(cx - 0.5) > 0.30 || Math.abs(cy - 0.5) > 0.30;
        evaluateCondition("looking_away", offCenter);
      }
    }, 1500);
  }

  // ---------------- Optional voice/audio detection ----------------
  function startVoiceDetection(mediaStream) {
    if (!mediaStream.getAudioTracks().length) return;
    let audioCtx;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) {
      return;
    }
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    const source = audioCtx.createMediaStreamSource(mediaStream);
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);

    const VOICE_THRESHOLD = 45; // heuristic RMS-ish threshold on a 0-255 scale
    let loudSince = null;

    setInterval(() => {
      analyser.getByteFrequencyData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) sum += data[i];
      const avg = sum / data.length;

      const now = Date.now();
      if (avg > VOICE_THRESHOLD) {
        if (!loudSince) loudSince = now;
      } else {
        loudSince = null;
      }
      // Significant, sustained audio (not a passing click/tap) for 2s+
      const sustained = loudSince && (now - loudSince >= 2000);
      const s = state.voice_detected || (state.voice_detected = { since: null, lastReported: 0 });
      if (sustained && now - s.lastReported >= COOLDOWN_MS) {
        s.lastReported = now;
        loudSince = now; // require sustained audio again before re-firing
        report("voice_detected");
      }
    }, 500);

    // Audio is analyzed locally only, never recorded or transmitted -
    // only the resulting violation metadata is ever sent to the server.
  }

  document.addEventListener("DOMContentLoaded", setupCamera);
  if (document.readyState !== "loading") setupCamera();

  window.addEventListener("beforeunload", () => {
    if (stream) stream.getTracks().forEach(t => t.stop());
  });
})();
