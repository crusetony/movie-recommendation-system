import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { MovieResultCard } from './MovieResultCard.tsx';
import { MovieRecommendation } from '../types/index.ts';


(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
// Synthetic component fixture, never used as recommendation or evaluation data.
const movie = {
  tmdb_id: 100, title: '组件测试电影', original_title: 'Fixture',
  overview: '用于验证简介和海报失败后的内容保留。', poster_url: '/fixture.jpg',
  release_date: '2000-01-01', runtime: 90, vote_average: 7, vote_count: 100,
  genres: ['剧情'], director: '测试导演', tmdb_url: 'https://www.themoviedb.org/movie/100',
  reasoning: '测试推荐理由', recall_sources: ['测试来源'], rank_before_mmr: 2,
  rank_after_mmr: 1, diversity_penalty: 0, diversity_reason: '测试重排说明',
  score_breakdown: { final_score: 0.5, formula_version: 'test', components: [] },
} as MovieRecommendation;

let root: Root, container: HTMLDivElement;
const originalMedia = window.matchMedia;
const render = (value = movie) => act(async () => {
  root.render(<MovieResultCard movie={value} index={0} />);
});
beforeEach(async () => {
  window.matchMedia = jest.fn(() => ({
    matches: false, addEventListener: jest.fn(), removeEventListener: jest.fn(),
    addListener: jest.fn(), removeListener: jest.fn(),
  } as unknown as MediaQueryList));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  await render();
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); window.matchMedia = originalMedia;
});

test('personal stars replace the old feedback controls and forward the exact numeric rating', async () => {
  const onRatingChange = jest.fn();
  await act(async () => root.render(<MovieResultCard movie={movie} index={0} onRatingChange={onRatingChange} />));
  expect(container.querySelector('.cinema-movie-feedback')).toBeNull();
  for (const label of ['喜欢', '不喜欢', '不感兴趣', '0 分']) {
    expect(Array.from(container.querySelectorAll('button')).map(item => item.textContent)).not.toContain(label);
  }
  const nine = container.querySelector<HTMLInputElement>('input[value="4.5"]')!;
  await act(async () => Simulate.change(nine, { target: { value: '4.5' } } as any));
  expect(onRatingChange).toHaveBeenCalledWith(movie, 9);
});

test('a failed image keeps the synopsis and TMDB link; a replacement poster can load again', async () => {
  await act(async () => Simulate.error(container.querySelector('img')!));
  expect(container.textContent).toContain('海报暂不可用');
  expect(container.textContent).toContain(movie.overview);
  expect(container.querySelector('a')!.href).toBe(movie.tmdb_url);
  expect(container.querySelector('.cinema-movie-feedback')).toBeNull();
  await render({ ...movie, poster_url: '/replacement.jpg' });
  expect(container.querySelector('img')!.getAttribute('src')).toBe('/replacement.jpg');
});

test('watched sits immediately after want-to-watch and uses the library action, with no duplicate feedback button', async () => {
  const onLibraryToggle = jest.fn();
  await act(async () => root.render(<MovieResultCard movie={movie} index={0}
    onLibraryToggle={onLibraryToggle} />));
  const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>('.cinema-library-actions button'));
  expect(buttons.map(button => button.textContent)).toEqual(['收藏', '想看', '看过']);
  await act(async () => buttons[2].click());
  expect(onLibraryToggle).toHaveBeenCalledWith(movie, 'watched');
  expect(container.querySelector('.cinema-movie-feedback')).toBeNull();
});

test('a watched card stays visible with a prominent status, synopsis and details', async () => {
  await act(async () => root.render(<MovieResultCard movie={movie} index={0}
    libraryItem={{ ...movie, favorite: false, watch_state: 'watched', personal_rating: 9, updated_at: '2026-10-08' }} />));
  expect(container.querySelector('.cinema-movie-card')?.getAttribute('data-watched')).toBe('true');
  expect(container.querySelector('.cinema-watched-tag')?.textContent).toBe('已看过');
  expect(container.textContent).toContain(movie.overview);
  expect(container.querySelector('a')?.href).toBe(movie.tmdb_url);
});

test('empty synopsis has an honest placeholder and score details still expand after image failure', async () => {
  await render({ ...movie, overview: '', poster_url: null });
  expect(container.textContent).toContain('暂无中文简介');
  const score = container.querySelector('.cinema-score .MuiAccordionSummary-root') as HTMLButtonElement;
  expect(score.getAttribute('aria-expanded')).toBe('false');
  await act(async () => score.click());
  expect(score.getAttribute('aria-expanded')).toBe('true');
  expect(container.textContent).toContain('测试重排说明');
});


test('list synopsis can expand fully and the detail action receives the same movie', async () => {
  const onViewMovie = jest.fn();
  const long = { ...movie, overview: '完整的电影简介。'.repeat(60) };
  await act(async () => root.render(<MovieResultCard movie={long} index={0} onViewMovie={onViewMovie} />));
  const overview = container.querySelector('.cinema-movie-overview')!;
  expect(overview.getAttribute('data-expanded')).toBe('false');
  await act(async () => Array.from(container.querySelectorAll('button')).find(el => el.textContent === '展开简介')!.click());
  expect(overview.getAttribute('data-expanded')).toBe('true');
  expect(overview.textContent).toBe(long.overview);
  await act(async () => Array.from(container.querySelectorAll('button')).find(el => el.textContent === '电影详情 ↗')!.click());
  expect(onViewMovie).toHaveBeenCalledWith(long);
  expect(container.querySelector('a')).toBeNull();
});
