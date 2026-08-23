/* =====================================================================
   FaceID School ERP -- common helpers
   - Sidebar collapse, modal helpers, status badge, fetch wrapper
   - Upload-photo modal logic (file / webcam, 3-step workflow)
   - Per-page renderers are loaded by app.js / *.js after this file
   ===================================================================== */
(() => {
  "use strict";

  // ---------------------------------------------------------------- helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  window.$ = $;
  window.$$ = $$;

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
  window.escapeHtml = escapeHtml;

  async function j(url, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      opts.headers = { ...(opts.headers || {}), "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "" };
    }
    const res = await fetch(url, opts);
    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = { raw: text }; }
    if (!res.ok) {
      const err = new Error((body && body.error) || `HTTP ${res.status}`);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }
  window.j = j;

  // ---------------------------------------------------------- sidebar
  function initSidebar() {
    const body = document.body;
    const KEY = "faceid-sidebar-collapsed";
    if (localStorage.getItem(KEY) === "1") body.classList.add("sidebar-collapsed");
    const btn = $("#sidebar-toggle");
    if (btn) {
      btn.addEventListener("click", () => {
        if (window.matchMedia("(max-width: 768px)").matches) {
          body.classList.toggle("sidebar-open");
        } else {
          body.classList.toggle("sidebar-collapsed");
          localStorage.setItem(KEY, body.classList.contains("sidebar-collapsed") ? "1" : "0");
        }
      });
    }
    $$(".nav-accordion-trigger").forEach((trigger) => {
      trigger.addEventListener("click", () => {
        const group = trigger.closest(".nav-accordion");
        if (!group) return;
        const willOpen = !group.classList.contains("open");
        if (willOpen) {
          // Collapse other open top-level accordions so navigation stays compact
          $$(".nav-accordion.open").forEach((other) => {
            if (other !== group) other.classList.remove("open");
          });
        }
        group.classList.toggle("open", willOpen);
      });
    });
    $$(".nav-sub-trigger").forEach((trigger) => {
      trigger.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const subGroup = trigger.closest(".nav-sub-accordion");
        if (subGroup) subGroup.classList.toggle("open");
      });
    });
  }

  function initProfileMenu() {
    const trigger = $(".profile-pill");
    const menu = $("#profile-menu");
    if (!trigger || !menu) return;
    const close = () => { menu.hidden = true; trigger.setAttribute("aria-expanded", "false"); };
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      menu.hidden = !menu.hidden;
      trigger.setAttribute("aria-expanded", String(!menu.hidden));
    });
    document.addEventListener("click", (event) => { if (!event.target.closest(".profile-menu-wrap")) close(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  }

  // ---------------------------------------------------------- modal helpers
  function openModal(sel) {
    const m = $(sel);
    if (m) { m.classList.add("open"); m.setAttribute("aria-hidden", "false"); }
  }
  function closeModal(sel) {
    const m = $(sel);
    if (m) { m.classList.remove("open"); m.setAttribute("aria-hidden", "true"); }
  }
  window.openModal = openModal;
  window.closeModal = closeModal;

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t && t.dataset && t.dataset.close) {
      const m = t.closest(".modal");
      if (m) { m.classList.remove("open"); m.setAttribute("aria-hidden", "true"); }
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      $$(".modal.open").forEach((m) => { m.classList.remove("open"); m.setAttribute("aria-hidden", "true"); });
    }
  });

  // ---------------------------------------------------------- result dialog
  function showResult({ title, ok, message }) {
    $("#result-title").textContent = title;
    const body = $("#result-body");
    body.innerHTML = `
      <div class="big ${ok ? "ok" : "err"}">
        <span>${ok ? "&#10003;" : "&#10007;"}</span>
        <span>${escapeHtml(title)}</span>
      </div>
      <div>${escapeHtml(message)}</div>`;
    openModal("#result-dialog");
  }
  window.showResult = showResult;

  // ---------------------------------------------------------- form modal
  // Builds a form inside #form-modal-body and wires Save to onSave(values).
  function openFormModal({ title, fields, onSave, initial = {} }) {
    $("#form-modal-title").textContent = title;
    const body = $("#form-modal-body");
    body.innerHTML = "";
    fields.forEach((f) => {
      const row = document.createElement("div");
      row.className = "form-row";
      const id = `f-${f.name}`;
      const value = initial[f.name] ?? f.value ?? "";
      let inputHtml = "";
      if (f.type === "select") {
        const opts = (f.options || []).map(
          (o) => `<option value="${escapeHtml(o.v)}" ${String(value) === String(o.v) ? "selected" : ""}>${escapeHtml(o.t)}</option>`
        ).join("");
        inputHtml = `<select id="${id}" name="${f.name}" ${f.required ? "required" : ""}>${opts}</select>`;
      } else if (f.type === "textarea") {
        inputHtml = `<textarea id="${id}" name="${f.name}" ${f.required ? "required" : ""}>${escapeHtml(value)}</textarea>`;
      } else {
        inputHtml = `<input id="${id}" name="${f.name}" type="${f.type || "text"}"
                            value="${escapeHtml(value)}" ${f.required ? "required" : ""}
                            ${f.readonly ? "readonly" : ""}
                            ${f.disabled ? "disabled" : ""}
                            ${f.accept ? `accept="${f.accept}"` : ""} />`;
      }
      row.innerHTML = `<label for="${id}">${escapeHtml(f.label)}${f.required ? " *" : ""}</label>${inputHtml}`;
      body.appendChild(row);
    });
    const save = $("#form-modal-save");
    const handler = async () => {
      const missing = fields
        .filter((f) => f.required)
        .filter((f) => {
          const el = body.querySelector(`[name="${f.name}"]`);
          return !el || !String(el.value ?? "").trim();
        });
      if (missing.length) {
        showResult({
          title: "Missing required fields",
          ok: false,
          message: `Please fill: ${missing.map((f) => f.label).join(", ")}`,
        });
        return;
      }
      const values = {};
      fields.forEach((f) => {
        const el = body.querySelector(`[name="${f.name}"]`);
        if (el) values[f.name] = el.value;
      });
      try {
        await onSave(values);
        closeModal("#form-modal");
      } catch (err) {
        showResult({ title: "Save failed", ok: false, message: err.message });
      }
    };
    save.onclick = handler;
    openModal("#form-modal");
  }
  window.openFormModal = openFormModal;

  // ---------------------------------------------------------- status badge
  function studentStatusBadge(s, todayPresentSet) {
    if (s.has_encoding && todayPresentSet && todayPresentSet.has(s.id)) {
      return `<span class="badge ok">Attendance Marked</span>`;
    }
    if (s.has_encoding) {
      return `<span class="badge info">Face Registered</span>`;
    }
    if (!s.is_active) {
      return `<span class="badge muted">Inactive</span>`;
    }
    return `<span class="badge warn">Pending Registration</span>`;
  }
  window.studentStatusBadge = studentStatusBadge;

  // ---------------------------------------------------------- upload modal
  // 3-step workflow: Front -> Left -> Right
  // The Upload object is reused. It now stores a `photos` map (one file per
  // step) plus the current step. The legacy `Upload.file` getter is kept for
  // any external code that still reads it.
  const STEPS = [
    { key: "front", label: "Front Face", hint: "Step 1 of 3 \u2014 Capture a clear FRONT-facing face." },
    { key: "left",  label: "Left Face",  hint: "Step 2 of 3 \u2014 Turn the head slightly to the LEFT." },
    { key: "right", label: "Right Face", hint: "Step 3 of 3 \u2014 Turn the head slightly to the RIGHT." },
  ];

  const Upload = {
    personId: null,
    personName: "",
    personType: "student",
    step: 0,                       // current step index
    photos: { front: null, left: null, right: null }, // one File per step
    previewUrl: null,              // current preview blob URL (for the active step)
    stream: null,                  // active MediaStream (webcam)
  };
  // Backwards-compatible aliases for any old code that reads Upload.studentId / Upload.studentName / Upload.file.
  Object.defineProperty(Upload, "studentId", {
    get() { return Upload.personId; },
    set(v) { Upload.personId = v; },
  });
  Object.defineProperty(Upload, "studentName", {
    get() { return Upload.personName; },
    set(v) { Upload.personName = v; },
  });
  Object.defineProperty(Upload, "file", {
    get() { return Upload.photos[Upload.stepKey()] || null; },
    set(v) { Upload.photos[Upload.stepKey()] = v; },
  });
  Upload.stepKey = function () { return STEPS[Upload.step] ? STEPS[Upload.step].key : "front"; };
  window.Upload = Upload;

  function switchTab(name) {
    $$(".tab", $("#upload-modal")).forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    $$(".tab-panel", $("#upload-modal")).forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
    if (name !== "webcam") stopWebcam();
  }
  window.switchUploadTab = switchTab;

  // Re-renders the single preview, the stepper, the navigation buttons and
  // the register button. Called any time the workflow state changes.
  function refreshUploadUI() {
    const stepper = $("#upload-stepper");
    if (stepper) {
      $$(".step", stepper).forEach((el) => {
        const idx = STEPS.findIndex((s) => s.key === el.dataset.step);
        el.classList.toggle("active", idx === Upload.step);
        el.classList.toggle("done", Upload.photos[el.dataset.step] != null && idx !== Upload.step);
      });
    }

    const meta = STEPS[Upload.step] || STEPS[0];
    const hint = $("#upload-step-hint");
    if (hint) hint.textContent = meta.hint;

    const current = Upload.photos[meta.key];
    if (current) {
      setPreview(current._previewUrl || URL.createObjectURL(current));
      current._previewUrl = current._previewUrl || (Upload.previewUrl || null);
    } else {
      setPreview(null);
    }

    const status = $("#preview-status");
    if (status) {
      const cap = STEPS[Upload.step].label;
      if (current) {
        status.textContent = `${cap} captured. Click "Retake" to replace it, or "Next" to continue.`;
      } else {
        status.textContent = `Awaiting capture for Step ${Upload.step + 1} \u2014 ${cap}.`;
      }
    }

    const prev = $("#step-prev");
    const next = $("#step-next");
    if (prev) prev.disabled = Upload.step === 0;
    if (next) {
      next.disabled = !current;
      next.textContent = Upload.step === STEPS.length - 1 ? "Finish" : "Next";
    }

    const submit = $("#upload-submit");
    if (submit) {
      submit.disabled = !(Upload.photos.front && Upload.photos.left && Upload.photos.right);
    }
  }

  function setStep(idx) {
    Upload.step = Math.max(0, Math.min(STEPS.length - 1, idx));
    // Reset webcam UI for the new step so the user can capture fresh.
    stopWebcam();
    const cap = $("#webcam-capture");
    const retake = $("#webcam-retake");
    if (cap) cap.disabled = true;
    if (retake) retake.hidden = true;
    const status = $("#upload-status");
    if (status) { status.textContent = ""; status.classList.remove("ok", "err"); }
    refreshUploadUI();
  }

  function openUploadModal(personId, personName, personType = "student") {
    Upload.personId = personId;
    Upload.personName = personName || "";
    Upload.personType = personType || "student";
    Upload.photos = { front: null, left: null, right: null };
    Upload.step = 0;
    const title = $("#upload-modal-title");
    if (title) {
      const kindLabel = Upload.personType === "teacher" ? "Teacher" : "Student";
      title.textContent = `Register Face \u2014 ${Upload.personName} (${kindLabel})`;
    }
    switchTab("file");
    setStep(0);
    openModal("#upload-modal");
  }
  window.openUploadModal = openUploadModal;

  function closeUploadModal() {
    closeModal("#upload-modal");
    stopWebcam();
    // Revoke any object URLs we created.
    Object.values(Upload.photos).forEach((f) => { if (f && f._previewUrl) URL.revokeObjectURL(f._previewUrl); });
    Upload.photos = { front: null, left: null, right: null };
    Upload.previewUrl = null;
  }
  window.closeUploadModal = closeUploadModal;

  function setPreview(url) {
    if (Upload.previewUrl && Upload.previewUrl !== url) URL.revokeObjectURL(Upload.previewUrl);
    Upload.previewUrl = url;
    const img = $("#preview-img");
    const empty = $("#preview-empty");
    if (url) {
      img.src = url; img.style.display = "block"; empty.style.display = "none";
    } else {
      img.removeAttribute("src"); img.style.display = "none"; empty.style.display = "flex";
    }
  }

  function acceptFile(file) {
    if (!file) return;
    const okType = /^image\/(jpeg|jpg|png)$/.test(file.type) || /\.(jpe?g|png)$/i.test(file.name || "");
    if (!okType) {
      showResult({ title: "Unsupported file", ok: false, message: "Only .jpg, .jpeg or .png images are accepted." });
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      showResult({ title: "File too large", ok: false, message: `Image must be smaller than 8 MB (got ${(file.size / 1024 / 1024).toFixed(1)} MB).` });
      return;
    }
    const key = Upload.stepKey();
    // Replace only the current step's image; keep the others.
    if (Upload.photos[key] && Upload.photos[key]._previewUrl) {
      URL.revokeObjectURL(Upload.photos[key]._previewUrl);
    }
    file._previewUrl = URL.createObjectURL(file);
    Upload.photos[key] = file;
    refreshUploadUI();
  }
  window.acceptFile = acceptFile;

  // webcam
  async function startWebcam() {
    try {
      Upload.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
        audio: false,
      });
      const v = $("#webcam");
      v.srcObject = Upload.stream;
      await v.play();
      $("#webcam-start").disabled = true;
      $("#webcam-capture").disabled = false;
      $("#webcam-retake").hidden = true;
      $("#webcam-hint").textContent = "Camera is on. Position the face inside the frame, then click Capture.";
    } catch (err) {
      $("#webcam-hint").textContent = `Could not open camera: ${err.message || err}`;
    }
  }
  function stopWebcam() {
    if (Upload.stream) { Upload.stream.getTracks().forEach((t) => t.stop()); Upload.stream = null; }
    const v = $("#webcam"); if (v) v.srcObject = null;
    const start = $("#webcam-start");
    if (start) start.disabled = false;
  }
  function captureFromWebcam() {
    const v = $("#webcam"), c = $("#snap");
    if (!v.videoWidth) return;
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    c.toBlob((blob) => {
      if (blob) {
        const f = new File([blob], `${Upload.stepKey()}-webcam.jpg`, { type: "image/jpeg" });
        acceptFile(f);
      }
    }, "image/jpeg", 0.92);
    $("#webcam-capture").disabled = true;
    $("#webcam-retake").hidden = false;
    stopWebcam();
  }
  // Retake replaces ONLY the current step image. Previously captured
  // images for the other steps are preserved.
  function retake() {
    const key = Upload.stepKey();
    if (Upload.photos[key] && Upload.photos[key]._previewUrl) {
      URL.revokeObjectURL(Upload.photos[key]._previewUrl);
    }
    Upload.photos[key] = null;
    Upload.previewUrl = null;
    setStep(Upload.step);
  }

  function bindUploadModal() {
    $$("#upload-modal .tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
    $("#file-label").addEventListener("click", (e) => {
      if (e.target.tagName !== "INPUT") $("#file-input").click();
    });
    $("#file-input").addEventListener("change", () => {
      const f = $("#file-input").files && $("#file-input").files[0];
      if (f) acceptFile(f);
      // Clear the input so the same file can be re-picked if needed.
      $("#file-input").value = "";
    });

    document.addEventListener("paste", (e) => {
      const upload = $("#upload-modal");
      if (!upload.classList.contains("open")) return;
      if (!$('.tab-panel[data-panel="file"]').classList.contains("active")) return;
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (const it of items) {
        if (it.kind === "file" && it.type.startsWith("image/")) {
          const f = it.getAsFile(); if (f) { acceptFile(f); break; }
        }
      }
    });

    $("#webcam-start").addEventListener("click", startWebcam);
    $("#webcam-capture").addEventListener("click", captureFromWebcam);
    $("#webcam-retake").addEventListener("click", retake);

    // Step navigation
    $("#step-prev").addEventListener("click", () => setStep(Upload.step - 1));
    $("#step-next").addEventListener("click", () => {
      if (Upload.step < STEPS.length - 1) setStep(Upload.step + 1);
    });

    // Clicking a stepper chip jumps to that step (for retake convenience)
    $$("#upload-stepper .step").forEach((el) => {
      el.addEventListener("click", () => {
        const idx = STEPS.findIndex((s) => s.key === el.dataset.step);
        if (idx >= 0) setStep(idx);
      });
    });

    $("#upload-submit").addEventListener("click", () => {
      if (!Upload.personId) return;
      const front = Upload.photos.front, left = Upload.photos.left, right = Upload.photos.right;
      if (!front || !left || !right) {
        showResult({
          title: "All 3 Photos Required",
          ok: false,
          message: "Please capture front, left, and right face photos before registering.",
        });
        return;
      }
      $("#upload-submit").disabled = true;
      $("#step-prev").disabled = true;
      $("#step-next").disabled = true;
      const status = $("#upload-status");
      status.textContent = "Registering face...";
      status.classList.remove("ok", "err");

      const fd = new FormData();
      fd.append("front", front, front.name || "front.jpg");
      fd.append("left",  left,  left.name  || "left.jpg");
      fd.append("right", right, right.name || "right.jpg");

      const endpoint = Upload.personType === "teacher"
        ? `/api/teachers/${Upload.personId}/photo`
        : `/api/students/${Upload.personId}/photo`;

      const xhr = new XMLHttpRequest();
      xhr.open("POST", endpoint, true);
      xhr.setRequestHeader("X-CSRF-Token", document.querySelector('meta[name="csrf-token"]')?.content || "");
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = (e.loaded / e.total) * 100;
        status.textContent = `Uploading... ${pct.toFixed(0)}%`;
      };
      xhr.onload = () => {
        let body = null;
        try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) {
          status.textContent = "Face Registered Successfully";
          status.classList.add("ok");
          showResult({ title: "Face Registered Successfully", ok: true,
            message: `${Upload.personName}'s face has been encoded from 3 angles and is now ready for live recognition.` });
          closeUploadModal();
          window.dispatchEvent(new CustomEvent("face-registered", {
            detail: { personId: Upload.personId, studentId: Upload.personId, personType: Upload.personType }
          }));
        } else {
          const msg = (body && body.error) || `Upload failed (HTTP ${xhr.status}).`;
          status.textContent = msg;
          status.classList.add("err");
          const isNoFace = /no face/i.test(msg);
          showResult({
            title: isNoFace ? "No Face Detected" : "Upload Failed",
            ok: false,
            message: isNoFace
              ? "We couldn't find a face in one of the images. Retake the affected step with a clearer photo and good lighting."
              : msg,
          });
          $("#upload-submit").disabled = false;
          $("#step-prev").disabled = Upload.step === 0;
          $("#step-next").disabled = !Upload.photos[Upload.stepKey()];
        }
      };
      xhr.onerror = () => {
        status.textContent = "Network error";
        status.classList.add("err");
        showResult({ title: "Network Error", ok: false, message: "The request didn't reach the server. Check your connection and try again." });
        $("#upload-submit").disabled = false;
        $("#step-prev").disabled = Upload.step === 0;
        $("#step-next").disabled = !Upload.photos[Upload.stepKey()];
      };
      xhr.send(fd);
    });
  }

  // ------------------------------------------------------------ bootstrap
  document.addEventListener("DOMContentLoaded", () => {
    initSidebar();
    initProfileMenu();
    bindUploadModal();
  });
})();
