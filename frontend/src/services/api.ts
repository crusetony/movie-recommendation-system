import axios from 'axios';
import {
  RecommendationRequest,
  RecommendationResponse,
  MultimodalRecommendationRequest,
  MovieSearchResult,
  MovieDetails,
  MovieGenre,
  MovieCatalogQuery,
  MovieCatalogResponse,
  SystemStatus,
  ExperimentResult,
  ExperimentSummary,
  PerformanceResult,
  BenchmarkDataset,
  BenchmarkAnnotationUpdate,
  BenchmarkCandidates,
  BenchmarkCaptureResult,
} from '../types/index.ts';
import { normalizeMovieDetails } from './movieDetails.ts';

const API_BASE_URL = process.env.REACT_APP_API_URL || '';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 90000,
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      const detail = error.response.data?.detail;
      const message = typeof detail === 'string' ? detail
        : Array.isArray(detail) ? detail.map((item: { msg?: string }) => item.msg || '参数无效').join('；')
          : '请求失败';
      throw new Error(message);
    }
    if (error.request) {
      throw new Error('无法连接后端，请确认本地后端服务已经启动');
    }
    throw new Error(error.message || '未知错误');
  }
);

export const apiService = {
  getSystemStatus: async (): Promise<SystemStatus> => {
    const response = await api.get<SystemStatus>('/status');
    return response.data;
  },

  recommendMovies: async (
    request: RecommendationRequest
  ): Promise<RecommendationResponse> => {
    const response = await api.post<RecommendationResponse>('/recommend', request);
    return response.data;
  },

  recommendMoviesMultimodal: async (
    request: MultimodalRecommendationRequest
  ): Promise<RecommendationResponse> => {
    const form = new FormData();
    form.append('query', request.query);
    form.append('max_recommendations', String(request.max_recommendations));
    form.append('exclude_movie_ids', JSON.stringify(request.exclude_movie_ids || []));
    form.append('feedback', JSON.stringify(request.feedback || []));
    form.append('discover_page', String(request.discover_page || 1));
    form.append('force_refresh', String(request.force_refresh ?? false));
    if (request.reference_movie_id) {
      form.append('reference_movie_id', String(request.reference_movie_id));
    }
    if (request.reference_image) {
      form.append('reference_image', request.reference_image, request.reference_image.name);
    }
    const response = await api.post<RecommendationResponse>('/recommend/multimodal', form, {
      timeout: 180000,
    });
    return response.data;
  },

  searchMovies: async (query: string): Promise<MovieSearchResult[]> => {
    const response = await api.get<MovieSearchResult[]>('/movies/search', {
      params: { query, limit: 8 },
    });
    return response.data;
  },

  getMovieDetails: async (movieId: number, signal?: AbortSignal): Promise<MovieDetails> => {
    const response = await api.get<{ movie: unknown }>(`/movies/${movieId}`, { signal });
    return normalizeMovieDetails(response.data.movie, movieId);
  },

  getMovieGenres: async (signal?: AbortSignal): Promise<MovieGenre[]> => {
    const response = await api.get<MovieGenre[]>('/movies/genres', { signal, timeout: 30000 });
    return response.data;
  },

  browseMovies: async (query: MovieCatalogQuery, signal?: AbortSignal): Promise<MovieCatalogResponse> => {
    const response = await api.get<MovieCatalogResponse>('/movies/catalog', { params: query, signal, timeout: 30000 });
    return response.data;
  },

  healthCheck: async (): Promise<{ status: 'healthy' }> => {
    const response = await api.get<{ status: 'healthy' }>('/health');
    return response.data;
  },

  listExperimentResults: async (): Promise<ExperimentSummary[]> => {
    const response = await api.get<{ results: ExperimentSummary[] }>('/experiments/results');
    return response.data.results;
  },

  getExperimentResult: async (resultId: string): Promise<ExperimentResult> => {
    const response = await api.get<ExperimentResult>(`/experiments/results/${encodeURIComponent(resultId)}`);
    return response.data;
  },

  listPerformanceResults: async (): Promise<PerformanceResult[]> => {
    const response = await api.get<{ results: PerformanceResult[] }>('/experiments/performance');
    return response.data.results;
  },

  getBenchmarkDataset: async (): Promise<BenchmarkDataset> => {
    const response = await api.get<BenchmarkDataset>('/experiments/benchmark');
    return response.data;
  },

  getBenchmarkCandidates: async (queryId: string, ignoreAnnotationBinding = false): Promise<BenchmarkCandidates> => {
    const response = await api.get<BenchmarkCandidates>(
      `/experiments/benchmark/${encodeURIComponent(queryId)}/candidates`,
      { params: { ignore_annotation_binding: ignoreAnnotationBinding } },
    );
    return response.data;
  },

  captureBenchmarkCandidates: async (queryId: string, datasetRevision: string, refresh = false): Promise<BenchmarkCaptureResult> => {
    const response = await api.post<BenchmarkCaptureResult>(
      `/experiments/benchmark/${encodeURIComponent(queryId)}/capture`,
      { dataset_revision: datasetRevision, refresh },
      { timeout: 180000 },
    );
    return response.data;
  },

  updateBenchmarkAnnotation: async (
    queryId: string,
    update: BenchmarkAnnotationUpdate,
  ): Promise<BenchmarkDataset> => {
    const response = await api.put<BenchmarkDataset>(
      `/experiments/benchmark/${encodeURIComponent(queryId)}`,
      update,
    );
    return response.data;
  },
};
