/**
 * AttendanceOS — Teacher Portal Frontend Controller
 * Mobile-First, Real-Time Hydration, Tab Management & Self-Service Workflows
 */

(function () {
  'use strict';

  // State
  let currentDashboardData = null;
  let currentProfileData = null;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  // Helpers
  function getTodayMonthString() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    return `${y}-${m}`;
  }

  function formatTime(isoStr) {
    if (!isoStr) return '--:--';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
    } catch {
      return isoStr;
    }
  }

  function formatDate(isoStr) {
    if (!isoStr) return '--';
    try {
      const d = new Date(isoStr + 'T00:00:00');
      return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
      return isoStr;
    }
  }

  function getStatusBadge(status) {
    const st = (status || 'present').toLowerCase().replace(' ', '_');
    let cls = 'badge-present';
    let label = status || 'Present';
    if (st.includes('late')) { cls = 'badge-late'; label = 'Late'; }
    else if (st.includes('absent')) { cls = 'badge-absent'; label = 'Absent'; }
    else if (st.includes('leave')) { cls = 'badge-leave'; label = 'On Leave'; }
    else if (st.includes('holiday')) { cls = 'badge-holiday'; label = 'Holiday'; }
    else if (st.includes('weekly') || st.includes('off')) { cls = 'badge-weekly_off'; label = 'Weekly Off'; }
    else if (st.includes('half')) { cls = 'badge-half_day'; label = 'Half Day'; }
    else if (st.includes('not') || st.includes('pending')) { cls = 'badge-pending'; label = 'Pending / Not Marked'; }
    return `<span class="status-pill ${cls}">${label}</span>`;
  }

  function getMethodBadge(method) {
    const m = (method || 'Face Recognition').toLowerCase();
    let icon = 'scan-face';
    let text = 'Face';
    if (m.includes('finger') || m.includes('biomax')) {
      icon = 'fingerprint';
      text = 'Fingerprint';
    } else if (m.includes('manual')) {
      icon = 'edit-3';
      text = 'Manual';
    }
    return `<span class="method-pill"><i data-lucide="${icon}"></i> ${text}</span>`;
  }

  // Toast notification
  function showToast(message, type = 'info') {
    const existing = document.getElementById('portal-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'portal-toast';
    toast.style.cssText = `
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 9999;
      padding: 12px 20px;
      border-radius: 12px;
      color: white;
      font-size: 14px;
      font-weight: 600;
      box-shadow: 0 10px 30px rgba(0,0,0,0.15);
      background: ${type === 'success' ? '#34C759' : type === 'error' ? '#FF3B30' : '#007AFF'};
      transition: all 0.3s cubic-bezier(0.25, 1, 0.5, 1);
      transform: translateY(20px);
      opacity: 0;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);

    requestAnimationFrame(() => {
      toast.style.transform = 'translateY(0)';
      toast.style.opacity = '1';
    });

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(20px)';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  // Tab Navigation Controller
  function initTabs() {
    const root = document.getElementById('teacher-portal-root');
    const initialTab = root?.getAttribute('data-active-tab') || 'dashboard';
    const tabBtns = document.querySelectorAll('.portal-tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    const crumbTitle = document.getElementById('portal-crumb-title');

    function switchTab(tabId) {
      tabBtns.forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
      });
      tabContents.forEach(content => {
        content.classList.toggle('active', content.id === `tab-${tabId}`);
      });

      if (crumbTitle) {
        const titleMap = {
          'dashboard': 'Dashboard',
          'attendance': 'Attendance History',
          'summary': 'Monthly Summary',
          'leave': 'Leave Management',
          'corrections': 'Attendance Corrections',
          'profile': 'My Profile'
        };
        crumbTitle.textContent = titleMap[tabId] || 'Dashboard';
      }

      // Load specific tab data on demand
      if (tabId === 'attendance') loadAttendanceHistory();
      if (tabId === 'summary') loadMonthlySummary();
      if (tabId === 'leave') loadLeaves();
      if (tabId === 'corrections') loadCorrections();
      if (tabId === 'profile') loadProfile();

      // Refresh icons
      if (window.lucide) window.lucide.createIcons();
    }

    tabBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        switchTab(tab);
        window.history.replaceState(null, '', `/portal/${tab === 'dashboard' ? '' : tab}`);
      });
    });

    // Quick action buttons in hero
    document.getElementById('btn-quick-leave')?.addEventListener('click', () => {
      switchTab('leave');
    });
    document.getElementById('btn-quick-correct')?.addEventListener('click', () => {
      switchTab('corrections');
    });
    document.getElementById('btn-view-all-history')?.addEventListener('click', () => {
      switchTab('attendance');
    });
    document.getElementById('btn-apply-history-correction')?.addEventListener('click', () => {
      switchTab('corrections');
    });

    // Initialize with active tab
    switchTab(initialTab);
  }

  // Fetch Dashboard Data
  async function loadDashboard() {
    try {
      const res = await fetch('/api/teacher/dashboard', { headers: { 'Accept': 'application/json' } });
      const json = await res.json();
      if (!json.success || !json.data) return;

      const data = json.data;
      currentDashboardData = data;

      // Hydrate Hero
      const tInfo = data.teacher_info || {};
      const tNameEl = document.getElementById('t-name');
      if (tNameEl) tNameEl.textContent = tInfo.name || 'Teacher';

      const tAvatarEl = document.getElementById('t-avatar');
      if (tAvatarEl) tAvatarEl.textContent = (tInfo.name || 'T').charAt(0).toUpperCase();

      const tIdBadge = document.getElementById('t-id-badge');
      if (tIdBadge) tIdBadge.textContent = `Teacher ID: ${tInfo.teacher_id || '--'}`;

      const tDeptDesig = document.getElementById('t-dept-desig');
      if (tDeptDesig) tDeptDesig.textContent = `${tInfo.department || 'Academic'} • ${tInfo.designation || 'Teacher'}`;

      // Today's Status
      const today = data.today || {};
      const todayDateEl = document.getElementById('today-full-date');
      if (todayDateEl) todayDateEl.textContent = formatDate(today.date);

      const statusBadgeEl = document.getElementById('today-status-badge');
      if (statusBadgeEl) {
        statusBadgeEl.innerHTML = getStatusBadge(today.status);
      }

      const inTimeEl = document.getElementById('today-in-time');
      if (inTimeEl) inTimeEl.textContent = formatTime(today.in_time);

      const outTimeEl = document.getElementById('today-out-time');
      if (outTimeEl) outTimeEl.textContent = formatTime(today.out_time);

      const inCamEl = document.getElementById('today-in-camera');
      if (inCamEl) inCamEl.textContent = today.camera || 'Main Gate Camera';

      const hoursEl = document.getElementById('today-work-hours');
      if (hoursEl) hoursEl.textContent = today.working_hours || '0h 00m';

      const methodTextEl = document.getElementById('today-method-text');
      if (methodTextEl) methodTextEl.textContent = today.method || 'Face Recognition';

      const lateChip = document.getElementById('today-late-chip');
      if (lateChip) {
        if (today.is_late && today.late_minutes > 0) {
          lateChip.style.display = 'inline-flex';
          lateChip.textContent = `Late: ${today.late_minutes}m`;
        } else {
          lateChip.style.display = 'none';
        }
      }

      // Month stats
      const stats = data.month_stats || {};
      const monthRateEl = document.getElementById('dash-month-rate');
      if (monthRateEl) monthRateEl.textContent = `${stats.attendance_percentage || 0}%`;

      const statRate = document.getElementById('stat-month-rate');
      if (statRate) statRate.textContent = `${stats.attendance_percentage || 0}%`;

      const statPres = document.getElementById('stat-present-days');
      if (statPres) statPres.textContent = stats.present_days ?? 0;

      const statLate = document.getElementById('stat-late-count');
      if (statLate) statLate.textContent = stats.late_days ?? 0;

      const statLeaves = document.getElementById('stat-leaves-taken');
      if (statLeaves) statLeaves.textContent = stats.leave_days ?? 0;

      // Pending counters in tabs
      const pendingLeaveEl = document.getElementById('tab-leaves-count');
      if (pendingLeaveEl) pendingLeaveEl.textContent = data.pending_leaves_count ?? 0;

      const pendingCorrEl = document.getElementById('tab-corrections-count');
      if (pendingCorrEl) pendingCorrEl.textContent = data.pending_corrections_count ?? 0;

      // Recent 7 days table
      const recentTbody = document.getElementById('dash-recent-tbody');
      if (recentTbody) {
        const recent = data.recent_7_days || [];
        if (recent.length === 0) {
          recentTbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-tertiary);">No recent attendance entries.</td></tr>';
        } else {
          recentTbody.innerHTML = recent.map(r => `
            <tr>
              <td><strong>${formatDate(r.date)}</strong></td>
              <td>${formatTime(r.in_time)}</td>
              <td>${formatTime(r.out_time)}</td>
              <td>${r.working_hours || '--'}</td>
              <td>${getStatusBadge(r.status)}</td>
              <td>${getMethodBadge(r.method)}</td>
            </tr>
          `).join('');
        }
      }

      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error('Failed to load teacher dashboard:', err);
    }
  }

  // Load Attendance History
  async function loadAttendanceHistory(monthStr = null) {
    const monthPicker = document.getElementById('hist-month-picker');
    const targetMonth = monthStr || monthPicker?.value || getTodayMonthString();
    if (monthPicker && !monthPicker.value) monthPicker.value = targetMonth;

    const tbody = document.getElementById('history-tbody');
    const mobileWrap = document.getElementById('mobile-history-cards');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:32px; color:var(--text-tertiary);">Loading records...</td></tr>';

    try {
      const res = await fetch(`/api/teacher/attendance-history?month=${targetMonth}`, {
        headers: { 'Accept': 'application/json' }
      });
      const json = await res.json();
      if (!json.success || !json.data) return;

      const records = json.data.records || [];

      if (records.length === 0) {
        const emptyHtml = '<tr><td colspan="8" style="text-align:center; padding:32px; color:var(--text-tertiary);">No attendance records found for this month.</td></tr>';
        if (tbody) tbody.innerHTML = emptyHtml;
        if (mobileWrap) mobileWrap.innerHTML = '<div class="empty-state"><p>No attendance records found for this month.</p></div>';
        return;
      }

      // Hydrate Table
      if (tbody) {
        tbody.innerHTML = records.map(r => `
          <tr>
            <td><strong>${formatDate(r.date)}</strong></td>
            <td>${formatTime(r.in_time)}</td>
            <td>${formatTime(r.out_time)}</td>
            <td>${getStatusBadge(r.status)}</td>
            <td>${r.late_minutes > 0 ? `<span class="status-pill badge-late">${r.late_minutes}m</span>` : '<span style="color:var(--text-tertiary);">--</span>'}</td>
            <td><strong>${r.working_hours || '--'}</strong></td>
            <td>${getMethodBadge(r.method)}</td>
            <td>
              <button class="btn-p-secondary btn-p-sm btn-row-correct" data-date="${r.date}" type="button">
                <i data-lucide="edit-3" style="width:12px;"></i> Fix
              </button>
            </td>
          </tr>
        `).join('');
      }

      // Hydrate Mobile Cards
      if (mobileWrap) {
        mobileWrap.innerHTML = records.map(r => `
          <div class="m-hist-card">
            <div class="m-hist-head">
              <strong>${formatDate(r.date)}</strong>
              ${getStatusBadge(r.status)}
            </div>
            <div class="m-hist-grid">
              <div class="item">
                <span class="item-label">IN Time</span>
                <span class="item-val">${formatTime(r.in_time)}</span>
              </div>
              <div class="item">
                <span class="item-label">OUT Time</span>
                <span class="item-val">${formatTime(r.out_time)}</span>
              </div>
              <div class="item">
                <span class="item-label">Duration</span>
                <span class="item-val">${r.working_hours || '--'}</span>
              </div>
              <div class="item">
                <span class="item-label">Punch Method</span>
                <span class="item-val">${r.method || 'Face'}</span>
              </div>
            </div>
            <div style="display:flex; justify-content:flex-end; margin-top:4px;">
              <button class="btn-p-secondary btn-p-sm btn-row-correct" data-date="${r.date}" type="button">
                <i data-lucide="clock-alert" style="width:12px;"></i> Request Fix
              </button>
            </div>
          </div>
        `).join('');
      }

      // Attach row correction handlers
      document.querySelectorAll('.btn-row-correct').forEach(btn => {
        btn.addEventListener('click', () => {
          const dateStr = btn.getAttribute('data-date');
          const corrDateInput = document.getElementById('corr-date');
          if (corrDateInput) corrDateInput.value = dateStr;
          const root = document.getElementById('teacher-portal-root');
          const corrTabBtn = document.querySelector('.portal-tab-btn[data-tab="corrections"]');
          if (corrTabBtn) corrTabBtn.click();
        });
      });

      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error('Failed to load history:', err);
    }
  }

  // Load Monthly Summary
  async function loadMonthlySummary(monthStr = null) {
    const monthPicker = document.getElementById('summary-month-picker');
    const targetMonth = monthStr || monthPicker?.value || getTodayMonthString();
    if (monthPicker && !monthPicker.value) monthPicker.value = targetMonth;

    try {
      const res = await fetch(`/api/teacher/monthly-summary?month=${targetMonth}`, {
        headers: { 'Accept': 'application/json' }
      });
      const json = await res.json();
      if (!json.success || !json.data) return;

      const sum = json.data;

      const titleEl = document.getElementById('summary-month-title');
      if (titleEl) titleEl.textContent = `Summary for ${targetMonth}`;

      const pctLarge = document.getElementById('sum-pct-large');
      if (pctLarge) pctLarge.textContent = `${sum.attendance_percentage}%`;

      const ratioEl = document.getElementById('sum-present-ratio');
      if (ratioEl) ratioEl.textContent = `${sum.present_days} of ${sum.working_days} Working Days`;

      // Progress bar segments
      const totalDays = sum.working_days || 1;
      const progPres = document.getElementById('prog-present');
      if (progPres) progPres.style.width = `${((sum.present_days - sum.late_days) / totalDays) * 100}%`;

      const progLate = document.getElementById('prog-late');
      if (progLate) progLate.style.width = `${(sum.late_days / totalDays) * 100}%`;

      const progLeave = document.getElementById('prog-leave');
      if (progLeave) progLeave.style.width = `${(sum.leave_days / totalDays) * 100}%`;

      const progAbs = document.getElementById('prog-absent');
      if (progAbs) progAbs.style.width = `${(sum.absent_days / totalDays) * 100}%`;

      // Stat cards
      document.getElementById('sum-working-days').textContent = sum.working_days;
      document.getElementById('sum-present-days').textContent = sum.present_days;
      document.getElementById('sum-late-days').textContent = sum.late_days;
      document.getElementById('sum-leave-days').textContent = sum.leave_days;
      document.getElementById('sum-absent-days').textContent = sum.absent_days;
      document.getElementById('sum-half-days').textContent = sum.half_days;
      document.getElementById('sum-total-hours').textContent = sum.total_hours_worked || '0h';
      document.getElementById('sum-avg-hours').textContent = sum.average_daily_hours || '0h';
    } catch (err) {
      console.error('Failed to load monthly summary:', err);
    }
  }

  // Load Leaves
  async function loadLeaves() {
    try {
      const res = await fetch('/api/teacher/leaves', { headers: { 'Accept': 'application/json' } });
      const json = await res.json();
      if (!json.success || !json.data) return;

      const data = json.data;
      const bal = data.leave_balances || {};

      const balCl = document.getElementById('bal-cl');
      if (balCl) balCl.textContent = `${bal.casual_leave_remaining ?? '--'} / ${bal.casual_leave_total ?? '--'}`;

      const balSl = document.getElementById('bal-sl');
      if (balSl) balSl.textContent = `${bal.sick_leave_remaining ?? '--'} / ${bal.sick_leave_total ?? '--'}`;

      const balEl = document.getElementById('bal-el');
      if (balEl) balEl.textContent = `${bal.earned_leave_remaining ?? '--'} / ${bal.earned_leave_total ?? '--'}`;

      const balPend = document.getElementById('bal-pending');
      if (balPend) balPend.textContent = data.pending_count ?? 0;

      const tbody = document.getElementById('leaves-tbody');
      const leaves = data.leave_requests || [];

      if (tbody) {
        if (leaves.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:24px; color:var(--text-tertiary);">No leave applications found.</td></tr>';
        } else {
          tbody.innerHTML = leaves.map(l => `
            <tr>
              <td><strong>${formatDate(l.start_date)}</strong> to <strong>${formatDate(l.end_date)}</strong></td>
              <td><span class="method-pill">${l.leave_type}</span></td>
              <td>${l.days_count} ${l.is_half_day ? '(Half Day)' : ''}</td>
              <td>${l.reason || '--'}</td>
              <td><span class="status-pill badge-${(l.status || 'pending').toLowerCase()}">${l.status}</span></td>
              <td>${formatDate(l.applied_on?.split('T')[0] || l.applied_on)}</td>
              <td>${l.admin_remarks || '<span style="color:var(--text-tertiary);">--</span>'}</td>
            </tr>
          `).join('');
        }
      }

      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error('Failed to load leaves:', err);
    }
  }

  // Load Corrections
  async function loadCorrections() {
    try {
      const res = await fetch('/api/teacher/corrections', { headers: { 'Accept': 'application/json' } });
      const json = await res.json();
      if (!json.success || !json.data) return;

      const corrections = json.data.corrections || [];
      const tbody = document.getElementById('corrections-tbody');

      if (tbody) {
        if (corrections.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:24px; color:var(--text-tertiary);">No correction requests submitted.</td></tr>';
        } else {
          tbody.innerHTML = corrections.map(c => `
            <tr>
              <td><strong>${formatDate(c.attendance_date)}</strong></td>
              <td><span class="method-pill">${c.issue_type || 'Missing Punch'}</span></td>
              <td>${c.requested_time || '--'}</td>
              <td>
                <div style="font-weight:600;">${c.reason}</div>
                <div style="font-size:12px; color:var(--text-secondary);">${c.note || ''}</div>
              </td>
              <td><span class="status-pill badge-${(c.status || 'pending').toLowerCase()}">${c.status}</span></td>
              <td>${formatDate(c.created_at?.split('T')[0] || c.created_at)}</td>
              <td>${c.admin_comment || '<span style="color:var(--text-tertiary);">Pending review</span>'}</td>
            </tr>
          `).join('');
        }
      }

      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error('Failed to load corrections:', err);
    }
  }

  // Load Profile
  async function loadProfile() {
    try {
      const res = await fetch('/api/teacher/profile', { headers: { 'Accept': 'application/json' } });
      const json = await res.json();
      if (!json.success || !json.data) return;

      const p = json.data;
      currentProfileData = p;

      document.getElementById('prof-name').textContent = p.name || '--';
      document.getElementById('prof-avatar').textContent = (p.name || 'T').charAt(0).toUpperCase();
      document.getElementById('prof-designation').textContent = `${p.designation || 'Teacher'} • ${p.department || 'Academic'}`;
      document.getElementById('prof-id').textContent = p.teacher_id || '--';
      document.getElementById('prof-email').textContent = p.email || '--';
      document.getElementById('prof-phone').textContent = p.phone || '--';
      document.getElementById('prof-dept').textContent = p.department || '--';
      document.getElementById('prof-role').textContent = p.designation || '--';
      document.getElementById('prof-joining').textContent = p.joining_date ? formatDate(p.joining_date) : '--';

      const classesWrap = document.getElementById('prof-classes-wrap');
      if (classesWrap) {
        const classes = p.classes_handled || [];
        const subjects = p.subjects_handled || [];
        const items = [...classes, ...subjects];
        if (items.length === 0) {
          classesWrap.innerHTML = '<span class="method-pill">All Standard Assigned Classes</span>';
        } else {
          classesWrap.innerHTML = items.map(c => `<span class="method-pill">${c}</span>`).join('');
        }
      }

      const faceBadge = document.getElementById('prof-face-status');
      if (faceBadge) {
        faceBadge.className = `status-pill ${p.face_enrolled ? 'badge-present' : 'badge-absent'}`;
        faceBadge.innerHTML = `<i data-lucide="scan-face"></i> ${p.face_enrolled ? 'Face Enrolled' : 'Face Not Enrolled'}`;
      }

      const fpBadge = document.getElementById('prof-fp-status');
      if (fpBadge) {
        fpBadge.className = `status-pill ${p.fingerprint_registered ? 'badge-present' : 'badge-absent'}`;
        fpBadge.innerHTML = `<i data-lucide="fingerprint"></i> ${p.fingerprint_registered ? 'Fingerprint Registered' : 'Fingerprint Not Registered'}`;
      }

      if (window.lucide) window.lucide.createIcons();
    } catch (err) {
      console.error('Failed to load profile:', err);
    }
  }

  // Handle Form Submissions
  function initForms() {
    // Apply Leave Form
    const formLeave = document.getElementById('form-apply-leave');
    if (formLeave) {
      formLeave.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('btn-submit-leave');
        if (btn) btn.disabled = true;

        const payload = {
          leave_type: document.getElementById('leave-type').value,
          start_date: document.getElementById('leave-start-date').value,
          end_date: document.getElementById('leave-end-date').value,
          half_day: document.getElementById('leave-half-day').value,
          reason: document.getElementById('leave-reason').value
        };

        try {
          const res = await fetch('/api/teacher/leaves', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(payload)
          });
          const json = await res.json();
          if (json.success) {
            showToast(json.message || 'Leave application submitted successfully!', 'success');
            formLeave.reset();
            loadLeaves();
          } else {
            showToast(json.error || 'Failed to submit leave application', 'error');
          }
        } catch (err) {
          showToast('Network error while submitting leave application', 'error');
        } finally {
          if (btn) btn.disabled = false;
        }
      });
    }

    // Submit Correction Form
    const formCorr = document.getElementById('form-submit-correction');
    if (formCorr) {
      formCorr.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('btn-submit-corr');
        if (btn) btn.disabled = true;

        const payload = {
          attendance_date: document.getElementById('corr-date').value,
          issue_type: document.getElementById('corr-issue-type').value,
          requested_time: document.getElementById('corr-req-time').value,
          reason: document.getElementById('corr-reason').value,
          note: document.getElementById('corr-note').value
        };

        try {
          const res = await fetch('/api/teacher/corrections', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(payload)
          });
          const json = await res.json();
          if (json.success) {
            showToast(json.message || 'Correction request submitted for administrator review!', 'success');
            formCorr.reset();
            loadCorrections();
          } else {
            showToast(json.error || 'Failed to submit correction request', 'error');
          }
        } catch (err) {
          showToast('Network error while submitting correction request', 'error');
        } finally {
          if (btn) btn.disabled = false;
        }
      });
    }

    // Filter controls
    const histMonthPicker = document.getElementById('hist-month-picker');
    if (histMonthPicker) {
      histMonthPicker.value = getTodayMonthString();
      histMonthPicker.addEventListener('change', () => {
        loadAttendanceHistory(histMonthPicker.value);
      });
    }

    document.getElementById('btn-filter-this-month')?.addEventListener('click', () => {
      const thisMonth = getTodayMonthString();
      if (histMonthPicker) histMonthPicker.value = thisMonth;
      loadAttendanceHistory(thisMonth);
    });

    document.getElementById('btn-filter-last-month')?.addEventListener('click', () => {
      const now = new Date();
      now.setMonth(now.getMonth() - 1);
      const lastMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
      if (histMonthPicker) histMonthPicker.value = lastMonth;
      loadAttendanceHistory(lastMonth);
    });

    const sumMonthPicker = document.getElementById('summary-month-picker');
    if (sumMonthPicker) {
      sumMonthPicker.value = getTodayMonthString();
      sumMonthPicker.addEventListener('change', () => {
        loadMonthlySummary(sumMonthPicker.value);
      });
    }

    document.getElementById('btn-summary-reload')?.addEventListener('click', () => {
      loadMonthlySummary(sumMonthPicker?.value);
    });
  }

  // DOM Ready
  document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initForms();
    loadDashboard();
  });

})();
