/* =====================================================================
   FaceID School ERP -- per-page controllers
   Page detection: looks at <body data-page="..."> injected by the
   server-side template via @app.context_processor would be even nicer,
   but we infer the page from the active nav link for simplicity.
   ===================================================================== */
(() => {
  "use strict";

  const $ = window.$, $$ = window.$$;
  const j = window.j;
  const escapeHtml = window.escapeHtml;
  const openUploadModal = window.openUploadModal;
  const showResult = window.showResult;
  const openFormModal = window.openFormModal;
  const studentStatusBadge = window.studentStatusBadge;

  const set = (id, value) => {
    const el = $(id);
    if (el) el.textContent = value ?? "--";
  };

  function cameraMetaHtml(c) {
    return `
      <span class="pill"><i data-lucide="map-pin"></i> ${escapeHtml(c.location || "Campus")}</span>
      <span class="pill"><i data-lucide="gauge"></i> FPS <b data-cam-fps="${c.id}">--</b></span>
      <span class="pill"><i data-lucide="eye"></i> Detected <b data-cam-detected="${c.id}">0</b></span>
      <span class="pill"><i data-lucide="user-check"></i> Recognized <b data-cam-recognized="${c.id}">0</b></span>
      <span class="pill"><i data-lucide="help-circle"></i> Unknown <b data-cam-unknown="${c.id}">0</b></span>
      <span class="pill"><i data-lucide="link"></i> ${escapeHtml(c.source)}</span>`;
  }

  let isCamStatusInFlight = false;
  async function refreshCameraStatus(root = document) {
    if (document.hidden || isCamStatusInFlight) return;
    isCamStatusInFlight = true;
    try {
      const statuses = await j("/api/cameras/status");
      if (statuses && typeof statuses === "object") {
        Object.values(statuses).forEach((s) => {
          const set = (key, value) => {
            $$(`[data-cam-${key}="${s.camera_id}"]`, root).forEach((el) => { el.textContent = value; });
          };
          set("fps", s.fps);
          set("detected", s.detected_faces);
          set("recognized", s.recognized_faces);
          set("unknown", s.unknown_faces);
        });
      }
    } catch { /* status is best-effort */ }
    finally {
      isCamStatusInFlight = false;
    }
  }

  // ------------------------------------------------------ active page
  function activePage() {
    if (document.body && document.body.dataset && document.body.dataset.page) {
      return document.body.dataset.page.trim();
    }
    // Safe fallbacks only when data-page is omitted
    if (document.querySelector("#enterprise-attendance-table")) return "attendance_enterprise";
    return "";
  }

  // ============================================================ DASHBOARD
  let dashboardTimers = [];
  function clearDashboardTimers() {
    dashboardTimers.forEach((t) => clearInterval(t));
    dashboardTimers = [];
  }

  function initDashboard() {
    clearDashboardTimers();

    const camGrid = $("#cam-grid");
    const attendanceTbody = $("#attendance-table tbody");
    const unknownList = $("#unknown-list");
    const scopePill = $("#scope-pill");

    let isStatsInFlight = false;
    let isAttendanceInFlight = false;
    let isUnknownInFlight = false;

    async function refreshStats() {
      if (isStatsInFlight || document.hidden) return;
      isStatsInFlight = true;
      try {
        const s = await j("/api/stats");
        if (s) {
          $("#stat-total").textContent = s.total_students ?? "--";
          $("#stat-present").textContent = s.present_today ?? "--";
          $("#stat-absent").textContent = s.absent_today ?? "--";
          $("#stat-unknown").textContent = s.unknown_today ?? "--";
          $("#stat-cams").textContent = s.active_cameras ?? "--";
          set("#stat-teachers-present", s.teachers_present);
          set("#stat-teachers-absent", s.teachers_absent);
          set("#stat-late", s.late_today);
          set("#stat-entries", s.today_entries);
          set("#stat-exits", s.today_exits);
          const label = s.class_name ? `${s.class_name}${s.section ? " " + s.section : ""}` : "All Classes";
          if (scopePill) scopePill.textContent = label;
        }
      } catch (e) { /* noop */ }
      finally {
        isStatsInFlight = false;
      }
    }

    async function refreshCams() {
      if (!camGrid) return;
      try {
        const cams = await j("/api/cameras");
        if (!cams || !cams.length) { camGrid.innerHTML = `<div class="empty">No cameras configured yet.</div>`; return; }
        camGrid.innerHTML = "";
        cams.forEach((c) => {
          const card = document.createElement("div");
          card.className = "cam-card";
          card.innerHTML = `
            <div class="cam-head">
              <div>
                <div class="cam-name">${escapeHtml(c.name)}</div>
                <div class="cam-loc">${escapeHtml(c.location || "")}</div>
              </div>
              <span class="badge ${c.is_active ? "ok" : "muted"}">${c.is_active ? "Active" : "Stopped"}</span>
            </div>
            <img class="cam-stream" src="/stream/${c.id}" alt="${escapeHtml(c.name)}" />
            <div class="cam-meta">
              ${cameraMetaHtml(c)}
            </div>
            <div class="cam-foot">
              <button class="btn-secondary" data-cam-action="start" data-id="${c.id}" ${c.is_active ? "disabled" : ""}>
                <i data-lucide="play"></i> Start
              </button>
              <button class="btn-secondary" data-cam-action="stop" data-id="${c.id}" ${c.is_active ? "" : "disabled"}>
                <i data-lucide="square"></i> Stop
              </button>
              <button class="btn-icon" data-cam-action="fullscreen" data-id="${c.id}" title="Fullscreen">
                <i data-lucide="maximize"></i>
              </button>
            </div>`;
          camGrid.appendChild(card);
        });
        if (window.lucide) lucide.createIcons();
        refreshCameraStatus(camGrid);
      } catch { /* noop */ }
    }

    async function refreshAttendance() {
      if (!attendanceTbody || isAttendanceInFlight || document.hidden) return;
      isAttendanceInFlight = true;
      try {
        const rows = await j("/api/enterprise/attendance?type=student");
        attendanceTbody.innerHTML = "";
        if (!rows || !rows.length) {
          attendanceTbody.innerHTML = `<tr><td colspan="6" class="empty">No attendance recorded today.</td></tr>`;
          return;
        }
        rows.forEach((r) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td>${r.in_time ? new Date(r.in_time).toLocaleTimeString() : "—"}</td>
            <td>${escapeHtml(r.name || "")}</td>
            <td>${escapeHtml(r.roll_no || "")}</td>
            <td>${escapeHtml(r.class_name || "")}</td>
            <td>${escapeHtml(r.camera_name || "")}</td>
            <td>${(r.confidence || 0).toFixed(2)}</td>`;
          attendanceTbody.appendChild(tr);
        });
      } catch { /* noop */ }
      finally {
        isAttendanceInFlight = false;
      }
    }

    async function refreshUnknown() {
      if (!unknownList || isUnknownInFlight || document.hidden) return;
      isUnknownInFlight = true;
      try {
        const rows = await j("/api/unknown-faces");
        const list = (rows || []).slice(0, 10);
        if (!list.length) {
          unknownList.innerHTML = `<li class="empty">No unknown faces recorded.</li>`;
          return;
        }
        unknownList.innerHTML = list.map((r) => `
          <li>
            <span><i data-lucide="help-circle"></i> ${escapeHtml(r.camera_name || "Unknown")}</span>
            <span class="muted small">${new Date(r.timestamp).toLocaleString()}</span>
          </li>`).join("");
        if (window.lucide) lucide.createIcons();
      } catch { /* noop */ }
      finally {
        isUnknownInFlight = false;
      }
    }

    camGrid && camGrid.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-cam-action]");
      if (!btn) return;
      const action = btn.dataset.camAction;
      const id = btn.dataset.id;
      if (action === "fullscreen") {
        const img = camGrid.querySelector(`.cam-stream`);
        if (img && img.requestFullscreen) img.requestFullscreen();
        return;
      }
      await j(`/api/cameras/${id}/${action}`, { method: "POST" });
      refreshCams();
      refreshStats();
    });

    // Initial load
    refreshStats();
    refreshCams();
    refreshAttendance();

    // Sensible polling intervals:
    // Camera status: 12s, Attendance: 20s, Statistics: 25s, Unknown faces: 45s
    dashboardTimers.push(setInterval(() => { if (!document.hidden) refreshCameraStatus(camGrid); }, 12000));
    dashboardTimers.push(setInterval(() => { if (!document.hidden) refreshAttendance(); }, 20000));
    dashboardTimers.push(setInterval(() => { if (!document.hidden) refreshStats(); }, 25000));
    dashboardTimers.push(setInterval(() => { if (!document.hidden) refreshUnknown(); }, 45000));

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && activePage() === "dashboard") {
        refreshStats();
        refreshAttendance();
        refreshCameraStatus(camGrid);
      }
    });
  }

  // ============================================================ STUDENTS
  function initStudents() {
    const tbody = $("#students-table tbody");
    const countEl = $("#students-count");
    const titleEl = $("#students-title");
    const pager = $("#students-pager");
    const state = { class_name: "", section: "", search: "", page: 1, pageSize: 50 };

    // hydrate from URL (sidebar link)
    const path = window.location.pathname;
    const m = path.match(/^\/students\/class\/(.+)$/);
    if (m) {
      state.class_name = decodeURIComponent(m[1]);
      $("#filter-class").value = state.class_name;
    }

    async function refreshStats() {
      const params = new URLSearchParams();
      if (state.class_name) params.set("class_name", state.class_name);
      if (state.section) params.set("section", state.section);
      const s = await j(`/api/stats?${params.toString()}`);
      $("#stat-total").textContent = s.total_students;
      $("#stat-present").textContent = s.present_today;
      $("#stat-absent").textContent = s.absent_today;
      $("#stat-unknown").textContent = s.unknown_today;
      $("#stat-cams").textContent = s.active_cameras;
      $("#stat-scope").textContent = state.class_name
        ? `${state.class_name}${state.section ? " - " + state.section : ""}`
        : "All Classes";
    }

    async function load() {
      const params = new URLSearchParams();
      if (state.class_name) params.set("class_name", state.class_name);
      if (state.section) params.set("section", state.section);
      if (state.search) params.set("q", state.search);
      const rows = await j(`/api/students?${params.toString()}`);
      const total = rows.length;
      const start = (state.page - 1) * state.pageSize;
      const slice = rows.slice(start, start + state.pageSize);
      if (countEl) countEl.textContent = `${total} students`;
      if (titleEl) {
        titleEl.textContent = state.class_name
          ? `${state.class_name}${state.section ? " - " + state.section : ""} Students`
          : "All Students";
      }
      if (!slice.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty">No students found.</td></tr>`;
      } else {
        tbody.innerHTML = slice.map((s) => `
          <tr data-id="${s.id}">
            <td>${escapeHtml(s.roll_no)}</td>
            <td>${escapeHtml(s.name)}</td>
            <td>${s.id}</td>
            <td>${studentStatusBadge(s)}</td>
            <td>
              <button class="btn-primary" data-action="upload" data-id="${s.id}" data-name="${escapeHtml(s.name)}" title="Capture Face / Upload Photo">
                <i data-lucide="camera"></i> Face
              </button>
              <button class="btn-secondary" data-action="edit" data-id="${s.id}"><i data-lucide="edit"></i> Edit</button>
              <button class="btn-danger" data-action="delete" data-id="${s.id}"><i data-lucide="trash-2"></i> Delete</button>
            </td>
          </tr>`).join("");
      if (window.lucide) lucide.createIcons();
      }
      refreshStats();
      // pager
      const pages = Math.max(1, Math.ceil(total / state.pageSize));
      pager.innerHTML = "";
      for (let p = 1; p <= pages; p++) {
        const b = document.createElement("button");
        b.textContent = p;
        if (p === state.page) b.classList.add("active");
        b.addEventListener("click", () => { state.page = p; load(); });
        pager.appendChild(b);
      }
    }

    $("#filter-class").addEventListener("change", (e) => {
      state.class_name = e.target.value;
      state.page = 1;
      // Update URL so refreshing / sharing keeps the filter
      const url = state.class_name
        ? `/students/class/${encodeURIComponent(state.class_name)}`
        : `/students`;
      history.replaceState(null, "", url);
      load();
    });
    $("#filter-section").addEventListener("change", (e) => {
      state.section = e.target.value; state.page = 1; load();
    });
    let timer;
    $("#filter-search").addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.search = e.target.value; state.page = 1; load(); }, 200);
    });

    $("#btn-add-student").addEventListener("click", () => {
      const classes = window.__SCHOOL__ && window.__SCHOOL__.classes || [];
      const sections = (window.__SCHOOL__ && window.__SCHOOL__.sections) || ["A", "B", "C", "D"];
      openFormModal({
        title: "Add Student",
        fields: [
          { name: "student_id", label: "Student ID", value: "Auto-generated", readonly: true },
          { name: "roll_no", label: "Roll Number", required: true },
          { name: "name", label: "Student Name", required: true },
          { name: "class_name", label: "Class", type: "select", required: true, value: state.class_name || "",
            options: [{ v: "", t: "Select class..." }, ...classes.map((c) => ({ v: c, t: c }))] },
          { name: "section", label: "Section", type: "select", value: state.section || "",
            options: [{ v: "", t: "Select section..." }, ...sections.map((s) => ({ v: s, t: s }))] },
          { name: "mobile", label: "Mobile Number" },
          { name: "email", label: "Email", type: "email" },
        ],
        onSave: async (v) => {
          delete v.student_id;
          if (!v.class_name) {
            throw new Error("Please select a class.");
          }
          const created = await j("/api/students", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(v),
          });
          showResult({ title: "Student added", ok: true, message: `${created.name} added. Now register their face.` });
          load();
          openUploadModal(created.id, created.name);
        },
      });
    });

    tbody.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      if (action === "upload") {
        openUploadModal(parseInt(id, 10), btn.dataset.name);
      } else if (action === "delete") {
        if (!confirm("Delete this student and their face encoding?")) return;
        try { await j(`/api/students/${id}`, { method: "DELETE" }); load(); }
        catch (err) { showResult({ title: "Delete failed", ok: false, message: err.message }); }
      } else if (action === "edit") {
        try {
          const rows = await j(`/api/students?q=${id}`);
          const s = rows.find((r) => r.id == id) || (await j(`/api/students`)).find((r) => r.id == id);
          if (!s) return;
          const classes = (window.__SCHOOL__ && window.__SCHOOL__.classes) || [];
          const sections = (window.__SCHOOL__ && window.__SCHOOL__.sections) || ["A","B","C","D"];
          openFormModal({
            title: `Edit ${s.name}`,
            initial: s,
            fields: [
              { name: "id", label: "Student ID", readonly: true },
              { name: "roll_no", label: "Roll Number", required: true },
              { name: "name", label: "Student Name", required: true },
              { name: "class_name", label: "Class", type: "select", required: true, options: classes.map((c) => ({ v: c, t: c })) },
              { name: "section", label: "Section", type: "select", options: sections.map((sec) => ({ v: sec, t: sec })) },
              { name: "mobile", label: "Mobile Number" },
              { name: "email", label: "Email", type: "email" },
              { name: "is_active", label: "Active", type: "select", options: [{v:"true",t:"Active"},{v:"false",t:"Inactive"}] },
            ],
            onSave: async (v) => {
              v.is_active = String(v.is_active).toLowerCase() === "true";
              delete v.id;
              await j(`/api/students/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });
              showResult({ title: "Student updated", ok: true, message: `${v.name} updated.` });
              load();
            },
          });
        } catch (err) { showResult({ title: "Edit failed", ok: false, message: err.message }); }
      }
    });

    // Re-render when a face is registered so the badge flips.
    window.addEventListener("face-registered", () => load());

    load();
  }

  // ============================================================ ATTENDANCE
  function initAttendance() {
    const tbody = $("#attendance-table tbody");
    const countEl = $("#attendance-count");
    const state = { class_name: "", section: "", date: new Date().toISOString().slice(0, 10), camera_id: "", search: "" };

    function isTodayRecord(timestamp) {
      if (!timestamp) return false;
      const d = new Date(timestamp);
      const now = new Date();
      return d.getFullYear() === now.getFullYear()
        && d.getMonth() === now.getMonth()
        && d.getDate() === now.getDate();
    }

    async function loadCams() {
      const sel = $("#filter-camera");
      const cams = await j("/api/cameras");
      cams.forEach((c) => {
        const o = document.createElement("option");
        o.value = c.id; o.textContent = c.name; sel.appendChild(o);
      });
    }
    $("#filter-date").value = state.date;

    async function load() {
      const params = new URLSearchParams();
      if (state.class_name) params.set("class_name", state.class_name);
      if (state.section) params.set("section", state.section);
      if (state.date) params.set("date", state.date);
      if (state.camera_id) params.set("camera_id", state.camera_id);
      if (state.search) params.set("q", state.search);
      const rows = await j(`/api/attendance?${params.toString()}`);
      if (countEl) countEl.textContent = `${rows.length} records`;
      if (!rows.length) { tbody.innerHTML = `<tr><td colspan="8" class="empty">No attendance for selected filters.</td></tr>`; return; }
      const q = state.search.toLowerCase();
      const filtered = q
        ? rows.filter((r) => (r.student_name || "").toLowerCase().includes(q) ||
                              (r.roll_no || "").toLowerCase().includes(q) ||
                              String(r.student_id || "").includes(q))
        : rows;
      tbody.innerHTML = filtered.map((r) => `
        <tr data-id="${r.id}">
          <td>${new Date(r.timestamp).toLocaleTimeString()}</td>
          <td>${escapeHtml(r.roll_no || "")}</td>
          <td>${escapeHtml(r.student_name || "")}</td>
          <td>${escapeHtml(r.class_name || "")}</td>
          <td>${escapeHtml(r.section || "")}</td>
          <td>${escapeHtml(r.camera_name || "")}</td>
          <td>${(r.confidence || 0).toFixed(2)}</td>
          <td>
            <button class="btn-secondary" data-action="edit" data-id="${r.id}" title="Edit attendance">
              <i data-lucide="edit"></i>
            </button>
            <button class="btn-danger" data-action="delete" data-id="${r.id}" title="Delete attendance">
              <i data-lucide="trash-2"></i>
            </button>
          </td>
        </tr>`).join("");
      if (window.lucide) lucide.createIcons();
    }

    function exportHref(fmt) {
      const params = new URLSearchParams();
      if (state.class_name) params.set("class_name", state.class_name);
      if (state.section) params.set("section", state.section);
      if (state.date) params.set("date", state.date);
      params.set("format", fmt);
      return `/api/attendance/export?${params.toString()}`;
    }

    $("#filter-class").addEventListener("change", (e) => { state.class_name = e.target.value; load(); });
    $("#filter-section").addEventListener("change", (e) => { state.section = e.target.value; load(); });
    $("#filter-date").addEventListener("change", (e) => { state.date = e.target.value; load(); });
    $("#filter-camera").addEventListener("change", (e) => { state.camera_id = e.target.value; load(); });
    $("#filter-search").addEventListener("input", (e) => { state.search = e.target.value; load(); });
    $("#btn-export-csv").addEventListener("click", () => { window.location.href = exportHref("csv"); });
    $("#btn-export-pdf").addEventListener("click", () => { window.location.href = exportHref("pdf"); });
    $("#btn-print").addEventListener("click", () => window.print());

    tbody.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      const params = new URLSearchParams();
      if (state.class_name) params.set("class_name", state.class_name);
      if (state.section) params.set("section", state.section);
      if (state.date) params.set("date", state.date);
      if (state.camera_id) params.set("camera_id", state.camera_id);
      const rows = await j(`/api/attendance?${params.toString()}`);
      const record = rows.find((r) => String(r.id) === String(id));
      if (!record) return;

      if (action === "delete") {
        const today = isTodayRecord(record.timestamp);
        const msg = today
          ? `Delete attendance for ${record.student_name || record.roll_no}?`
          : `This record is from a previous date. Delete anyway?`;
        if (!confirm(msg)) return;
        try {
          const url = today
            ? `/api/attendance/${id}`
            : `/api/attendance/${id}?force=true`;
          await j(url, { method: "DELETE" });
          showResult({
            title: "Attendance removed",
            ok: true,
            message: `${record.student_name || record.roll_no} attendance deleted.`,
          });
          load();
        } catch (err) {
          showResult({ title: "Delete failed", ok: false, message: err.message });
        }
      } else if (action === "edit") {
        const today = isTodayRecord(record.timestamp);
        openFormModal({
          title: `Edit Attendance — ${record.student_name || record.roll_no}`,
          initial: {
            status: record.status || "present",
            confidence: (record.confidence || 0).toFixed(2),
            timestamp: record.timestamp ? record.timestamp.slice(0, 16) : "",
          },
          fields: [
            { name: "status", label: "Status", type: "select", required: true,
              options: [
                { v: "present", t: "Present" },
                { v: "absent", t: "Absent" },
                { v: "late", t: "Late" },
              ] },
            { name: "confidence", label: "Confidence", type: "number" },
            { name: "timestamp", label: "Timestamp (local)", type: "datetime-local" },
          ],
          onSave: async (v) => {
            const payload = {
              status: v.status,
              confidence: parseFloat(v.confidence || "0"),
            };
            if (v.timestamp) {
              payload.timestamp = new Date(v.timestamp).toISOString();
            }
            if (!today) payload.force = true;
            await j(`/api/attendance/${id}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload),
            });
            showResult({
              title: "Attendance updated",
              ok: true,
              message: `${record.student_name || record.roll_no} attendance corrected.`,
            });
            load();
          },
        });
      }
    });

    loadCams();
    load();
  }

  // ============================================================ TEACHERS
  function initTeachers() {
    const tbody = $("#teachers-table tbody");
    const countEl = $("#teachers-count");
    let search = "";

    async function loadStats() {
      try {
        const stats = await j("/api/enterprise/dashboard");
        set("#stat-teacher-total", stats.total_teachers ?? "--");
        set("#stat-teacher-present", stats.teachers_present ?? "--");
        set("#stat-teacher-absent", stats.teachers_absent ?? "--");
      } catch {
        /* ignore */
      }
    }

    async function load() {
      const rows = await j("/api/teachers");
      loadStats();
      const registeredCount = rows.filter((r) => r.has_encoding || r.encoding != null).length;
      set("#stat-teacher-registered", registeredCount);

      const q = search.toLowerCase();
      const filtered = q
        ? rows.filter((r) =>
            (r.name || "").toLowerCase().includes(q) ||
            (r.teacher_id || "").toLowerCase().includes(q) ||
            (r.subject || "").toLowerCase().includes(q) ||
            (r.department || "").toLowerCase().includes(q) ||
            (r.designation || "").toLowerCase().includes(q))
        : rows;
      if (countEl) countEl.textContent = `${filtered.length} teachers`;
      if (!filtered.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty">No teachers found.</td></tr>`; return;
      }
      tbody.innerHTML = filtered.map((t) => {
        const profileUrl = `/teachers/${t.id}/profile`;
        return `
        <tr>
          <td><strong>${escapeHtml(t.teacher_id)}</strong></td>
          <td>
            <div style="display:flex;align-items:center;gap:10px;">
              ${t.photo_path ? `<img src="/static/${t.photo_path}" style="width:34px;height:34px;border-radius:50%;object-fit:cover;" />` : `<div style="width:34px;height:34px;border-radius:50%;background:var(--primary-subtle,#e0e7ff);display:flex;align-items:center;justify-content:center;font-weight:bold;color:var(--primary);">${escapeHtml((t.name || "T")[0].toUpperCase())}</div>`}
              <div>
                <strong>${escapeHtml(t.name)}</strong>
              </div>
            </div>
          </td>
          <td>${escapeHtml(t.department || "General")} <br/><small class="muted">${escapeHtml(t.designation || "Teacher")}</small></td>
          <td>${escapeHtml(t.subject || "All Subjects")} <br/><small class="muted">Classes: ${escapeHtml(t.assigned_classes || "N/A")}</small></td>
          <td><span class="badge ${t.is_active ? "ok" : "muted"}">${t.is_active ? "ACTIVE" : "INACTIVE"}</span></td>
          <td>
            <a class="btn-secondary small" href="${profileUrl}" style="text-decoration:none;display:inline-flex;align-items:center;gap:6px;" title="View Complete 360° Profile"><i data-lucide="user"></i> Profile</a>
          </td>
        </tr>`;
      }).join("");
      if (window.lucide) lucide.createIcons();
    }

    let timer;
    const searchEl = $("#filter-search");
    if (searchEl) {
      searchEl.addEventListener("input", (e) => {
        clearTimeout(timer);
        timer = setTimeout(() => { search = e.target.value; load(); }, 200);
      });
    }

    const addBtn = $("#btn-add-teacher");
    if (addBtn) {
      addBtn.addEventListener("click", () => {
        openFormModal({
          title: "Add Teacher",
          fields: [
            { name: "teacher_id", label: "Teacher ID", required: true },
            { name: "name", label: "Name", required: true },
            { name: "attendance_method", label: "Attendance Identification Method", type: "select", options: [{v:"FACE",t:"Face Recognition (CCTV/Webcam)"},{v:"FINGERPRINT",t:"BioMax Indigo Fingerprint Device"}] },
            { name: "fingerprint_user_id", label: "BioMax Device User ID (optional, defaults to Teacher ID)" },
            { name: "department", label: "Department (e.g. Science, Mathematics)" },
            { name: "designation", label: "Designation (e.g. Senior Teacher, HOD)" },
            { name: "subject", label: "Subject" },
            { name: "assigned_classes", label: "Assigned Classes (e.g. 5A, 6B)" },
            { name: "mobile", label: "Mobile" },
            { name: "email", label: "Email", type: "email" },
          ],
          onSave: async (v) => {
            await j("/api/teachers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });
            showResult({ title: "Teacher added", ok: true, message: `${v.name} added.` });
            load();
          },
        });
      });
    }

    async function showTeacherProfile(tid, tname) {
      const modal = $("#teacher-profile-modal");
      const title = $("#tp-modal-title");
      const body = $("#tp-modal-body");
      const regBtn = $("#tp-btn-register");
      const fpBtn = $("#tp-btn-fingerprint");

      if (title) title.textContent = `Teacher Profile \u2014 ${tname}`;
      if (body) body.innerHTML = `<div class="empty">Loading teacher profile...</div>`;
      openModal("#teacher-profile-modal");

      try {
        const prof = await j(`/api/teachers/${tid}/profile`);
        const btn360 = $("#tp-btn-360");
        if (btn360) {
          btn360.href = `/hr/employees/${prof.linked_employee_id || prof.id}`;
        }
        if (regBtn) {
          regBtn.onclick = () => {
            closeModal("#teacher-profile-modal");
            openUploadModal(prof.id, prof.name, "teacher");
          };
        }
        if (fpBtn) {
          fpBtn.onclick = async () => {
            try {
              const res = await j(`/api/teachers/${prof.id}/fingerprint/enroll`, { method: "POST" });
              showResult({ title: "BioMax Indigo Enrollment", ok: true, message: res.message || "Fingerprint enrolled." });
              showTeacherProfile(tid, tname);
              load();
            } catch (err) {
              showResult({ title: "Enrollment Failed", ok: false, message: err.message || "Could not enroll on BioMax Indigo device." });
            }
          };
        }
        const m = prof.month || {};
        const last = prof.last_recognition;
        const lastRecText = last
          ? `${last.event_type.toUpperCase()} at ${new Date(last.event_time).toLocaleTimeString()} (${Math.round((last.confidence || 0) * 100)}% conf on ${last.camera || 'Device'})`
          : "No recognition recorded yet";

        const method = (prof.attendance_method || "FACE").toUpperCase();

        if (body) {
          body.innerHTML = `
            <div style="display:grid;grid-template-columns:120px 1fr;gap:20px;margin-bottom:20px;align-items:center;">
              <div style="text-align:center;">
                ${prof.photo_path ? `<img src="/static/${prof.photo_path}" style="width:100px;height:100px;border-radius:12px;object-fit:cover;border:2px solid var(--border);" />` : `<div style="width:100px;height:100px;border-radius:12px;background:var(--primary-subtle,#e0e7ff);display:flex;align-items:center;justify-content:center;font-size:2rem;font-weight:bold;color:var(--primary);">${escapeHtml((prof.name || "T")[0].toUpperCase())}</div>`}
                <div style="margin-top:8px;">
                  <span class="badge ${method === 'FACE' ? (prof.face_registered ? 'ok' : 'warn') : (prof.fingerprint_enrolled ? 'ok' : 'warn')}">
                    ${method === 'FACE' ? (prof.face_registered ? 'Face Ready' : 'No Face') : (prof.fingerprint_enrolled ? 'FP Enrolled' : 'FP Not Enrolled')}
                  </span>
                </div>
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                <div><span class="muted small">Teacher ID:</span> <br/><strong>${escapeHtml(prof.teacher_id)}</strong></div>
                <div><span class="muted small">Attendance Method:</span> <br/><strong><span class="badge" style="background:#e0e7ff;color:#3730a3;">${escapeHtml(method)}</span></strong></div>
                <div><span class="muted small">Department:</span> <br/><strong>${escapeHtml(prof.department || "General")}</strong></div>
                <div><span class="muted small">Designation:</span> <br/><strong>${escapeHtml(prof.designation || "Teacher")}</strong></div>
                <div><span class="muted small">Subject:</span> <br/><strong>${escapeHtml(prof.subject || "All Subjects")}</strong></div>
                <div><span class="muted small">Contact:</span> <br/><strong>${escapeHtml(prof.mobile || "-")} / ${escapeHtml(prof.email || "-")}</strong></div>
              </div>
            </div>

            <hr style="border:none;border-top:1px solid var(--border);margin:16px 0;" />

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
              <div style="background:var(--surface,#f8fafc);padding:12px;border-radius:8px;">
                <h4 style="margin-bottom:8px;font-size:0.95rem;"><i data-lucide="scan-face"></i> Face Registration</h4>
                <div class="small">
                  <div>Status: <strong>${prof.face_registered ? "Registered & Active" : "Not Registered"}</strong></div>
                  <div>Encodings: <strong>${prof.encodings_count || 0} angles (${(prof.angles || []).join(", ") || "None"})</strong></div>
                </div>
              </div>
              <div style="background:var(--surface,#f8fafc);padding:12px;border-radius:8px;">
                <h4 style="margin-bottom:8px;font-size:0.95rem;"><i data-lucide="fingerprint"></i> BioMax Indigo Fingerprint</h4>
                <div class="small">
                  <div>Status: <strong>${prof.fingerprint_enrolled ? "Enrolled on Device" : "Not Enrolled"}</strong></div>
                  <div>Device User ID: <strong>${escapeHtml(prof.fingerprint_user_id || prof.teacher_id)}</strong></div>
                  <div>Enrolled At: <strong>${prof.fingerprint_enrolled_at ? new Date(prof.fingerprint_enrolled_at).toLocaleString() : "Never"}</strong></div>
                </div>
              </div>
            </div>

            <h4 style="margin-bottom:12px;"><i data-lucide="calendar"></i> Monthly Attendance Summary</h4>
            <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:10px;">
              <div class="stat-card glass" style="padding:10px;text-align:center;">
                <div class="stat-label">Days Present</div>
                <div class="stat-value" style="font-size:1.4rem;">${m.days_present ?? 0}</div>
              </div>
              <div class="stat-card glass" style="padding:10px;text-align:center;">
                <div class="stat-label">Working Hours</div>
                <div class="stat-value" style="font-size:1.4rem;">${Math.floor((m.working_minutes || 0) / 60)}h ${(m.working_minutes || 0) % 60}m</div>
              </div>
              <div class="stat-card glass" style="padding:10px;text-align:center;">
                <div class="stat-label">Overtime</div>
                <div class="stat-value" style="font-size:1.4rem;">${Math.floor((m.overtime_minutes || 0) / 60)}h ${(m.overtime_minutes || 0) % 60}m</div>
              </div>
              <div class="stat-card glass" style="padding:10px;text-align:center;">
                <div class="stat-label">Attendance %</div>
                <div class="stat-value" style="font-size:1.4rem;color:var(--success);">${m.attendance_pct ?? 0}%</div>
              </div>
            </div>
          `;
          if (window.lucide) lucide.createIcons();
        }
      } catch (err) {
        if (body) body.innerHTML = `<div class="empty err">Could not load profile: ${escapeHtml(err.message || err)}</div>`;
      }
    }

    tbody.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const id = btn.dataset.id;
      const name = btn.dataset.name || "Teacher";
      const action = btn.dataset.action;

      if (action === "upload") {
        openUploadModal(id, name, "teacher");
      } else if (action === "fingerprint") {
        try {
          const res = await j(`/api/teachers/${id}/fingerprint/enroll`, { method: "POST" });
          showResult({ title: "BioMax Fingerprint", ok: true, message: res.message || `${name} fingerprint registered on BioMax device.` });
          load();
        } catch (err) {
          showResult({ title: "Enrollment Failed", ok: false, message: err.message || "Failed to communicate with BioMax Indigo device." });
        }
      } else if (action === "profile") {
        window.location.href = `/teachers/${id}/profile`;
      } else if (action === "delete") {
        if (!confirm(`Delete teacher ${name}?`)) return;
        await j(`/api/teachers/${id}`, { method: "DELETE" });
        load();
      } else if (action === "edit") {
        const t = (await j("/api/teachers")).find((r) => r.id == id);
        if (!t) return;
        openFormModal({
          title: `Edit ${t.name}`,
          initial: t,
          fields: [
            { name: "teacher_id", label: "Teacher ID", required: true },
            { name: "name", label: "Name", required: true },
            { name: "attendance_method", label: "Attendance Identification Method", type: "select", options: [{v:"FACE",t:"Face Recognition (CCTV/Webcam)"},{v:"FINGERPRINT",t:"BioMax Indigo Fingerprint Device"}] },
            { name: "fingerprint_user_id", label: "BioMax Device User ID" },
            { name: "fingerprint_enrolled", label: "Fingerprint Enrolled on Device", type: "select", options: [{v:"true",t:"Enrolled"},{v:"false",t:"Not Enrolled"}] },
            { name: "department", label: "Department" },
            { name: "designation", label: "Designation" },
            { name: "subject", label: "Subject" },
            { name: "assigned_classes", label: "Assigned Classes" },
            { name: "mobile", label: "Mobile" },
            { name: "email", label: "Email", type: "email" },
            { name: "is_active", label: "Status", type: "select", options: [{v:"true",t:"Active"},{v:"false",t:"Inactive"}] },
          ],
          onSave: async (v) => {
            v.is_active = String(v.is_active).toLowerCase() === "true";
            v.fingerprint_enrolled = String(v.fingerprint_enrolled).toLowerCase() === "true";
            await j(`/api/teachers/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });
            showResult({ title: "Teacher updated", ok: true, message: `${v.name} saved.` });
            load();
          },
        });
      }
    });

    window.addEventListener("face-registered", (e) => {
      if (e.detail && (e.detail.personType === "teacher" || e.detail.personId)) {
        load();
      }
    });

    load();
  }

  // ============================================================ CAMERAS
  function initCameras() {
    const grid = $("#cam-grid");

    async function load() {
      const cams = await j("/api/cameras");
      if (!cams.length) { grid.innerHTML = `<div class="empty">No cameras yet. Add one to start.</div>`; return; }
      grid.innerHTML = cams.map((c) => `
        <div class="cam-card" data-id="${c.id}">
          <div class="cam-head">
            <div>
              <div class="cam-name">${escapeHtml(c.name)}</div>
              <div class="cam-loc">${escapeHtml(c.location || "")}</div>
            </div>
            <span class="badge ${c.is_active ? "ok" : "muted"}">${c.is_active ? "Active" : "Stopped"}</span>
          </div>
          <img class="cam-stream" src="/stream/${c.id}" alt="${escapeHtml(c.name)}" />
          <div class="cam-meta">
            ${cameraMetaHtml(c)}
          </div>
          <div class="cam-foot">
            <button class="${c.is_active ? "btn-secondary" : "btn-primary"}" data-action="toggle" data-id="${c.id}">
              <i data-lucide="${c.is_active ? "square" : "play"}"></i> ${c.is_active ? "Stop" : "Start"}
            </button>
            <button class="btn-secondary" data-action="test" data-id="${c.id}"><i data-lucide="wifi"></i> Test</button>
            <button class="btn-secondary" data-action="edit" data-id="${c.id}"><i data-lucide="edit"></i> Edit</button>
            <button class="btn-danger" data-action="delete" data-id="${c.id}"><i data-lucide="trash-2"></i></button>
          </div>
        </div>`).join("");
      if (window.lucide) lucide.createIcons();
      refreshCameraStatus(grid);
    }

    $("#btn-add-camera").addEventListener("click", () => {
      openFormModal({
        title: "Add Camera",
        fields: [
          { name: "name", label: "Camera Name", required: true },
          { name: "source", label: "RTSP URL or 0 (webcam)", required: true },
          { name: "location", label: "Location" },
        ],
        onSave: async (v) => {
          await j("/api/cameras", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });
          showResult({ title: "Camera added", ok: true, message: `${v.name} added.` });
          load();
        },
      });
    });

    grid.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const id = btn.dataset.id;
      if (btn.dataset.action === "toggle") {
        const cams = await j("/api/cameras");
        const cam = cams.find((r) => r.id == id);
        const action = cam && cam.is_active ? "stop" : "start";
        await j(`/api/cameras/${id}/${action}`, { method: "POST" });
        load();
      } else if (btn.dataset.action === "test") {
        const r = await j(`/api/cameras/${id}/test`, { method: "POST" });
        showResult({ title: r.ok ? "Reachable" : "Unreachable", ok: r.ok, message: r.message || r.source || "" });
      } else if (btn.dataset.action === "delete") {
        const cams = await j("/api/cameras");
        const cam = cams.find((r) => r.id == id);
        const msg = cam && cam.is_active
          ? "This camera is currently active. It will be stopped and deleted. Proceed?"
          : "Delete this camera?";
        if (!confirm(msg)) return;
        try {
          await j(`/api/cameras/${id}`, { method: "DELETE" });
          showResult({ title: "Camera deleted", ok: true, message: "Camera removed." });
          load();
        } catch (err) {
          showResult({ title: "Delete failed", ok: false, message: err.message });
        }
      } else if (btn.dataset.action === "edit") {
        const cams = await j("/api/cameras");
        const c = cams.find((r) => r.id == id);
        if (!c) return;
        openFormModal({
          title: `Edit ${c.name}`,
          initial: c,
          fields: [
            { name: "name", label: "Camera Name", required: true },
            { name: "source", label: "RTSP URL or 0", required: true },
            { name: "location", label: "Location" },
            { name: "is_active", label: "Status", type: "select", options: [{v:"true",t:"Active"},{v:"false",t:"Stopped"}] },
          ],
          onSave: async (v) => {
            v.is_active = String(v.is_active).toLowerCase() === "true";
            await j(`/api/cameras/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(v) });
            showResult({ title: "Camera updated", ok: true, message: `${v.name} saved.` });
            load();
          },
        });
      }
    });

    load();
    setInterval(() => {
      if (!document.hidden) refreshCameraStatus(grid);
    }, 12000);
  }

  // ============================================================ SETTINGS
  function initSettings() {
    const t = $("#set-threshold");
    const tv = $("#set-threshold-value");
    if (t && tv) {
      t.addEventListener("input", () => { tv.textContent = t.value; });
    }
    // Populate school details
    j("/api/settings/school").then((data) => {
      const sch = data.school || data;
      if (sch) {
        const idEl = $("#set-school-id");
        const nameEl = $("#set-school-name");
        const emailEl = $("#set-school-email");
        const phoneEl = $("#set-school-phone");
        if (idEl && sch.school_id) idEl.value = sch.school_id;
        if (nameEl && sch.name) nameEl.value = sch.name;
        if (emailEl && sch.contact_email) emailEl.value = sch.contact_email;
        if (phoneEl && sch.contact_phone) phoneEl.value = sch.contact_phone;
      }
    }).catch(() => {});

    const btnSaveSchool = $("#btn-save-school");
    if (btnSaveSchool) {
      btnSaveSchool.addEventListener("click", async () => {
        const name = $("#set-school-name")?.value.trim();
        const contact_email = $("#set-school-email")?.value.trim();
        const contact_phone = $("#set-school-phone")?.value.trim();
        if (!name) {
          showResult({ title: "Validation Error", ok: false, message: "School name is required." });
          return;
        }
        try {
          const res = await j("/api/settings/school", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, contact_email, contact_phone }),
          });
          if (res.ok || res.school) {
            showResult({ title: "School Profile Saved", ok: true, message: "Institution branding updated successfully." });
            const brandTitle = document.querySelector(".brand-title");
            if (brandTitle) brandTitle.textContent = name;
          } else {
            showResult({ title: "Error", ok: false, message: res.error || "Failed to update school settings." });
          }
        } catch (err) {
          showResult({ title: "Save Failed", ok: false, message: err.message || "Network error" });
        }
      });
    }

    try {
      $("#sys-cv").textContent = "opencv-python";
      $("#sys-flask").textContent = "Flask";
      $("#sys-if").textContent = "insightface";
    } catch { /* noop */ }
  }

  // ============================================================ BOOTSTRAP
  document.addEventListener("DOMContentLoaded", async () => {
    // pre-load class/section lists for forms
    try {
      const meta = await j("/api/meta");
      window.__SCHOOL__ = { classes: meta.classes, sections: meta.sections };
    } catch { window.__SCHOOL__ = { classes: [], sections: [] }; }

    switch (activePage()) {
      case "dashboard":  initDashboard(); break;
      case "students":   initStudents(); break;
      case "attendance": initAttendance(); break;
      case "attendance_enterprise": break;
      case "teachers":   initTeachers(); break;
      case "cameras":    initCameras(); break;
      case "settings":   initSettings(); break;
      default: /* noop */ break;
    }
  });
})();
