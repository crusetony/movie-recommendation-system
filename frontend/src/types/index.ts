export interface SystemStatus {
  status: 'ready' | 'configuration_required' | 'unavailable';
  tmdb_configured: boolean;
  tmdb_reachable: boolean;
  llm_configured: boolean;
  parser_mode: 'rules' | 'llm+rules';
  visual_model_enabled: boolean;
  visual_model_loaded: boolean;
  visual_model_name?: string | null;
  visual_fallback_reason?: string | null;
  cache_enabled: boolean;
  metrics: RuntimeMetrics;
  message: string;
}

export interface RuntimeMetrics {
  configured_model: string;
  reported_model?: string | null;
  llm_successful_requests: number;
  llm_fallbacks: number;
  llm_input_tokens: number;
  llm_output_tokens: number;
  llm_total_tokens: number;
  tmdb_requests: number;
  recommendation_requests: number;
  recommendation_cache_hits: number;
  last_recommendation_duration_ms?: number | null;
  last_recommendation_llm_calls: number;
  last_recommendation_tokens: number;
  last_recommendation_tmdb_requests: number;
  last_recommendation_cache_hit: boolean;
}

export interface QueryIntent {
  original_query: string;
  input_mode: 'preference' | 'review';
  genres: string[];
  excluded_genres: string[];
  origin_countries: string[];
  original_languages: string[];
  year_from?: number | null;
  year_to?: number | null;
  runtime_min?: number | null;
  runtime_max?: number | null;
  min_rating?: number | null;
  moods: string[];
  keywords: string[];
  retrieval_keywords?: string[];
  themes: string[];
  preferred_aspects: string[];
  disliked_aspects: string[];
  director_names: string[];
  excluded_director_names?: string[];
  cast_names: string[];
  seed_title?: string | null;
  avoid_violence: boolean;
  parser: 'rules' | 'llm+rules';
}

export type ScoreComponentKey =
  | 'hard_constraints'
  | 'text_relevance'
  | 'visual_similarity'
  | 'quality'
  | 'popularity'
  | 'user_feedback';

export interface ScoreComponent {
  key: ScoreComponentKey;
  label: string;
  score: number;
  weight: number;
  configured_weight: number;
  effective_weight: number;
  contribution: number;
  available: boolean;
  evidence: string[];
  algorithm: string;
}

export interface ScoreBreakdown {
  final_score: number;
  formula_version: string;
  components: ScoreComponent[];
}

export type FeedbackAction = 'like' | 'dislike' | 'not_interested' | 'watched';

interface MovieFeedbackProfile {
  tmdb_id: number;
  timestamp: string;
  title: string;
  overview: string;
  genres: string[];
  keywords: string[];
  model_version?: string | null;
}

// Version 1 is retained only for migration and historical experiment replay.
export type SessionFeedback = MovieFeedbackProfile & (
  | { schema_version: 1; action: FeedbackAction; rating?: never }
  | { schema_version: 2; action: 'rating'; rating: number }
);

export interface RecommendationRequest {
  query: string;
  max_recommendations: number;
  exclude_movie_ids?: number[];
  discover_page?: number;
  force_refresh?: boolean;
  feedback?: SessionFeedback[];
}

export interface MultimodalRecommendationRequest extends RecommendationRequest {
  reference_movie_id?: number;
  reference_image?: File;
}

export interface MovieSearchResult {
  tmdb_id: number;
  title: string;
  original_title: string;
  release_date?: string | null;
  poster_url?: string | null;
}

// Shared, unscored metadata for details and personal lists. Recommendation scores
// and explanations are deliberately not persisted in the library.
export interface MovieSummary extends MovieSearchResult {
  overview?: string;
  runtime?: number | null;
  genres?: string[];
  director?: string | null;
  cast?: string[];
  keywords?: string[];
  production_countries?: string[];
  vote_average?: number | null;
  vote_count?: number;
}

export interface MovieDetails extends MovieSummary {
  overview: string;
  genres: string[];
  directors: string[];
  cast: string[];
  cast_roles: Array<{ name: string; character: string }>;
  keywords: string[];
  production_countries: string[];
  spoken_languages: string[];
  tagline: string;
  status: string;
  tmdb_url: string;
}

export interface MovieGenre { id: number; name: string }
export interface MovieCatalogQuery {
  mode: 'title' | 'discover'; query?: string; year?: number; genre_id?: number; decade?: number; page: number;
}
export interface MovieCatalogResponse {
  mode: 'title' | 'discover'; movies: MovieSummary[]; page: number; total_pages: number;
  available_pages: number; total_results: number; has_next: boolean; pagination_limited: boolean;
}

