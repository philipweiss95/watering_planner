export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (error) {
    throw new ApiError("Server nicht erreichbar", 0, error);
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ApiError(
      payload?.error || `Anfrage fehlgeschlagen (${response.status})`,
      response.status,
      payload,
    );
  }
  return payload;
}

export const api = {
  get(path) {
    return request(path);
  },
  post(path, body = {}) {
    return request(path, { method: "POST", body: JSON.stringify(body) });
  },
  put(path, body) {
    return request(path, { method: "PUT", body: JSON.stringify(body) });
  },
  delete(path) {
    return request(path, { method: "DELETE" });
  },
};
