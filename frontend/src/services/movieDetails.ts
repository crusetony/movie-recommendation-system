import { MovieDetails } from '../types/index.ts';

const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value)
  ? value as Record<string, unknown> : {};
const text = (value: unknown): string => typeof value === 'string' ? value.trim() : '';
const rows = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(object) : [];
const names = (value: unknown): string[] => rows(value).map(row => text(row.name)).filter(Boolean);
const number = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;

export const normalizeMovieDetails = (value: unknown, requestedId: number): MovieDetails => {
  const movie = object(value);
  if (movie.id !== requestedId || !Number.isInteger(requestedId) || requestedId <= 0) {
    throw new Error('电影详情身份不一致，请重试');
  }
  const credits = object(movie.credits);
  const directors = rows(credits.crew).filter(row => row.job === 'Director').map(row => text(row.name)).filter(Boolean);
  const cast = rows(credits.cast).slice(0, 20).map(row => ({ name: text(row.name), character: text(row.character) })).filter(row => row.name);
  const posterPath = text(movie.poster_path);
  return {
    tmdb_id: requestedId,
    title: text(movie.title) || text(movie.original_title) || '未知片名',
    original_title: text(movie.original_title),
    overview: text(movie.overview),
    poster_url: /^\/[a-zA-Z0-9_./-]+\.(jpg|jpeg|png|webp)$/i.test(posterPath)
      ? `https://image.tmdb.org/t/p/w500${posterPath}` : null,
    release_date: text(movie.release_date) || null,
    runtime: number(movie.runtime),
    vote_average: number(movie.vote_average),
    vote_count: number(movie.vote_count) ?? 0,
    genres: names(movie.genres),
    directors, director: directors.join('、') || null,
    cast: cast.map(row => row.name), cast_roles: cast,
    keywords: names(object(movie.keywords).keywords),
    production_countries: names(movie.production_countries),
    spoken_languages: rows(movie.spoken_languages).map(row => text(row.name) || text(row.english_name)).filter(Boolean),
    tagline: text(movie.tagline), status: text(movie.status),
    tmdb_url: `https://www.themoviedb.org/movie/${requestedId}`,
  };
};
