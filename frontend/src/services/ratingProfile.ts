import { MovieLibraryItem } from '../types/index.ts';

export type RatingDirection = 'positive' | 'negative' | 'neutral';
export type ProfileDimensionKey = 'genres' | 'director' | 'decade' | 'countries';
export interface RatingEvidence { movie: MovieLibraryItem; rating: number; direction: RatingDirection }
export interface PreferenceGroup { label: string; positive: RatingEvidence[]; negative: RatingEvidence[] }
export interface ProfileDimension {
  key: ProfileDimensionKey;
  label: string;
  known: Record<RatingDirection, number>;
  missing: RatingEvidence[];
  groups: PreferenceGroup[];
}

const labels = (values: string[] = []): string[] => [...new Set(values.map(value => value.trim()).filter(Boolean))];
const decade = (date?: string | null): string[] => {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || '');
  if (!match) return [];
  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText), month = Number(monthText), day = Number(dayText);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (year < 1000 || parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return [];
  return [`${Math.floor(year / 10) * 10} 年代`];
};
const definitions: Array<{ key: ProfileDimensionKey; label: string; values: (movie: MovieLibraryItem) => string[] }> = [
  { key: 'genres', label: '类型', values: movie => labels(movie.genres) },
  // Keep the stored credit as-is; the library does not retain all director IDs/credits.
  { key: 'director', label: '导演署名', values: movie => labels(movie.director ? [movie.director] : []) },
  { key: 'decade', label: '年代', values: movie => decade(movie.release_date) },
  { key: 'countries', label: '国家 / 地区', values: movie => labels(movie.production_countries) },
];

// A display-only projection of the current library, never persisted or sent to scoring.
// All library ratings are shown; the recommendation request has its own 200-item limit.
export const buildRatingProfile = (items: MovieLibraryItem[]) => {
  const rated: RatingEvidence[] = items
    .filter(item => Number.isInteger(item.personal_rating) && item.personal_rating! >= 1 && item.personal_rating! <= 10)
    .map(movie => ({ movie, rating: movie.personal_rating!, direction: movie.personal_rating! > 6 ? 'positive' as const
      : movie.personal_rating! < 6 ? 'negative' as const : 'neutral' as const }));
  const counts = { positive: 0, negative: 0, neutral: 0 };
  rated.forEach(evidence => counts[evidence.direction]++);
  const dimensions: ProfileDimension[] = definitions.map(({ key, label, values }) => {
    const known = { positive: 0, negative: 0, neutral: 0 };
    const missing: RatingEvidence[] = [];
    const groups = new Map<string, PreferenceGroup>();
    for (const evidence of rated) {
      const metadata = values(evidence.movie);
      if (!metadata.length) { missing.push(evidence); continue; }
      known[evidence.direction]++;
      if (evidence.direction === 'neutral') continue;
      for (const value of metadata) {
        const group = groups.get(value) || { label: value, positive: [], negative: [] };
        group[evidence.direction].push(evidence);
        groups.set(value, group);
      }
    }
    return { key, label, known, missing, groups: [...groups.values()].sort((left, right) =>
      (right.positive.length + right.negative.length) - (left.positive.length + left.negative.length)
      || left.label.localeCompare(right.label, 'zh-CN')) };
  });
  return { ratedCount: rated.length, unratedCount: items.length - rated.length, counts,
    neutral: rated.filter(evidence => evidence.direction === 'neutral'), dimensions };
};
