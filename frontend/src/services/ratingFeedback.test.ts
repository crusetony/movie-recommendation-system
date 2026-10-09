import { buildRatingFeedback } from './ratingFeedback.ts';
import { MovieLibraryItem } from '../types/index.ts';

const entry = (id: number, points: number | null): MovieLibraryItem => ({ tmdb_id: id, title: '测试电影', original_title: '',
  favorite: true, watch_state: 'watched', personal_rating: points, updated_at: `2026-10-08T00:00:00Z`, genres: ['科幻'] });

test('only explicit valid ratings form feedback; watched, favorites and zero are not inferred as preference', () => {
  const items = [entry(1, null), entry(2, 9), entry(3, 0), entry(4, 11), entry(5, 1), entry(6, 6)];
  expect(buildRatingFeedback(items)).toEqual([2, 5, 6].map(id => expect.objectContaining({
    schema_version: 2, action: 'rating', tmdb_id: id, rating: items.find(item => item.tmdb_id === id)!.personal_rating,
  })));
  expect(items.map(item => item.tmdb_id)).toEqual([1, 2, 3, 4, 5, 6]);
});

test('the most recent 200 profiles are sent; edits, clear and removal withdraw previous values', () => {
  const items = Array.from({ length: 210 }, (_, i) => entry(i + 1, 9));
  expect(buildRatingFeedback(items)).toHaveLength(200);
  expect(buildRatingFeedback(items)[0].tmdb_id).toBe(11);
  expect(buildRatingFeedback([entry(1, 2)])[0].rating).toBe(2);
  expect(buildRatingFeedback([entry(1, null)])).toEqual([]);
  expect(buildRatingFeedback([])).toEqual([]);
});
