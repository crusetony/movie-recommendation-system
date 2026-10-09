import { FeedbackAction, SessionFeedback } from '../types/index.ts';


const STORAGE_KEY = 'movie_recommendation_feedback';
const SCHEMA_VERSION = 1;
const ACTIONS = new Set<FeedbackAction>([
  'like',
  'dislike',
  'not_interested',
  'watched',
]);

interface FeedbackEnvelope {
  schema_version: 1;
  items: SessionFeedback[];
}

const cleanStrings = (value: unknown, limit: number): string[] => {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, limit);
};

const normalizeItem = (value: unknown): SessionFeedback | null => {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  const tmdbId = Number(item.tmdb_id);
  const action = item.action as FeedbackAction;
  if (!Number.isInteger(tmdbId) || tmdbId <= 0 || !ACTIONS.has(action)) return null;
  return {
    schema_version: 1,
    tmdb_id: tmdbId,
    action,
    timestamp:
      typeof item.timestamp === 'string' && item.timestamp
        ? item.timestamp.slice(0, 64)
        : new Date(0).toISOString(),
    title: typeof item.title === 'string' ? item.title.slice(0, 300) : '',
    overview: typeof item.overview === 'string' ? item.overview.slice(0, 3000) : '',
    genres: cleanStrings(item.genres, 20),
    keywords: cleanStrings(item.keywords, 30),
    model_version:
      typeof item.model_version === 'string' ? item.model_version.slice(0, 200) : null,
  };
};

const deduplicate = (items: SessionFeedback[]): SessionFeedback[] => {
  const byMovie = new Map<number, SessionFeedback>();
  items.forEach((item) => byMovie.set(item.tmdb_id, item));
  return Array.from(byMovie.values()).slice(-200);
};

export const loadFeedback = (): SessionFeedback[] => {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    // Migration path for the initial development format that stored a bare array.
    const candidates = Array.isArray(parsed)
      ? parsed
      : parsed && typeof parsed === 'object' &&
          Number((parsed as Record<string, unknown>).schema_version) === SCHEMA_VERSION &&
          Array.isArray((parsed as Record<string, unknown>).items)
        ? (parsed as Record<string, unknown>).items as unknown[]
        : null;
    if (candidates === null) {
      window.localStorage.removeItem(STORAGE_KEY);
      return [];
    }
    const items = deduplicate(
      candidates.map(normalizeItem).filter((item): item is SessionFeedback => item !== null),
    );
    saveFeedback(items);
    return items;
  } catch {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Storage may be disabled; feedback remains an in-memory feature.
    }
    return [];
  }
};

export const saveFeedback = (items: SessionFeedback[]): void => {
  const envelope: FeedbackEnvelope = {
    schema_version: SCHEMA_VERSION,
    items: deduplicate(items),
  };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(envelope));
  } catch {
    // Quota or privacy-mode failures must not break recommendations.
  }
};

export const clearStoredFeedback = (): void => {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Keep the UI functional when storage is unavailable.
  }
};

export { STORAGE_KEY as FEEDBACK_STORAGE_KEY };
