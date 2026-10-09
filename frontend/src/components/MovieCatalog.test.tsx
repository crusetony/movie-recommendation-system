import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { MovieCatalog } from './MovieCatalog.tsx';
import { apiService } from '../services/api.ts';
import { MovieCatalogQuery, MovieSummary } from '../types/index.ts';
import { useMovieLibrary } from '../services/useMovieLibrary.ts';
import { MOVIE_LIBRARY_STORAGE_KEY } from '../services/movieLibraryStorage.ts';

jest.mock('../services/api.ts', () => ({ apiService: { getMovieGenres: jest.fn(), browseMovies: jest.fn() } }));
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const movie: MovieSummary = {tmdb_id: 901, title: '核对电影', original_title: 'Fixture', genres: ['剧情'], overview: '公开简介', release_date: '1994-09-10'};
const page = (request: MovieCatalogQuery, movies = [movie]) => ({mode:request.mode,movies,page:request.page,total_pages:3,available_pages:3,total_results:45,has_next:request.page<3,pagination_limited:false});
let container: HTMLDivElement, root: Root;
const originalMedia = window.matchMedia;
const originalScroll = HTMLElement.prototype.scrollIntoView;
const scroll = jest.fn();
const onView = jest.fn();
const Harness = () => { const library = useMovieLibrary([]); return <MovieCatalog library={library.items} onViewMovie={onView} onToggle={library.toggle} onRatingChange={library.rate} />; };
const button = (name: string) => Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(value => value.textContent === name)!;
const click = async (name: string) => act(async () => button(name).click());
const change = async (label: string, value: string) => {
  const field = Array.from(container.querySelectorAll('label')).find(el => el.textContent?.startsWith(label))!;
  const element = document.getElementById(field.htmlFor)!;
  await act(async () => Simulate.change(element, {target:{value}} as any));
};
const submit = async () => act(async () => Simulate.submit(container.querySelector('form')!));
const select = async (label: string, value: string) => {
  const field = Array.from(container.querySelectorAll('label')).find(el => el.textContent === label)!;
  await act(async () => Simulate.mouseDown(document.getElementById(field.htmlFor)!, {button:0} as any));
  const option = Array.from(document.querySelectorAll<HTMLElement>('[role="option"]')).find(el => el.textContent === value)!;
  await act(async () => option.click());
};
beforeEach(async () => {
  window.localStorage.clear();
  window.matchMedia = jest.fn(() => ({ matches: false } as MediaQueryList));
  HTMLElement.prototype.scrollIntoView = scroll;
  (apiService.getMovieGenres as jest.Mock).mockResolvedValue([{id:18,name:'剧情'}]);
  (apiService.browseMovies as jest.Mock).mockImplementation(async request => page(request));
  container=document.createElement('div'); document.body.appendChild(container); root=createRoot(container);
  await act(async () => root.render(<Harness />));
});
afterEach(async () => { await act(async () => root.unmount());container.remove();jest.clearAllMocks();
  window.matchMedia = originalMedia; HTMLElement.prototype.scrollIntoView = originalScroll; });

test('opening does not search; title/year uses upstream pagination and a changed search resets to page one', async () => {
  expect(apiService.browseMovies).not.toHaveBeenCalled();
  await change('电影片名','  核对  '); await change('上映年份','1994'); await submit();
  expect(apiService.browseMovies).toHaveBeenLastCalledWith({mode:'title',query:'核对',year:1994,page:1},expect.any(AbortSignal));
  await click('下一页'); expect(apiService.browseMovies).toHaveBeenLastCalledWith({mode:'title',query:'核对',year:1994,page:2},expect.any(AbortSignal));
  await change('电影片名','新片名'); expect(container.textContent).toContain('片名“核对”');
  await submit(); expect(apiService.browseMovies).toHaveBeenLastCalledWith({mode:'title',query:'新片名',year:1994,page:1},expect.any(AbortSignal));
});

test('title and invalid year are rejected before requests; discover discards hidden title/year fields', async () => {
  await submit(); expect(apiService.browseMovies).not.toHaveBeenCalled();
  await change('电影片名','核对'); await change('上映年份','1800'); await submit();
  expect(apiService.browseMovies).not.toHaveBeenCalled(); expect(container.textContent).toContain('1880–2100');
  await click('按类型与年代浏览'); await submit();
  expect(apiService.browseMovies).toHaveBeenLastCalledWith({mode:'discover',page:1},expect.any(AbortSignal));
});

test('genre and decade selections are submitted together and preserved when paging', async () => {
  await click('按类型与年代浏览'); await select('电影类型','剧情'); await select('上映年代','1990–1999年'); await submit();
  expect(apiService.browseMovies).toHaveBeenLastCalledWith({mode:'discover',genre_id:18,decade:1990,page:1},expect.any(AbortSignal));
  expect(container.textContent).toContain('剧情 · 1990–1999年');
  await click('下一页');expect(apiService.browseMovies).toHaveBeenLastCalledWith({mode:'discover',genre_id:18,decade:1990,page:2},expect.any(AbortSignal));
});

