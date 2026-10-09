import { MovieLibraryItem, SessionFeedback } from '../types/index.ts';

// Clearing/removing a library rating withdraws it from subsequent requests.
export const buildRatingFeedback = (library: MovieLibraryItem[]): SessionFeedback[] => library
  .filter(item => Number.isInteger(item.personal_rating) && item.personal_rating! >= 1 && item.personal_rating! <= 10)
  .sort((left, right) => left.updated_at.localeCompare(right.updated_at) || left.tmdb_id - right.tmdb_id)
  .slice(-200)
  .map(item => ({
    schema_version: 2,
    action: 'rating',
    rating: item.personal_rating!,
    tmdb_id: item.tmdb_id,
    timestamp: item.updated_at.slice(0, 64),
    title: item.title.slice(0, 300),
    overview: (item.overview || '').slice(0, 3000),
    genres: (item.genres || []).slice(0, 20),
    keywords: (item.keywords || []).slice(0, 30),
  }));
