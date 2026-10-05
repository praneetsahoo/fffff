// Thin API client. Every failure becomes an ApiError with a human message taken from the
// server's {error: {code, message, details}} body, or a clear network/timeout message.

export class ApiError extends Error {
  constructor(message, { code = "error", status = 0, details = {} } = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

const NETWORK_MSG = "Can't reach the OpsIntel server. Check your connection and try again.";

async function parseError(res) {
  try {
    const body = await res.json();
    if (body?.error) {
      return new ApiError(body.error.message, { code: body.error.code, status: res.status, details: body.error.details });
    }
  } catch { /* non-JSON error body */ }
  return new ApiError(`The server returned an error (${res.status}).`, { status: res.status });
}

export async function get(path, params, { timeout = 20000 } = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params || {})) {
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, x));
    else url.searchParams.set(k, v);
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(url, { signal: ctrl.signal, headers: { Accept: "application/json" } });
  } catch (e) {
    throw new ApiError(e.name === "AbortError" ? "The request took too long. Please try again." : NETWORK_MSG, { code: "network" });
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function post(path) {
  let res;
  try {
    res = await fetch(path, { method: "POST", headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError(NETWORK_MSG, { code: "network" });
  }
  if (!res.ok) throw await parseError(res);
  return res.json();
}

// Upload with byte-level progress (fetch has no upload progress events).
export function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.timeout = 120000;
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body);
      if (body?.error) {
        return reject(new ApiError(body.error.message, { code: body.error.code, status: xhr.status, details: body.error.details }));
      }
      reject(new ApiError(`Upload failed (${xhr.status}).`, { status: xhr.status }));
    };
    xhr.onerror = () => reject(new ApiError(NETWORK_MSG, { code: "network" }));
    xhr.ontimeout = () => reject(new ApiError("The upload timed out. Please try again.", { code: "timeout" }));
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
}

export function queryString(params) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => usp.append(k, x));
    else usp.set(k, v);
  }
  return usp.toString();
}