test('results reuse details, library actions and half-star scores without hiding watched movies', async () => {
  await change('电影片名','核对'); await submit(); await click('核对电影');
  expect(onView).toHaveBeenCalledWith(movie);
  await click('收藏'); await click('想看');
  expect(JSON.parse(window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY)!).items[0]).toMatchObject({favorite:true,watch_state:'want_to_watch'});
  const radio = container.querySelector<HTMLInputElement>('input[value="4.5"]')!;
  await act(async () => Simulate.change(radio,{target:{value:'4.5',checked:true}} as any));
  expect(container.textContent).toContain('已看过'); expect(container.textContent).toContain('9 / 10');
  expect(container.querySelectorAll('.cinema-catalog-card')).toHaveLength(1);
  await click('清除评分');
  expect(JSON.parse(window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY)!).items[0]).toMatchObject({watch_state:'watched',personal_rating:null});
  expect(apiService.browseMovies).toHaveBeenCalledTimes(1);
});

test('failed pagination keeps the last successful range and retries exactly the failed request', async () => {
  await change('电影片名','核对'); await submit();
  (apiService.browseMovies as jest.Mock).mockRejectedValueOnce(new Error('TMDB超时'));
  await click('下一页'); expect(container.textContent).toContain('TMDB超时'); expect(container.textContent).toContain('第 1 / 3 页');
  expect(scroll).not.toHaveBeenCalled();
  await change('电影片名','未提交的新片名'); await click('重试');
  expect(apiService.browseMovies).toHaveBeenLastCalledWith({mode:'title',query:'核对',page:2},expect.any(AbortSignal));
  expect(container.textContent).toContain('第 2 / 3 页');
  expect(scroll).toHaveBeenCalledTimes(1);
  expect(scroll.mock.instances[0]).toBe(container.querySelector('.cinema-catalog-card'));
});

test('cancellation prevents late results from overwriting a newer search, and unmount aborts pending reads', async () => {
  let resolveOld: (value: any) => void = () => {};
  (apiService.browseMovies as jest.Mock).mockImplementationOnce(() => new Promise(resolve => {resolveOld=resolve;}));
  await change('电影片名','旧请求'); await submit();
  const oldSignal = (apiService.browseMovies as jest.Mock).mock.calls[0][1] as AbortSignal;
  await click('取消读取'); expect(oldSignal.aborted).toBe(true);
  await change('电影片名','新请求'); await submit();
  await act(async () => resolveOld(page({mode:'title',query:'旧请求',page:1},[{...movie,title:'旧结果'}])));
  expect(container.textContent).toContain('片名“新请求”');expect(container.textContent).not.toContain('旧结果');
  (apiService.browseMovies as jest.Mock).mockImplementationOnce(() => new Promise(() => {}));
  await submit();const signal=(apiService.browseMovies as jest.Mock).mock.calls[2][1] as AbortSignal;
  await act(async () => root.unmount());expect(signal.aborted).toBe(true);root=createRoot(container);
});

test('empty results stay explicit, terminal pagination disables next, and missing poster/metadata have fallbacks', async () => {
  (apiService.browseMovies as jest.Mock).mockResolvedValueOnce({...page({mode:'title',page:1}),movies:[],total_results:0,total_pages:0,available_pages:0,has_next:false});
  await change('电影片名','不存在');await submit();
  expect(container.textContent).toContain('没有找到电影');expect(button('下一页').disabled).toBe(true);
  (apiService.browseMovies as jest.Mock).mockResolvedValueOnce({...page({mode:'title',page:1}),movies:[{tmdb_id:902,title:'缺资料',original_title:''}]});
  await submit();expect(container.textContent).toContain('海报暂不可用');expect(container.textContent).toContain('暂无简介');
});

test.each([
  ['success', false, false, 'smooth'],
  ['reduced motion', true, false, 'auto'],
  ['empty page', false, true, null],
])('paging %s returns to the new first movie only after the response', async (_, reduced, empty, behavior) => {
  await change('电影片名','核对'); await submit();
  expect(scroll).not.toHaveBeenCalled();
  window.matchMedia = jest.fn(() => ({ matches: reduced } as MediaQueryList));
  let resolvePage!: (value: unknown) => void;
  (apiService.browseMovies as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { resolvePage = resolve; }));
  await click('下一页');
  expect(scroll).not.toHaveBeenCalled(); expect(container.textContent).toContain('第 1 / 3 页');
  await act(async () => resolvePage(page({mode:'title',query:'核对',page:2},empty ? [] : [{...movie,tmdb_id:902,title:'第二页首部'}])));
  if (empty) expect(scroll).not.toHaveBeenCalled();
  else {
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.instances[0]).toBe(container.querySelector('.cinema-catalog-card'));
    expect(scroll).toHaveBeenCalledWith({behavior,block:'start'});
    expect(document.activeElement).toBe(container.querySelector('.cinema-catalog-card'));
    expect(container.textContent).toContain('第二页首部');
  }
});
