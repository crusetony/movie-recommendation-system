import { MovieLibraryAction, MovieLibraryItem, MovieSummary, SessionFeedback } from '../types/index.ts';

export const MOVIE_LIBRARY_STORAGE_KEY = 'movie_recommendation_library';
const MAX_ITEMS = 500;
const string = (value: unknown, limit: number) => typeof value === 'string' ? value.slice(0, limit) : '';
const strings = (value: unknown) => Array.isArray(value)
  ? value.filter((item): item is string => typeof item === 'string').slice(0, 30).map(item => item.slice(0, 200)) : [];
const nonnegative = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
const poster = (value: unknown): string | null => {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'image.tmdb.org' ? url.href : null;
  } catch { return null; }
};

const normalize = (value: unknown): MovieLibraryItem | null => {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  if (!Number.isInteger(item.tmdb_id) || Number(item.tmdb_id) <= 0) return null;
  const rating = typeof item.personal_rating === 'number' && Number.isInteger(item.personal_rating)
    && item.personal_rating >= 1 && item.personal_rating <= 10 ? item.personal_rating : null;
  // Existing scored entries acquire watched status under the same invariant.
  const watchState = rating !== null ? 'watched'
    : item.watch_state === 'want_to_watch' || item.watch_state === 'watched' ? item.watch_state : 'none';
  if (item.favorite !== true && watchState === 'none' && rating === null) return null;
  return {
    tmdb_id: Number(item.tmdb_id), title: string(item.title, 300) || '未知片名',
    original_title: string(item.original_title, 300), overview: string(item.overview, 6000),
    poster_url: poster(item.poster_url), release_date: string(item.release_date, 20) || null,
    runtime: nonnegative(item.runtime), vote_average: nonnegative(item.vote_average),
    vote_count: nonnegative(item.vote_count) ?? 0, genres: strings(item.genres),
    director: string(item.director, 500) || null, cast: strings(item.cast),
    keywords: strings(item.keywords), production_countries: strings(item.production_countries),
    favorite: item.favorite === true, watch_state: watchState,
    personal_rating: rating,
    updated_at: string(item.updated_at, 64) || new Date(0).toISOString(),
  };
};

export interface LibraryLoadResult { items: MovieLibraryItem[]; warning: string | null; migrated: boolean }
export const loadMovieLibrary = (feedback: SessionFeedback[] = []): LibraryLoadResult => {
  try {
    const raw = window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY);
    if (raw === null) {
      // Only carry over the existing explicit "watched" record on first use.
      // Likes are recommendation feedback, not an implicit favorite list.
      const items = feedback.filter(item => item.action === 'watched').map(item => normalize({
        ...item, favorite: false, watch_state: 'watched', updated_at: item.timestamp,
      })).filter((item): item is MovieLibraryItem => item !== null);
      return { items, warning: null, migrated: items.length > 0 };
    }
    const parsed = JSON.parse(raw);
    if (parsed?.schema_version !== 1 || !Array.isArray(parsed.items)) {
      return { items: [], warning: '片单版本无法识别，原数据已保留；可明确清空片单后重新使用。', migrated: false };
    }
    const unique = new Map<number, MovieLibraryItem>();
    for (const value of parsed.items) {
      const item = normalize(value);
      if (item) unique.set(item.tmdb_id, item);
    }
    return { items: Array.from(unique.values()), warning: null, migrated: false };
  } catch {
    return { items: [], warning: '无法读取本机片单，原数据未删除；当前操作仅在本次页面中生效。', migrated: false };
  }
};

export const saveMovieLibrary = (items: MovieLibraryItem[], reset = false): string | null => {
  try {
    if (!reset) {
      const raw = window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY);
      if (raw !== null) {
        const old = JSON.parse(raw);
        if (old?.schema_version !== 1 || !Array.isArray(old.items)) throw new Error('Unsupported library');
      }
    }
    window.localStorage.setItem(MOVIE_LIBRARY_STORAGE_KEY, JSON.stringify({ schema_version: 1, items }));
    return null;
  } catch {
    return '浏览器未能保存片单，原数据未覆盖；本次修改可能在刷新后丢失。';
  }
};

export const updateMovieLibrary = (
  items: MovieLibraryItem[], movie: MovieSummary, action: MovieLibraryAction,
  updatedAt = new Date().toISOString(),
): MovieLibraryItem[] => {
  const previous = items.find(item => item.tmdb_id === movie.tmdb_id);
  if (!previous && items.length >= MAX_ITEMS) throw new Error(`片单最多保存 ${MAX_ITEMS} 部，请先移除不需要的条目。`);
  const next = withMetadata(previous, movie, updatedAt);
  if (action === 'favorite') next.favorite = !next.favorite;
  else {
    next.watch_state = next.watch_state === action ? 'none' : action;
    if (next.watch_state !== 'watched') next.personal_rating = null;
  }
  const remaining = items.filter(item => item.tmdb_id !== movie.tmdb_id);
  const clean = normalize(next);
  return clean ? [...remaining, clean] : remaining;
};

const withMetadata = (previous: MovieLibraryItem | undefined, movie: MovieSummary, updatedAt: string): MovieLibraryItem => ({
  ...previous, ...Object.fromEntries(Object.entries(movie).filter(([, value]) => value !== undefined && value !== null && value !== '')),
  favorite: previous?.favorite ?? false, watch_state: previous?.watch_state ?? 'none',
  personal_rating: previous?.personal_rating ?? null, updated_at: updatedAt,
} as MovieLibraryItem);

export const setMovieRating = (items: MovieLibraryItem[], movie: MovieSummary, rating: number | null): MovieLibraryItem[] => {
  if (rating !== null && (!Number.isInteger(rating) || rating < 1 || rating > 10)) throw new Error('个人评分须为 1–10 的整数分。');
  const previous = items.find(item => item.tmdb_id === movie.tmdb_id);
  if (!previous && items.length >= MAX_ITEMS && rating !== null) throw new Error(`片单最多保存 ${MAX_ITEMS} 部，请先移除不需要的条目。`);
  const next = withMetadata(previous, movie, new Date().toISOString());
  next.personal_rating = rating;
  if (rating !== null) next.watch_state = 'watched';
  const remaining = items.filter(item => item.tmdb_id !== movie.tmdb_id);
  const clean = normalize(next);
  return clean ? [...remaining, clean] : remaining;
};
