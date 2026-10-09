import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { HeroPosters } from './HeroPosters.tsx';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root, container: HTMLDivElement;
const originalMedia = window.matchMedia;
const button = (index: number) => container.querySelectorAll('button')[index];
const link = () => container.querySelector('a')!;
beforeEach(async () => {
  window.matchMedia = jest.fn(() => ({ matches: false, addEventListener: jest.fn(), removeEventListener: jest.fn() } as unknown as MediaQueryList));
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  await act(async () => root.render(<HeroPosters />));
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); window.matchMedia = originalMedia; });

test('Taxi Driver is the default; clicking switches true TMDB identities, including the original Blade Runner', async () => {
  expect(link().getAttribute('href')).toBe('https://www.themoviedb.org/movie/103');
  expect(link().getAttribute('aria-label')).toContain('出租车司机（1976）');
  await act(async () => button(1).click());
  expect(link().getAttribute('href')).toBe('https://www.themoviedb.org/movie/18329');
  await act(async () => button(2).click());
  expect(link().getAttribute('href')).toBe('https://www.themoviedb.org/movie/78');
  expect(link().getAttribute('aria-label')).toContain('银翼杀手（1982）');
  expect(button(2).getAttribute('aria-pressed')).toBe('true');
});

test('keyboard focus previews a film; touch entry alone does not accidentally switch it', async () => {
  await act(async () => Simulate.pointerEnter(button(1), { pointerType: 'touch' }));
  expect(link().getAttribute('href')).toBe('https://www.themoviedb.org/movie/103');
  await act(async () => Simulate.focus(button(1)));
  expect(link().getAttribute('href')).toBe('https://www.themoviedb.org/movie/18329');
});

test('a failed poster keeps its movie identity and working detail link', async () => {
  await act(async () => Simulate.error(container.querySelectorAll('img')[0]));
  expect(container.querySelectorAll('img')).toHaveLength(2); expect(container.textContent).toContain('海报暂时无法加载');
  expect(link().getAttribute('href')).toBe('https://www.themoviedb.org/movie/103');
  await act(async () => button(1).click()); expect(container.querySelectorAll('img')).toHaveLength(2);
});

test('the app can open local details with the selected exact TMDB id, without selecting a recommendation reference', async () => {
  const onViewMovie = jest.fn();
  await act(async () => root.render(<HeroPosters onViewMovie={onViewMovie} />));
  await act(async () => Simulate.pointerEnter(button(2), { pointerType: 'mouse' }));
  expect(onViewMovie).not.toHaveBeenCalled();
  await act(async () => Simulate.click(link()));
  expect(onViewMovie).toHaveBeenCalledTimes(1);
  expect(onViewMovie).toHaveBeenCalledWith(expect.objectContaining({ tmdb_id: 78, title: '银翼杀手', release_date: '1982' }));
});

test.each([
  [0, 103, '出租车司机'], [1, 18329, '春光乍泄'], [2, 78, '银翼杀手'],
])('clicking title row %i opens that exact movie immediately, including repeated clicks', async (index, id, title) => {
  const onViewMovie = jest.fn();
  await act(async () => root.render(<HeroPosters onViewMovie={onViewMovie} />));
  // Click a different row without a preceding hover, as on a touch screen.
  await act(async () => button(Number(index)).click());
  expect(onViewMovie).toHaveBeenCalledTimes(1);
  expect(onViewMovie).toHaveBeenLastCalledWith(expect.objectContaining({tmdb_id:id,title}));
  expect(link().getAttribute('href')).toBe(`https://www.themoviedb.org/movie/${id}`);
  expect(button(Number(index)).getAttribute('aria-haspopup')).toBe('dialog');
  await act(async () => button(Number(index)).click());
  expect(onViewMovie).toHaveBeenCalledTimes(2);
  expect(onViewMovie).toHaveBeenLastCalledWith(expect.objectContaining({tmdb_id:id,title}));
});
