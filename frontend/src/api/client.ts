import type { ApiErrorEnvelope } from "./contracts.ts";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(
    message: string,
    options: {
      status: number;
      code?: string;
      requestId?: string;
      retryAfterSeconds?: number;
    },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code ?? "request_failed";
    this.requestId = options.requestId ?? null;
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
  }
}

type UnauthorizedHandler = () => void;

export class ApiClient {
  private csrfToken: string | null = null;
  private readonly onUnauthorized: UnauthorizedHandler;

  constructor(onUnauthorized: UnauthorizedHandler = () => undefined) {
    this.onUnauthorized = onUnauthorized;
  }

  setCsrfToken(token: string | null) {
    this.csrfToken = token;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    if (init.body && typeof init.body === "string" && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (!new Set(["GET", "HEAD", "OPTIONS"]).has(method) && this.csrfToken) {
      headers.set("X-CSRF-Token", this.csrfToken);
    }

    const response = await fetch(path, {
      ...init,
      method,
      headers,
      credentials: "include",
    });
    if (response.status === 401) {
      this.onUnauthorized();
    }
    if (!response.ok) {
      throw await this.toApiError(response);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    const contentType = response.headers.get("content-type") ?? "";
    if (contentType.includes("application/json")) {
      return (await response.json()) as T;
    }
    return (await response.text()) as T;
  }

  private async toApiError(response: Response): Promise<ApiError> {
    let body: ApiErrorEnvelope | null = null;
    try {
      body = (await response.json()) as ApiErrorEnvelope;
    } catch {
      body = null;
    }
    const retryAfter = Number(response.headers.get("retry-after"));
    return new ApiError(body?.error.message ?? "The request could not be completed.", {
      status: response.status,
      code: body?.error.code,
      requestId: body?.error.requestId,
      retryAfterSeconds: Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
    });
  }
}
