import { buildRatingProfile } from './ratingProfile.ts';
import { setMovieRating, updateMovieLibrary } from './movieLibraryStorage.ts';
import { buildRatingFeedback } from './ratingFeedback.ts';
import { MovieLibraryItem } from '../types/index.ts';

const movie = (tmdb_id: number, rating: number | null, metadata = {}): MovieLibraryItem => ({
  tmdb_id, title: `核对影片${tmdb_id}`, original_title: '', favorite: true,
  watch_state: rating === null ? 'want_to_watch' : 'watched', personal_rating: rating, updated_at: '2026-10-08', ...metadata,
});
const dimension = (items: MovieLibraryItem[], key: string) => buildRatingProfile(items).dimensions.find(value => value.key === key)!;

test('all integer ratings use backend direction boundaries; favorites/wanted/unrated watched do not imply preference', () => {
  const items = [...Array.from({ length: 10 }, (_, index) => movie(index + 1, index + 1, { genres: ['剧情'] })),
    movie(11, null, { genres: ['喜剧'] }), movie(12, null, { watch_state: 'watched', genres: ['动画'] })];
  const profile = buildRatingProfile(items);
  expect(profile).toMatchObject({ ratedCount: 10, unratedCount: 2, counts: { positive: 4, negative: 5, neutral: 1 } });
  expect(profile.neutral.map(value => value.rating)).toEqual([6]);
  expect(profile.dimensions[0].groups).toHaveLength(1);
  expect(profile.dimensions[0].groups[0]).toMatchObject({ label: '剧情', positive: expect.any(Array), negative: expect.any(Array) });
  expect(profile.dimensions[0].groups[0].positive.map(value => value.rating)).toEqual([7, 8, 9, 10]);
  expect(profile.dimensions[0].groups[0].negative.map(value => value.rating)).toEqual([1, 2, 3, 4, 5]);
});

test('metadata counting uses distinct movie evidence and keeps conflicting directions separate', () => {
  const items = [movie(1, 9, { genres: ['剧情', '剧情', ' 悬疑 ', ' '], director: '导演A', release_date: '1994-09-10', production_countries: ['US', 'US', 'GB'] }),
    movie(2, 2, { genres: ['剧情'], director: '导演A', release_date: '1999-12-31', production_countries: ['US'] }),
    movie(3, 6, { genres: ['动画'], director: '导演B', release_date: '2000-01-01', production_countries: ['JP'] })];
  const copy = JSON.stringify(items);
  const genre = dimension(items, 'genres');
  expect(genre.known).toEqual({ positive: 1, negative: 1, neutral: 1 });
  expect(genre.groups.find(value => value.label === '剧情')).toMatchObject({ positive: [{ rating: 9 }], negative: [{ rating: 2 }] });
  expect(genre.groups.map(value => value.label)).not.toContain('动画');
  expect(dimension(items, 'decade').groups[0]).toMatchObject({ label: '1990 年代', positive: [{ rating: 9 }], negative: [{ rating: 2 }] });
  expect(dimension(items, 'countries').groups.find(value => value.label === 'US')?.positive).toHaveLength(1);
  expect(dimension(items, 'director').groups[0].label).toBe('导演A');
  expect(JSON.stringify(items)).toBe(copy);
});

test('missing fields stay unknown; invalid dates and incomplete director credits are never invented', () => {
  const items = [movie(1, 9), movie(2, 4, { genres: [' '], director: '  ', release_date: '2023-02-29', production_countries: [] }),
    movie(3, 6, { release_date: 'not-a-date' }), movie(4, 7, { director: '导演甲 / 导演乙', release_date: '2000-02-29' })];
  expect(dimension(items, 'genres').missing).toHaveLength(4);
  const year = dimension(items, 'decade');
  expect(year.missing.map(value => value.movie.tmdb_id)).toEqual([1, 2, 3]);
  expect(year.groups[0].label).toBe('2000 年代');
  expect(dimension(items, 'director').groups.map(value => value.label)).toEqual(['导演甲 / 导演乙']);
});

test('all-neutral and empty lists have no directional groups; invalid numeric scores are ignored', () => {
  expect(buildRatingProfile([]).ratedCount).toBe(0);
  const items = [movie(1, 6, { genres: ['剧情'] }), ...[0, 11, 1.5, NaN, true, '9'].map((value, index) => movie(index + 2, value as number))];
  expect(buildRatingProfile(items)).toMatchObject({ ratedCount: 1, counts: { positive: 0, negative: 0, neutral: 1 } });
  expect(dimension(items, 'genres').groups).toEqual([]);
});

test('rerating, withdrawal, watch-state changes, removal and full clear derive from the current library', () => {
  const source = movie(1, null, { genres: ['剧情'] });
  let items = setMovieRating([source], source, 9);
  expect(buildRatingProfile(items).counts.positive).toBe(1);
  items = setMovieRating(items, source, 3);
  expect(buildRatingProfile(items).counts).toEqual({ positive: 0, negative: 1, neutral: 0 });
  items = setMovieRating(items, source, 6);
  expect(dimension(items, 'genres').groups).toEqual([]);
  items = setMovieRating(items, source, null);
  expect(buildRatingProfile(items).ratedCount).toBe(0);
  expect(items[0].watch_state).toBe('watched');
  items = updateMovieLibrary(setMovieRating(items, source, 10), source, 'want_to_watch');
  expect(buildRatingProfile(items).ratedCount).toBe(0);
  items = updateMovieLibrary(setMovieRating(items, source, 10), source, 'watched');
  expect(buildRatingProfile(items).ratedCount).toBe(0);
  items = setMovieRating(items, source, 9).filter(value => value.tmdb_id !== 1);
  expect(buildRatingProfile(items).ratedCount).toBe(0);
  expect(buildRatingProfile([]).dimensions.every(value => value.groups.length === 0)).toBe(true);
});

test('the display covers the whole library without changing the recommendation feedback limit', () => {
  const items = Array.from({ length: 201 }, (_, index) => movie(index + 1, 9, { genres: ['剧情'] }));
  const before = buildRatingFeedback(items);
  expect(buildRatingProfile(items).ratedCount).toBe(201);
  expect(dimension(items, 'genres').groups[0].positive).toHaveLength(201);
  expect(buildRatingFeedback(items)).toEqual(before);
  expect(before).toHaveLength(200);
});