export type MovieLibraryAction = 'favorite' | 'want_to_watch' | 'watched';
export interface MovieLibraryItem extends MovieSummary {
  favorite: boolean;
  watch_state: 'none' | 'want_to_watch' | 'watched';
  personal_rating: number | null;
  updated_at: string;
}

export interface MovieRecommendation {
  tmdb_id: number;
  title: string;
  original_title: string;
  overview: string;
  poster_url?: string | null;
  release_date?: string | null;
  runtime?: number | null;
  vote_average: number;
  vote_count: number;
  popularity: number;
  genres: string[];
  keywords: string[];
  director?: string | null;
  cast: string[];
  original_language: string;
  production_countries: string[];
  backdrop_url?: string | null;
  image_count: number;
  recall_sources: string[];
  tmdb_url: string;
  match_score: number;
  score_breakdown: ScoreBreakdown;
  reasoning: string;
  rank_before_mmr: number;
  rank_after_mmr: number;
  diversity_penalty: number;
  diversity_reason: string;
}

export interface RecommendationResponse {
  success: boolean;
  user_query: string;
  query_analysis: QueryIntent;
  recommendations: MovieRecommendation[];
  total_found: number;
  next_discover_page?: number | null;
  has_more: boolean;
  tmdb_total_pages?: number | null;
  warnings: string[];
  diagnostics: RecommendationDiagnostics;
}

export type RecommendationReference = { kind: 'none' } | { kind: 'image' }
  | { kind: 'movie'; movie: MovieSearchResult };
export interface RecommendationHistoryRequest {
  query: string;
  reference: RecommendationReference;
  discover_page: number;
  is_refresh: boolean;
  excluded_count: number;
  rating_count: number;
}
export interface RecommendationHistoryEntry {
  id: string;
  created_at: string;
  request: RecommendationHistoryRequest;
  response: RecommendationResponse;
}

export interface RecommendationDiagnostics {
  duration_ms: number;
  cache_hit: boolean;
  llm_calls: number;
  llm_tokens: number;
  tmdb_requests: number;
  configured_model: string;
  reported_model?: string | null;
  semantic_mode: 'vector_semantic' | 'lexical_fallback';
  semantic_fallback_reason?: string | null;
  semantic_model?: string | null;
  semantic_model_version?: string | null;
  semantic_device?: string | null;
  semantic_load_ms: number;
  semantic_encode_ms: number;
  text_vector_cache_hits: number;
  text_vector_cache_misses: number;
  visual_mode: 'image_vector' | 'unavailable';
  visual_fallback_reason?: string | null;
  visual_reference_source?: string | null;
  visual_model?: string | null;
  visual_model_version?: string | null;
  visual_device?: string | null;
  visual_load_ms: number;
  visual_encode_ms: number;
  visual_download_ms: number;
  poster_vector_cache_hits: number;
  poster_vector_cache_misses: number;
  diversity_mode: 'mmr' | 'disabled';
  mmr_lambda: number;
  mmr_algorithm?: string | null;
  mmr_reordered_count: number;
  mmr_rerank_ms: number;
  feedback_mode: 'semantic_metadata' | 'metadata' | 'unavailable';
  feedback_count: number;
  feedback_vector_count: number;
  feedback_fallback_reason?: string | null;
  feedback_encode_ms: number;
}

export interface ExperimentMetrics {
  ranking_evaluable_count?: number;
  ranking_excluded_query_ids?: string[];
  field_accuracy: number;
  field_precision: number;
  field_recall: number;
  field_f1: number;
  precision_at_5: number | null;
  ndcg_at_5: number | null;
  hard_constraint_rate: number;
  intra_list_diversity: number;
  genre_coverage: number;
  country_diversity: number;
  decade_diversity: number;
  average_latency_ms: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  text_vector_cache_hit_rate: number;
  poster_vector_cache_hit_rate: number;
  recommendation_cache_hit_rate: number;
  tmdb_requests: number;
  llm_tokens: number;
}

export interface ExperimentSummary {
  ranking_timing?: { mean_ms: number | null; p50_ms: number | null; p95_ms: number | null; sample_count: number } | null;
  comparison_group: string;
  capability_available_count: number;
  id: string;
  baseline: string;
  generated_at: string;
  query_count: number;
  verified_query_count: number;
  annotation_status: 'verified' | 'machine_reviewed' | 'draft_needs_human_review';
  metrics: ExperimentMetrics;
}

