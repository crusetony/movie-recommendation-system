import {
  clearStoredFeedback,
  FEEDBACK_STORAGE_KEY,
  loadFeedback,
  saveFeedback,
} from './feedbackStorage.ts';
import { SessionFeedback } from '../types/index.ts';


const sample: SessionFeedback = {
  schema_version: 1,
  tmdb_id: 157336,
  action: 'like',
  timestamp: '2026-09-24T00:00:00.000Z',
  title: '星际穿越',
  overview: '关于宇宙探索与亲情。',
  genres: ['科幻', '剧情'],
  keywords: ['太空', '亲情'],
  model_version: null,
};

beforeEach(() => window.localStorage.clear());

test('persists and clears versioned anonymous feedback', () => {
  saveFeedback([sample]);

  expect(loadFeedback()).toEqual([sample]);
  clearStoredFeedback();
  expect(loadFeedback()).toEqual([]);
});

test('migrates the legacy bare-array format', () => {
  window.localStorage.setItem(
    FEEDBACK_STORAGE_KEY,
    JSON.stringify([{ ...sample, schema_version: undefined }]),
  );

  const migrated = loadFeedback();

  expect(migrated).toHaveLength(1);
  expect(migrated[0].schema_version).toBe(1);
  expect(JSON.parse(window.localStorage.getItem(FEEDBACK_STORAGE_KEY) || '{}')).toMatchObject({
    schema_version: 1,
  });
});

test('ignores and removes corrupted localStorage data', () => {
  window.localStorage.setItem(FEEDBACK_STORAGE_KEY, '{not-json');

  expect(loadFeedback()).toEqual([]);
  expect(window.localStorage.getItem(FEEDBACK_STORAGE_KEY)).toBeNull();
});
