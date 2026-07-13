// Страница входа: отправка формы в /api/login (инлайн-скрипты запрещены CSP).
const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const btn = document.getElementById("login-btn");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.classList.remove("visible");
  btn.disabled = true;
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        login: form.login.value.trim(),
        password: form.password.value,
      }),
    });
    if (r.ok) {
      location.href = "/";
      return;
    }
    const data = await r.json().catch(() => ({}));
    errorBox.textContent = data.detail || "Ошибка входа";
    errorBox.classList.add("visible");
  } catch {
    errorBox.textContent = "Сервер недоступен";
    errorBox.classList.add("visible");
  } finally {
    btn.disabled = false;
  }
});
