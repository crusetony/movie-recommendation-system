import { loadMovieLibrary, MOVIE_LIBRARY_STORAGE_KEY, saveMovieLibrary, setMovieRating, updateMovieLibrary } from './movieLibraryStorage.ts';
import { MovieSummary, SessionFeedback } from '../types/index.ts';

// Synthetic fixtures only; these are never written to benchmark data.
const movie: MovieSummary = { tmdb_id: 123, title: '测试电影', original_title: 'Fixture', overview: '测试简介', genres: ['剧情'], poster_url: 'https://image.tmdb.org/t/p/w500/test.jpg' };
beforeEach(() => window.localStorage.clear());
afterEach(() => jest.restoreAllMocks());

test('favorites and watch state persist independently and wanted/watched are exclusive', () => {
  let items = updateMovieLibrary([], movie, 'favorite');
  items = updateMovieLibrary(items, movie, 'want_to_watch');
  items = updateMovieLibrary(items, movie, 'watched');
  expect(items[0]).toMatchObject({ favorite: true, watch_state: 'watched' });
  expect(saveMovieLibrary(items)).toBeNull();
  expect(loadMovieLibrary().items).toEqual(items);
  items = updateMovieLibrary(items, movie, 'favorite');
  expect(items[0]).toMatchObject({ favorite: false, watch_state: 'watched' });
  expect(updateMovieLibrary(items, movie, 'watched')).toEqual([]);
});

test('only explicit watched feedback is carried over on first use; an empty saved list stays empty', () => {
  const feedback = ['watched', 'like'].map((action, index) => ({ ...movie, tmdb_id: 123 + index, action, timestamp: '2026-10-08T00:00:00Z' })) as SessionFeedback[];
  expect(loadMovieLibrary(feedback)).toMatchObject({ migrated: true, items: [{ tmdb_id: 123, favorite: false, watch_state: 'watched' }] });
  saveMovieLibrary([]);
  expect(loadMovieLibrary(feedback).items).toEqual([]);
});

test('only metadata is persisted; scores, explanations and raw responses are omitted', () => {
  const items = updateMovieLibrary([], { ...movie, score_breakdown: { final_score: 99 }, reasoning: 'not stored' } as MovieSummary, 'favorite');
  saveMovieLibrary(items);
  const stored = window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY)!;
  expect(stored).not.toMatch(/score_breakdown|reasoning/);
  expect(stored).toContain('测试简介');
});

test('deduplicates TMDB ids, ignores malformed entries and removes unsafe poster URLs', () => {
  window.localStorage.setItem(MOVIE_LIBRARY_STORAGE_KEY, JSON.stringify({ schema_version: 1, items: [
    { ...movie, favorite: true }, { ...movie, title: '更新', favorite: true, poster_url: 'javascript:alert(1)' },
    { ...movie, tmdb_id: -1, favorite: true }, { ...movie, tmdb_id: 124, favorite: false, watch_state: 'none' },
  ] }));
  expect(loadMovieLibrary().items).toMatchObject([{ tmdb_id: 123, title: '更新', poster_url: null }]);
});

test.each(['{broken', JSON.stringify({ schema_version: 99, items: [] })])('corrupt or newer data remains intact until an explicit reset: %s', raw => {
  window.localStorage.setItem(MOVIE_LIBRARY_STORAGE_KEY, raw);
  expect(loadMovieLibrary().warning).toBeTruthy();
  expect(saveMovieLibrary(updateMovieLibrary([], movie, 'favorite'))).toBeTruthy();
  expect(window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY)).toBe(raw);
  expect(saveMovieLibrary([], true)).toBeNull();
  expect(loadMovieLibrary().items).toEqual([]);
});

test('quota and disabled storage return a visible warning, without deleting valid data', () => {
  saveMovieLibrary(updateMovieLibrary([], movie, 'favorite'));
  const before = window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY);
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Quota exceeded'); });
  expect(saveMovieLibrary([])).toBeTruthy();
  expect(window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY)).toBe(before);
  jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Disabled'); });
  expect(loadMovieLibrary()).toMatchObject({ items: [], warning: expect.any(String) });
});

test('capacity rejects new records instead of silently evicting existing ones', () => {
  const items = Array.from({ length: 500 }, (_, index) => ({ ...movie, tmdb_id: index + 1, favorite: true, watch_state: 'none' as const, personal_rating: null, updated_at: '' }));
  expect(() => updateMovieLibrary(items, { ...movie, tmdb_id: 501 }, 'favorite')).toThrow('500');
  expect(updateMovieLibrary(items, movie, 'watched')).toHaveLength(500);
});

test('personal scores persist, imply watched, and clearing only the rating retains watched/favorite', () => {
  let items = setMovieRating([], movie, 9);
  expect(items[0]).toMatchObject({ personal_rating: 9, favorite: false, watch_state: 'watched' });
  saveMovieLibrary(items); expect(loadMovieLibrary().items[0].personal_rating).toBe(9);
  items = updateMovieLibrary(items, movie, 'favorite');
  items = setMovieRating(items, movie, null);
  expect(items[0]).toMatchObject({ favorite: true, personal_rating: null, watch_state: 'watched' });
  expect(setMovieRating([], movie, null)).toEqual([]);
});

test.each([0, -1, 11, 4.5, NaN])('rejects invalid personal scores without changing the list: %s', rating => {
  const items = updateMovieLibrary([], movie, 'favorite');
  expect(() => setMovieRating(items, movie, rating)).toThrow('1–10');
  expect(items[0].personal_rating).toBeNull();
});

test('canceling watched or switching to want-to-watch clears the rating and preserves favorites', () => {
  const scored = setMovieRating(updateMovieLibrary([], movie, 'favorite'), movie, 9);
  expect(updateMovieLibrary(scored, movie, 'watched')[0]).toMatchObject({ personal_rating: null, watch_state: 'none', favorite: true });
  expect(updateMovieLibrary(scored, movie, 'want_to_watch')[0]).toMatchObject({ personal_rating: null, watch_state: 'want_to_watch', favorite: true });
});

test('older scored entries are restored as watched; legacy zero remains unscored', () => {
  window.localStorage.setItem(MOVIE_LIBRARY_STORAGE_KEY, JSON.stringify({ schema_version: 1, items: [
    { ...movie, favorite: true, watch_state: 'none', personal_rating: 9 },
    { ...movie, tmdb_id: 456, favorite: true, personal_rating: 0 },
  ] }));
  expect(loadMovieLibrary().items).toMatchObject([
    { personal_rating: 9, watch_state: 'watched' }, { personal_rating: null, watch_state: 'none' },
  ]);
});