export interface ExperimentQueryResult {
  query_id: string;
  query: string;
  query_type: string;
  annotation_status: 'verified' | 'machine_reviewed' | 'draft_needs_human_review';
  ranked_tmdb_ids: number[];
  ranked_titles: string[];
  precision_at_5: number | null;
  ndcg_at_5: number | null;
  hard_constraint_rate: number;
  intra_list_diversity: number;
  duration_ms: number;
}

export interface ExperimentResult extends ExperimentSummary {
  schema_version: number;
  experiment_id: string;
  seed: number;
  top_k: number;
  formulas: Record<string, string>;
  metadata: Record<string, unknown>;
  queries: ExperimentQueryResult[];
}

export interface PerformancePhase {
  phase: string;
  wall_ms: number;
  mode?: string;
  cache_hits?: number;
  cache_misses?: number;
  encode_ms?: number;
  device?: string | null;
}

export interface PerformanceResult {
  id: string;
  schema_version: number;
  kind: string;
  generated_at: string;
  validated: boolean;
  model?: string;
  model_version?: string;
  candidate_count?: number;
  scope?: string;
  phases?: PerformancePhase[];
}

export interface BenchmarkSummary {
  machine_reviewed_count: number;
  reviewed_count: number;
  reviewed_group_counts: Record<string, number>;
  ready_for_machine_evaluation: boolean;
  machine_blockers: string[];
  total_count: number;
  verified_count: number;
  draft_count: number;
  group_counts: Record<string, number>;
  verified_group_counts: Record<string, number>;
  minimum_total: number;
  minimum_per_group: number;
  ready_for_formal_evaluation: boolean;
  blockers: string[];
}

export interface BenchmarkDataset {
  dataset_revision: string;
  summary: BenchmarkSummary;
  records: BenchmarkRecord[];
}

export interface MachineJudgment {
  tmdb_id: number;
  grade: number | null;
  reason: string;
  excerpt: string;
  unknowns: string[];
}

export interface BenchmarkRecord {
  machine_review?: { source: string; scope: string; reviewed_at: string; snapshot_sha256: string; judgments: MachineJudgment[] } | null;
  query_id: string;
  query: string;
  query_type: string;
  query_group: string;
  expected_intent: Record<string, unknown>;
  reference_movie_id?: number | null;
  reference_image?: string | null;
  relevant_tmdb_ids: number[];
  graded_relevance: Record<string, number>;
  hard_constraints: Record<string, unknown>;
  feedback: Array<Record<string, unknown>>;
  annotation_note: string;
  annotation_status: 'verified' | 'machine_reviewed' | 'draft_needs_human_review';
  annotation_snapshot_id?: string | null;
  annotation_snapshot_sha256?: string | null;
}

export interface BenchmarkAnnotationUpdate {
  dataset_revision: string;
  expected_intent: Record<string, unknown>;
  relevant_tmdb_ids: number[];
  graded_relevance: Record<string, number>;
  hard_constraints: Record<string, unknown>;
  annotation_note: string;
  annotation_status: 'verified' | 'draft_needs_human_review';
  human_review_confirmed: boolean;
  annotation_snapshot_id?: string;
  annotation_snapshot_sha256?: string;
}

export interface BenchmarkCandidate {
  tmdb_id: number;
  title: string;
  original_title: string;
  overview: string;
  genres: string[];
  keywords: string[];
  production_countries: string[];
  release_date: string | null;
  runtime: number | null;
  director: string | null;
  cast: string[];
  poster_url: string | null;
  tmdb_url: string;
}

export interface BenchmarkCandidates {
  query_id: string;
  available: boolean;
  reason: string | null;
  snapshot_id?: string;
  snapshot_sha256?: string;
  snapshot_updated_at?: string;
  scope?: string;
  candidate_count: number;
  candidates: BenchmarkCandidate[];
  diagnostics?: Partial<RecommendationDiagnostics>;
  query_parser?: string | null;
  warnings?: string[];
  captured_at?: string | null;
}

export interface BenchmarkCaptureResult extends BenchmarkCandidates {
  reused: boolean;
  reuse_reason: string | null;
  elapsed_ms: number;
  review_required: boolean;
  request_diagnostics: Partial<RecommendationDiagnostics>;
}
