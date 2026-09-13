/** Typed API client.
 *
 * Same-origin by default. `VITE_API_BASE` (or a `window.BLINDSPOT_API_BASE`
 * global) points it at a separate backend when the UI runs on a Vite dev server.
 */
import type {
  AnalysisResult,
  AppSettings,
  BlindSpotPattern,
  Dashboard,
  IncidentListItem,
  IngestionResult,
  NormalizedTest,
  RecommendationStatus,
  TestSourceInfo,
} from "../types";

declare global {
  interface Window {
    BLINDSPOT_API_BASE?: string;
  }
}

const BASE = (typeof window !== "undefined" && window.BLINDSPOT_API_BASE) || "";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError("Cannot reach the BlindSpot API. Is the backend running?", 0);
  }

  if (!response.ok) {
    // FastAPI puts the useful message in `detail`; fall back to the status text.
    let message = response.statusText || `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        message = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
      } else if (payload?.error) {
        message = payload.error;
      }
    } catch {
      /* body was not JSON; keep the status text */
    }
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export const api = {
  dashboard: () => request<Dashboard>("/api/dashboard"),

  settings: () => request<AppSettings>("/api/settings"),

  health: () => request<Record<string, unknown>>("/api/health"),

  // -- tests --------------------------------------------------------------
  listTests: (params: { q?: string; feature?: string; framework?: string; limit?: number; offset?: number }) =>
    request<Paged<NormalizedTest>>(`/api/tests${query(params)}`),

  testStats: () => request<Record<string, unknown>>("/api/tests/stats"),

  listSources: () => request<{ items: TestSourceInfo[] }>("/api/tests/sources"),

  indexFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<IngestionResult>("/api/tests/index/file", { method: "POST", body: form });
  },

  indexRepository: (path: string) =>
    request<IngestionResult>("/api/tests/index/repository", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),

  reindex: () => request<{ tests_indexed: number }>("/api/tests/reindex", { method: "POST" }),

  deleteSource: (id: number) =>
    request<{ deleted: boolean }>(`/api/tests/sources/${id}`, { method: "DELETE" }),

  // -- incidents ----------------------------------------------------------
  analyze: (incident: string, extra?: Record<string, unknown>) =>
    request<AnalysisResult>("/api/incidents/analyze", {
      method: "POST",
      body: JSON.stringify({ incident, ...extra }),
    }),

  uploadIncident: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<AnalysisResult>("/api/incidents/upload", { method: "POST", body: form });
  },

  listIncidents: (params: { coverage?: string; feature?: string; q?: string; limit?: number; offset?: number }) =>
    request<Paged<IncidentListItem>>(`/api/incidents${query(params)}`),

  getIncident: (id: string) => request<AnalysisResult>(`/api/incidents/${encodeURIComponent(id)}`),

  reanalyze: (id: string) =>
    request<AnalysisResult>(`/api/incidents/${encodeURIComponent(id)}/reanalyze`, { method: "POST" }),

  deleteIncident: (id: string) =>
    request<{ deleted: boolean }>(`/api/incidents/${encodeURIComponent(id)}`, { method: "DELETE" }),

  setRecommendationStatus: (id: number, status: RecommendationStatus) =>
    request<{ status: string }>(`/api/recommendations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),

  // -- blind spots --------------------------------------------------------
  listBlindSpots: () => request<{ items: BlindSpotPattern[] }>("/api/blind-spots"),

  getBlindSpot: (identifier: string) =>
    request<{ pattern: BlindSpotPattern; incidents: Array<Record<string, unknown>> }>(
      `/api/blind-spots/${encodeURIComponent(identifier)}`,
    ),

  recomputeBlindSpots: () =>
    request<{ items: BlindSpotPattern[] }>("/api/blind-spots/recompute", { method: "POST" }),
};

function query(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  });
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}
