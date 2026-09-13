(() => {
  "use strict";

  document.addEventListener("DOMContentLoaded", () => {
    // 1. Password Visibility Toggle
    const toggleButtons = document.querySelectorAll(".password-toggle");
    toggleButtons.forEach((toggle) => {
      const container = toggle.closest(".auth-input");
      const passwordInput = container ? container.querySelector("input[type='password'], input[type='text']") : null;
      if (!passwordInput) return;

      const eyeOpenSvg = `<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg>`;
      const eyeClosedSvg = `<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" y1="2" x2="22" y2="22"/></svg>`;

      toggle.addEventListener("click", (e) => {
        e.preventDefault();
        const isCurrentlyText = passwordInput.type === "text";
        passwordInput.type = isCurrentlyText ? "password" : "text";
        toggle.setAttribute("aria-pressed", String(!isCurrentlyText));
        toggle.setAttribute("aria-label", isCurrentlyText ? "Show password" : "Hide password");
        toggle.innerHTML = isCurrentlyText ? eyeOpenSvg : eyeClosedSvg;
        passwordInput.focus();
      });
    });

    // 2. Password Length Validation (6 to 12 characters)
    const forms = document.querySelectorAll("form.auth-form");
    forms.forEach((form) => {
      const passwordInput = form.querySelector("input[name='password']");
      if (!passwordInput) return;

      const container = passwordInput.closest(".auth-input");
      let errorEl = form.querySelector("#password-error, .auth-field-error");

      if (!errorEl) {
        errorEl = document.createElement("div");
        errorEl.className = "auth-field-error";
        errorEl.setAttribute("role", "alert");
        errorEl.style.display = "none";
        if (container && container.parentNode) {
          container.parentNode.insertBefore(errorEl, container.nextSibling);
        } else {
          passwordInput.parentNode.insertBefore(errorEl, passwordInput.nextSibling);
        }
      }

      function validatePassword() {
        const val = passwordInput.value;
        if (!val) {
          errorEl.textContent = "";
          errorEl.style.display = "none";
          if (container) container.classList.remove("has-error");
          return true;
        }

        if (val.length < 6 || val.length > 12) {
          errorEl.textContent = "Password must be between 6 and 12 characters.";
          errorEl.style.display = "flex";
          if (container) container.classList.add("has-error");
          passwordInput.setAttribute("aria-invalid", "true");
          return false;
        }

        errorEl.textContent = "";
        errorEl.style.display = "none";
        if (container) container.classList.remove("has-error");
        passwordInput.removeAttribute("aria-invalid");
        return true;
      }

      passwordInput.addEventListener("input", validatePassword);
      passwordInput.addEventListener("blur", validatePassword);

      form.addEventListener("submit", (e) => {
        const val = passwordInput.value;
        if (!val) return; // Allow browser required validation to trigger

        if (val.length < 6 || val.length > 12) {
          e.preventDefault();
          validatePassword();
          passwordInput.focus();
        }
      });
    });
  });
})();
