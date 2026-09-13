/** Wire types mirroring the FastAPI schemas in backend/app/api/schemas.py. */

export type Coverage = "COVERED" | "PARTIAL" | "NOT_COVERED" | "INSUFFICIENT_EVIDENCE";
export type Risk = "HIGH" | "MEDIUM" | "LOW";
export type ConfidenceLevel = "HIGH" | "MEDIUM" | "LOW";
export type RecommendationStatus = "PROPOSED" | "ACCEPTED" | "IGNORED" | "ALREADY_COVERED";

export interface NormalizedTest {
  id: string;
  name: string;
  feature: string;
  scenario: string;
  inputs: Record<string, unknown>;
  expected_behavior: string;
  tags?: string[];
  source: string;
  framework: string;
  line_number?: number | null;
}

export interface RetrievedTest {
  test: NormalizedTest;
  score: number;
  vector_score: number;
  lexical_score: number;
  feature_match: boolean;
  matched_terms: string[];
}

export interface Evidence {
  kind: string;
  statement: string;
  production_value?: string | null;
  test_value?: string | null;
  test_id?: string | null;
}

export interface Gap {
  id?: number | null;
  gap_type: string;
  summary: string;
  detail: string;
  missing_conditions: Record<string, string>;
  family_key: string;
  family_label: string;
}

export interface Recommendation {
  id?: number | null;
  title: string;
  rationale: string;
  priority: Risk;
  status: RecommendationStatus;
  suggested_inputs: Record<string, unknown>;
}

export interface NormalizedIncident {
  id: string;
  title: string;
  description: string;
  feature: string;
  scenario: string;
  conditions: Record<string, unknown>;
  failure: string;
  root_cause: string;
  signals: string[];
  severity: string;
  occurred_at?: string | null;
}

export interface RetrievalDebug {
  candidate_count: number;
  considered_count: number;
  top_score: number;
  strategy: string;
  vector_store: string;
  embedding_model: string;
}

export interface ComparisonDebug {
  feature_match: boolean;
  scenario_match: boolean;
  condition_match: boolean;
  matched_conditions: string[];
  unmatched_conditions: string[];
  signal_match: boolean;
  unmatched_signals: string[];
  best_test_id?: string | null;
}

export interface AnalysisResult {
  incident: NormalizedIncident;
  coverage: Coverage;
  effectiveness: string;
  confidence: number;
  confidence_level: ConfidenceLevel;
  risk: Risk;
  explanation: string;
  gaps: Gap[];
  relevant_tests: RetrievedTest[];
  recommendations: Recommendation[];
  evidence: Evidence[];
  retrieval: RetrievalDebug;
  comparison: ComparisonDebug;
  reasoning_source: string;
  analyzed_at: string;
  analysis_id?: number | null;
}

export interface IncidentListItem {
  id: string;
  title: string;
  description: string;
  feature: string;
  severity: string;
  conditions: Record<string, unknown>;
  signals: string[];
  created_at: string;
  coverage?: Coverage | null;
  confidence?: number | null;
  confidence_level?: ConfidenceLevel | null;
  risk?: Risk | null;
  gap_type?: string | null;
}

export interface BlindSpotPattern {
  id?: number | null;
  key: string;
  label: string;
  incident_count: number;
  gap_count: number;
  risk: Risk;
  gap_types: string[];
  features: string[];
  example_incident_ids: string[];
  summary: string;
}

export interface TestSourceInfo {
  id: number;
  kind: string;
  location: string;
  label: string;
  test_count: number;
  indexed_at?: string | null;
  status: string;
  detail: Record<string, unknown>;
}

export interface IngestionIssue {
  location: string;
  reason: string;
  severity: string;
}

export interface IngestionResult {
  source: TestSourceInfo;
  tests_discovered: number;
  tests_indexed: number;
  duplicates_skipped: number;
  files_scanned: number;
  files_skipped: number;
  issues: IngestionIssue[];
  duration_ms: number;
}

export interface DashboardMetrics {
  incidents: number;
  tests_indexed: number;
  test_gaps: number;
  blind_spots: number;
  covered: number;
  partial: number;
  not_covered: number;
  insufficient_evidence: number;
}

export interface Dashboard {
  metrics: DashboardMetrics;
  tests: {
    total: number;
    sources: number;
    by_feature: Record<string, number>;
    by_framework: Record<string, number>;
    last_indexed?: string | null;
  };
  top_blind_spots: BlindSpotPattern[];
  recent_incidents: Array<Record<string, unknown>>;
  recent_runs: Array<Record<string, unknown>>;
  index: Record<string, unknown>;
  ai: Record<string, unknown>;
}

export interface AppSettings {
  ai: Record<string, unknown>;
  index: Record<string, unknown>;
  retrieval: Record<string, unknown>;
  limits: Record<string, unknown>;
  security: Record<string, unknown>;
  database: string;
  version: string;
}
