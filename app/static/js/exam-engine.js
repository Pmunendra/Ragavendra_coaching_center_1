(function () {
  const DATA = window.EXAM_DATA;
  const questions = DATA.questions;
  let currentIndex = 0;
  let remaining = DATA.remainingSeconds;
  let dirty = false;
  let submitting = false;

  // ---------------- Helpers ----------------
  function qs(id) { return document.getElementById(id); }

  function authHeaders() {
    return {
      "Content-Type": "application/json",
      "Authorization": "Bearer " + DATA.attemptToken,
    };
  }

  function currentQuestion() { return questions[currentIndex]; }

  function setSaveStatus(text, ok = true) {
    const el = qs("saveStatus");
    el.innerHTML = `<i class="bi bi-cloud-${ok ? "check" : "arrow-up"}"></i> ${text}`;
  }

  // ---------------- Rendering ----------------
  function renderQuestion() {
    const q = currentQuestion();
    const card = qs("questionCard");
    let html = `<div class="d-flex justify-content-between mb-2">
        <span class="badge bg-primary">Question ${currentIndex + 1} of ${questions.length}</span>
        <span class="badge bg-secondary">${q.marks} mark(s)</span>
      </div>`;

    if (q.passage) {
      html += `<div class="border rounded p-3 mb-3 bg-body-tertiary small">${escapeHtml(q.passage)}</div>`;
    }
    if (q.image_url) {
      html += `<div class="mb-3"><img src="${escapeAttr(q.image_url)}" class="img-fluid rounded border" style="max-height:300px;"></div>`;
    }

    html += `<p class="fw-semibold">${escapeHtml(q.text)}</p>`;

    const ans = q.answer || { selected: [], integer: null, text: null };

    if (["single_choice", "true_false", "image_based", "paragraph", "case_study"].includes(q.type)) {
      html += `<div class="d-flex flex-column gap-2 mt-3">`;
      q.options.forEach(opt => {
        const checked = ans.selected.includes(opt.id) ? "checked" : "";
        html += `<label class="form-check border rounded p-2">
            <input class="form-check-input me-2" type="radio" name="opt" value="${opt.id}" ${checked}>
            ${escapeHtml(opt.text)}
          </label>`;
      });
      html += `</div>`;
    } else if (q.type === "multiple_choice") {
      html += `<div class="d-flex flex-column gap-2 mt-3">`;
      q.options.forEach(opt => {
        const checked = ans.selected.includes(opt.id) ? "checked" : "";
        html += `<label class="form-check border rounded p-2">
            <input class="form-check-input me-2" type="checkbox" name="opt" value="${opt.id}" ${checked}>
            ${escapeHtml(opt.text)}
          </label>`;
      });
      html += `</div>`;
    } else if (q.type === "integer") {
      html += `<input type="number" class="form-control mt-3" id="integerInput" style="max-width:220px;"
                 value="${ans.integer !== null && ans.integer !== undefined ? ans.integer : ""}" placeholder="Enter numeric answer">`;
    } else if (q.type === "fill_blank") {
      html += `<input type="text" class="form-control mt-3" id="textInput" style="max-width:400px;"
                 value="${ans.text ? escapeAttr(ans.text) : ""}" placeholder="Type your answer">`;
    }

    card.innerHTML = html;

    card.querySelectorAll('input[name="opt"]').forEach(input => {
      input.addEventListener("change", onAnswerChange);
    });
    const intInput = qs("integerInput");
    if (intInput) intInput.addEventListener("input", onAnswerChange);
    const textInput = qs("textInput");
    if (textInput) textInput.addEventListener("input", onAnswerChange);

    renderPalette();
    markVisited(q.id);
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }
  function escapeAttr(str) { return (str || "").replace(/"/g, "&quot;"); }

  function renderPalette() {
    const container = qs("palette");
    container.innerHTML = "";
    questions.forEach((q, idx) => {
      const status = (q.answer && q.answer.status) || "not_visited";
      let cls = "palette-not-visited";
      if (status === "answered") cls = "palette-answered";
      else if (status === "not_answered") cls = "palette-not-answered";
      else if (status === "marked_for_review") cls = "palette-marked";
      else if (status === "answered_marked") cls = "palette-answered-marked";

      const btn = document.createElement("button");
      btn.className = `palette-btn ${cls} ${idx === currentIndex ? "palette-current" : ""}`;
      btn.textContent = idx + 1;
      btn.addEventListener("click", () => goTo(idx));
      container.appendChild(btn);
    });
  }

  function markVisited(questionId) {
    fetch(DATA.urls.visit, { method: "POST", headers: authHeaders(), body: JSON.stringify({ question_id: questionId }) })
      .catch(() => {});
  }

  // ---------------- Answer collection ----------------
  function collectCurrentResponse() {
    const q = currentQuestion();
    const payload = { question_id: q.id };

    if (["single_choice", "true_false", "image_based", "paragraph", "case_study"].includes(q.type)) {
      const checked = document.querySelector('input[name="opt"]:checked');
      payload.selected_option_ids = checked ? [parseInt(checked.value, 10)] : [];
    } else if (q.type === "multiple_choice") {
      const checked = Array.from(document.querySelectorAll('input[name="opt"]:checked'));
      payload.selected_option_ids = checked.map(c => parseInt(c.value, 10));
    } else if (q.type === "integer") {
      const val = qs("integerInput") ? qs("integerInput").value : "";
      payload.integer_answer = val === "" ? null : parseInt(val, 10);
    } else if (q.type === "fill_blank") {
      payload.text_answer = qs("textInput") ? qs("textInput").value : "";
    }
    return payload;
  }

  function applyLocalState(payload, forcedStatus) {
    const q = currentQuestion();
    q.answer = q.answer || {};
    q.answer.selected = payload.selected_option_ids || q.answer.selected || [];
    q.answer.integer = "integer_answer" in payload ? payload.integer_answer : q.answer.integer;
    q.answer.text = "text_answer" in payload ? payload.text_answer : q.answer.text;

    if (forcedStatus) {
      q.answer.status = forcedStatus;
    } else {
      const hasResponse = (q.answer.selected && q.answer.selected.length) ||
        (q.answer.integer !== null && q.answer.integer !== undefined) ||
        (q.answer.text && q.answer.text.length);
      q.answer.status = hasResponse ? "answered" : "not_answered";
    }
  }

  function onAnswerChange() { dirty = true; }

  async function saveCurrent(action = "save") {
    const payload = collectCurrentResponse();
    payload.action = action;
    setSaveStatus("Saving...", false);
    try {
      const res = await fetch(DATA.urls.save, { method: "POST", headers: authHeaders(), body: JSON.stringify(payload) });
      const data = await res.json();
      if (data.ok) {
        applyLocalState(payload, data.status);
        renderPalette();
        setSaveStatus("Saved");
      } else {
        setSaveStatus("Save failed", false);
      }
    } catch (e) {
      setSaveStatus("Offline - will retry", false);
    }
    dirty = false;
  }

  // ---------------- Navigation ----------------
  async function goTo(idx) {
    if (idx < 0 || idx >= questions.length) return;
    if (dirty) await saveCurrent("save");
    currentIndex = idx;
    renderQuestion();
  }

  qs("prevBtn").addEventListener("click", () => goTo(currentIndex - 1));
  qs("saveNextBtn").addEventListener("click", async () => { await saveCurrent("save"); goTo(currentIndex + 1); });
  qs("markBtn").addEventListener("click", async () => { await saveCurrent("mark_for_review"); goTo(currentIndex + 1); });
  qs("clearBtn").addEventListener("click", async () => {
    const q = currentQuestion();
    // Clear locally first, then go through the same saveCurrent() path as
    // every other answer change - it already retries on network failure
    // and shows "Offline - will retry", instead of firing a one-off fetch
    // with no error handling that could silently do nothing on a bad
    // connection and leave the student unsure whether it worked.
    q.answer = { status: "not_answered", selected: [], integer: null, text: null };
    renderQuestion();
    await saveCurrent("clear");
  });

  // ---------------- Auto save every 10s ----------------
  setInterval(() => { if (dirty) saveCurrent("save"); }, 10000);

  // ---------------- Timer (server-authoritative, resynced via heartbeat) ----------------
  function formatTime(sec) {
    const m = Math.floor(sec / 60).toString().padStart(2, "0");
    const s = Math.floor(sec % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  }

  function tick() {
    remaining = Math.max(0, remaining - 1);
    const el = qs("timer");
    el.textContent = formatTime(remaining);
    el.classList.toggle("timer-warning", remaining <= 60);
    if (remaining <= 0) {
      doSubmit(true);
    }
  }
  qs("timer").textContent = formatTime(remaining);
  setInterval(tick, 1000);

  async function resync() {
    try {
      const res = await fetch(DATA.urls.heartbeat, { method: "POST", headers: authHeaders() });
      const data = await res.json();
      if (data.ok) {
        remaining = data.remaining_seconds;
        if (data.status !== "in_progress") {
          window.location.href = "/result/" + DATA.attemptId;
        }
      }
    } catch (e) { /* offline: keep local countdown */ }
  }
  setInterval(resync, 15000);
  window.addEventListener("online", resync);

  // ---------------- Submit ----------------
  function doSubmit(auto = false) {
    if (submitting) return;
    submitting = true;
    qs("autoFlag").value = auto ? "1" : "0";
    document.getElementById("submitForm").submit();
  }

  qs("submitBtn").addEventListener("click", async () => {
    if (!confirm("Are you sure you want to submit the exam? This cannot be undone.")) return;
    await saveCurrent("save");
    doSubmit(false);
  });

  window.addEventListener("beforeunload", (e) => {
    if (!submitting) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // ---------------- Anti-cheating ----------------
  document.addEventListener("copy", e => e.preventDefault());
  document.addEventListener("cut", e => e.preventDefault());
  document.addEventListener("paste", e => e.preventDefault());
  document.addEventListener("contextmenu", e => e.preventDefault());
  document.addEventListener("selectstart", e => e.preventDefault());

  function showWarning(title, text) {
    qs("warningTitle").textContent = title;
    qs("warningText").textContent = text;
    new bootstrap.Modal(qs("warningModal")).show();
  }
  const STRICT_MODE = !!DATA.strictAntiCheat;
  const MAX_WARNINGS = DATA.maxWarnings || 3;
  let strictSubmitTriggered = false;
  let autoSubmitTriggered = false;

  function trackEvent(event, strict) {
    return fetch(DATA.urls.track, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, strict: !!strict }),
    }).then(r => r.json()).catch(() => null);
  }

  function strictAutoSubmit(reasonLabel, event) {
    if (strictSubmitTriggered || submitting) return;
    strictSubmitTriggered = true;
    trackEvent(event, true);
    showWarning(
      "Exam Closed Due To Security Policy",
      `${reasonLabel} This exam uses strict security mode, so it has been closed and submitted automatically.`
    );
    setTimeout(async () => {
      await saveCurrent("save");
      doSubmit(true);
    }, 1500);
  }

  // ---------------------------------------------------------------------
  // Phase 2 - Unified 3-warning system. Every violation type (tab switch,
  // fullscreen exit, face missing, multiple faces, looking away, voice
  // detected, ...) funnels through this ONE function, which asks the
  // server what the real warning count is (server-authoritative - the
  // client never decides this itself, so refresh / localStorage edits /
  // direct API calls can't bypass it). Exposed globally so
  // exam-security.js (camera/mic detection) can call it too.
  // ---------------------------------------------------------------------
  const WARNING_LABELS = {
    tab_switch: "You switched tabs or minimized the browser.",
    fullscreen_exit: "You exited full-screen mode.",
    blur: "This browser window lost focus.",
    devtools: "Developer tools were detected.",
    face_missing: "Your face was not visible to the camera.",
    multiple_faces: "More than one face was detected by the camera.",
    looking_away: "You appeared to look away from the screen repeatedly.",
    voice_detected: "Voice/audio activity was detected during the exam.",
  };

  async function reportSecurityViolation(event) {
    if (submitting || autoSubmitTriggered) return;

    if (STRICT_MODE) {
      strictAutoSubmit(WARNING_LABELS[event] || "A security rule was violated.", event);
      return;
    }

    const data = await trackEvent(event, false);
    if (!data || !data.ok) return;

    if (data.strict_auto_submit) return; // handled by strictAutoSubmit path already

    const count = data.warning_count;
    const max = data.max_warnings || MAX_WARNINGS;

    if (data.auto_submit || count >= max) {
      autoSubmitTriggered = true;
      showWarning(
        "Exam Terminated",
        `Warning ${max}/${max}. You have exceeded the maximum number of warnings. Your exam is being submitted automatically.`
      );
      setTimeout(async () => {
        await saveCurrent("save");
        doSubmit(true);
      }, 2000);
      return;
    }

    if (count === max - 1) {
      showWarning(
        `Warning ${count}/${max}`,
        `${WARNING_LABELS[event] || "Please follow the exam rules."} One more violation will automatically submit your exam.`
      );
    } else {
      showWarning(
        `Warning ${count}/${max}`,
        `${WARNING_LABELS[event] || "Please follow the exam rules."} This has been recorded.`
      );
    }
  }
  window.reportSecurityViolation = reportSecurityViolation;

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      reportSecurityViolation("tab_switch");
    }
  });

  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) {
      reportSecurityViolation("fullscreen_exit");
    }
  });

  // Strict-mode only: browser losing focus (alt-tab, another app, another window)
  if (STRICT_MODE) {
    window.addEventListener("blur", () => {
      strictAutoSubmit("This browser window lost focus.", "blur");
    });
  }

  // Lightweight DevTools heuristic: large delta between outer/inner window size
  let devtoolsWarned = false;
  setInterval(() => {
    const widthDelta = window.outerWidth - window.innerWidth;
    const heightDelta = window.outerHeight - window.innerHeight;
    if ((widthDelta > 160 || heightDelta > 160) && !devtoolsWarned) {
      devtoolsWarned = true;
      if (STRICT_MODE) {
        strictAutoSubmit("Developer tools were detected.", "devtools");
        return;
      }
      reportSecurityViolation("devtools");
      setTimeout(() => { devtoolsWarned = false; }, 15000);
    }
  }, 3000);

  document.addEventListener("keydown", (e) => {
    const blocked = (
      e.key === "F12" ||
      (e.ctrlKey && e.shiftKey && ["I", "J", "C"].includes(e.key.toUpperCase())) ||
      (e.ctrlKey && ["c", "v", "x", "u", "s", "p"].includes(e.key.toLowerCase()))
    );
    if (blocked) e.preventDefault();
  });

  // ---------------- Init ----------------
  renderQuestion();
})();
