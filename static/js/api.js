// Обёртка над fetch: JSON, куки-сессия, редирект на /login при 401.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const r = await fetch(path, options);
  if (r.status === 401) {
    location.href = "/login";
    throw new ApiError(401, "Не выполнен вход");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    throw new ApiError(r.status, data.detail || `Ошибка ${r.status}`);
  }
  return data;
}
