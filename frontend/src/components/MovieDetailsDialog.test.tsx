import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { MovieDetailsDialog } from './MovieDetailsDialog.tsx';
import { apiService } from '../services/api.ts';
import { MovieDetails, MovieSummary } from '../types/index.ts';

jest.mock('../services/api.ts', () => ({ apiService: { getMovieDetails: jest.fn(), recommendMovies: jest.fn() } }));
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const summary: MovieSummary = { tmdb_id: 123, title: '测试电影甲', original_title: 'Fixture A', overview: '已有简介' };
const details: MovieDetails = { ...summary, poster_url: 'https://image.tmdb.org/t/p/w500/test.jpg', overview: '完整剧情简介', genres: ['剧情'],
  director: '导演甲', directors: ['导演甲'], cast: ['演员甲'], cast_roles: [{ name: '演员甲', character: '角色甲' }], keywords: [],
  production_countries: ['France'], spoken_languages: ['French'], tagline: '测试副标题', status: 'Released', tmdb_url: 'https://www.themoviedb.org/movie/123' };
let container: HTMLDivElement, root: Root;
const onClose = jest.fn(), onUseReference = jest.fn(), onLibraryToggle = jest.fn();
const originalMedia = window.matchMedia;
const render = (movie: MovieSummary | null = summary) => act(async () => root.render(<MovieDetailsDialog movie={movie}
  onClose={onClose} onUseReference={onUseReference} onLibraryToggle={onLibraryToggle} onRatingChange={jest.fn()} />));
const click = async (name: string) => act(async () => {
  const button = Array.from(document.querySelectorAll('button')).find(item => item.textContent === name || item.getAttribute('aria-label') === name)!;
  button.click();
});
beforeEach(() => {
  window.matchMedia = jest.fn(() => ({ matches: false, addListener: jest.fn(), removeListener: jest.fn(), addEventListener: jest.fn(), removeEventListener: jest.fn() } as unknown as MediaQueryList));
  (apiService.getMovieDetails as jest.Mock).mockResolvedValue(details);
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); window.matchMedia = originalMedia; jest.clearAllMocks(); });

test('requests only on open and displays full synopsis, crew and roles', async () => {
  await render(null); expect(apiService.getMovieDetails).not.toHaveBeenCalled();
  await render();
  expect(apiService.getMovieDetails).toHaveBeenCalledWith(123, expect.any(AbortSignal));
  expect(document.body.textContent).toContain('完整剧情简介');
  expect(document.body.textContent).toContain('导演甲');
  expect(document.body.textContent).toContain('角色甲');
  expect(document.querySelector('a')?.href).toBe(details.tmdb_url);
});

test('reference selection and collection use the exact movie and never start a recommendation', async () => {
  await render(); await click('收藏'); await click('设为参考电影');
  expect(onLibraryToggle).toHaveBeenCalledWith(details, 'favorite');
  expect(onUseReference).toHaveBeenCalledWith(details);
  expect(apiService.recommendMovies).not.toHaveBeenCalled();
});

test('request failure preserves existing metadata and supports an explicit retry', async () => {
  (apiService.getMovieDetails as jest.Mock).mockRejectedValueOnce(new Error('TMDB 暂时不可用'));
  await render();
  expect(document.body.textContent).toContain('TMDB 暂时不可用'); expect(document.body.textContent).toContain('已有简介');
  await click('重试'); expect(document.body.textContent).toContain('完整剧情简介'); expect(apiService.getMovieDetails).toHaveBeenCalledTimes(2);
});

test('switching movies aborts the first request and ignores its late response', async () => {
  let finishA!: (value: MovieDetails) => void;
  (apiService.getMovieDetails as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { finishA = resolve; }));
  await render(); const signal = (apiService.getMovieDetails as jest.Mock).mock.calls[0][1] as AbortSignal;
  const second = { ...details, tmdb_id: 456, title: '测试电影乙', overview: '乙的完整简介' };
  (apiService.getMovieDetails as jest.Mock).mockResolvedValueOnce(second);
  await render(second); expect(signal.aborted).toBe(true);
  await act(async () => finishA(details));
  expect(document.querySelector('#movie-detail-title')?.textContent).toBe('测试电影乙');
  expect(document.body.textContent).toContain('乙的完整简介'); expect(document.body.textContent).not.toContain('完整剧情简介');
});

test('closing aborts pending requests; a failed poster retains the details and actions', async () => {
  await render(); await act(async () => Simulate.error(document.querySelector('img')!));
  expect(document.body.textContent).toContain('海报暂不可用'); expect(document.body.textContent).toContain('角色甲');
  const signal = (apiService.getMovieDetails as jest.Mock).mock.calls[0][1] as AbortSignal;
  await click('关闭电影详情'); expect(onClose).toHaveBeenCalledTimes(1);
  await render(null); expect(signal.aborted).toBe(true);
});
