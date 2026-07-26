(() => {
  "use strict";
  const toggle = document.querySelector(".password-toggle");
  const password = document.querySelector("#login-password");
  if (!toggle || !password) return;
  toggle.addEventListener("click", () => {
    const visible = password.type === "text";
    password.type = visible ? "password" : "text";
    toggle.setAttribute("aria-pressed", String(!visible));
    toggle.setAttribute("aria-label", visible ? "Show password" : "Hide password");
  });
})();
