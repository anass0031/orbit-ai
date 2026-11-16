// Thin fetch wrapper for the ORBIT API: JWT header + the API's error envelope.
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const PREFIX = "/api/v1";
const TOKEN_KEY = "orbit_token";

function safeGet(key: string): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function safeSet(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export const session = {
  token: () => safeGet(TOKEN_KEY),
  setToken: (t: string | null) => safeSet(TOKEN_KEY, t),
};

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  correlationId?: string;
  constructor(status: number, code: string, message: string, details?: unknown, correlationId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.correlationId = correlationId;
  }
  /** Field-level messages from a 422 validation error. */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    if (Array.isArray(this.details)) {
      for (const d of this.details as { field?: string; message?: string }[]) {
        if (d.field) out[d.field] = d.message || "Invalid value";
      }
    }
    return out;
  }
}

type Options = Omit<RequestInit, "body"> & { body?: unknown };

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
  const token = session.token();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) body = opts.body;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}${PREFIX}${path}`, { ...opts, headers, body });
  } catch {
    throw new ApiError(0, "network_error", `Cannot reach the ORBIT API at ${API_URL}. Is the backend running?`);
  }
  if (res.status === 401 && token) {
    session.setToken(null);
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login?expired=1";
    }
  }
  if (!res.ok) {
    let payload: { error?: { code?: string; message?: string; details?: unknown; correlation_id?: string } } = {};
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error */
    }
    const e = payload.error || {};
    throw new ApiError(res.status, e.code || "http_error", e.message || res.statusText, e.details, e.correlation_id);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);
