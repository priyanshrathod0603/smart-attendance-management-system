# USER INTERFACE & DESIGN SYSTEM RULES

## 1. Design System Foundations
* **Color Palette**:
  - Dark Theme Background: `#0B0F19` / `#111827`
  - Card & Container Surface: `#1F2937` / `#1E293B`
  - Border Accents: `#374151` / `rgba(255, 255, 255, 0.1)`
  - Primary Brand Accent: Indigo / Blue (`#3B82F6` / `#6366F1`)
  - Success Indicator: `#10B981` (Emerald)
  - Warning Indicator: `#F59E0B` (Amber)
  - Danger Indicator: `#EF4444` (Rose)
* **Typography**: System UI font stack (`Inter`, `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `Roboto`).
* **Layout Architecture**: Responsive CSS Grid and Flexbox with a fixed/collapsible sidebar navigation.

---

## 2. Template Structure (`templates/`)
* `base.html`: Master layout containing the sidebar navigation, top bar, role badges, user profile dropdown, and modal wrappers.
* `dashboard.html`: Academic KPI cards, attendance progress charts, class rosters.
* `dashboard_enterprise.html`: Advanced institutional time metrics, late counters, overtime graphs.
* `students.html` & `teachers.html`: Management tables with search, filter dropdowns, and 3-angle face enrollment modals.
* `cameras.html`: Multi-camera grid with live MJPEG streaming feeds and status indicators.
* `hr/`: Comprehensive HR and Payroll views (`employees.html`, `employee_profile.html`, `payroll.html`, `settings.html`, `reports.html`).
* `auth/`: Authentication views (`login.html`, `signup.html`, `verify_code.html`, `verify_email.html`, `forgot_password.html`, `account.html`).
* `chat.html`: Natural language attendance query chat interface.

---

## 3. UI Guidelines for Future AI Agents
1. **Preserve Dark Theme Consistency**: Maintain CSS variables defined in `static/styles.css`.
2. **Modal Flows**: Multi-step modals (such as 3-angle face enrollment) must guide the user through Front -> Left -> Right photo capture.
3. **Toast Notifications & Alerts**: Use `showToast()` helper in `static/common.js` for user feedback rather than raw alerts.
4. **Responsive Layouts**: Ensure all tables have horizontal scroll wrappers (`.table-responsive`) for mobile display.
