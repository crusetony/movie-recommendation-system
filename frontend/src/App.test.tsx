import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import App from './App.tsx';
import { apiService } from './services/api.ts';

jest.mock('./services/api.ts', () => ({ apiService: { getSystemStatus: jest.fn() } }));
jest.mock('./components/CinematicHero.tsx', () => ({ CinematicHero: ({ onStart }: any) => <header id="top"><button onClick={onStart}>开始选片</button></header> }));
jest.mock('./components/SystemStatus.tsx', () => ({ SystemStatus: () => <button aria-label="系统状态">状态</button> }));
jest.mock('./components/MovieRecommendation.tsx', () => ({ MovieRecommendation: () => {
  const [value, setValue] = require('react').useState('');
  return <input id="movie-query" aria-label="需求" value={value} onChange={e => setValue(e.target.value)} />;
} }));
jest.mock('./components/ExperimentEvaluation.tsx', () => ({ ExperimentEvaluation: () => {
  const [value, setValue] = require('react').useState('');
  return <input aria-label="未保存核对说明" value={value} onChange={e => setValue(e.target.value)} />;
} }));
jest.mock('./components/MovieCatalog.tsx', () => ({ MovieCatalog: () => {
  const [value, setValue] = require('react').useState('');
  return <input aria-label="搜索片名" value={value} onChange={e => setValue(e.target.value)} />;
} }));
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root, container: HTMLDivElement;
const originalMedia = window.matchMedia, originalScroll = HTMLElement.prototype.scrollIntoView;
const scroll = jest.fn();
const button = (name: string) => Array.from(container.querySelectorAll('button')).find(el => el.textContent === name)!;
const click = async (name: string) => act(async () => button(name).click());
const input = (name: string) => container.querySelector<HTMLInputElement>(`[aria-label="${name}"]`)!;
beforeEach(async () => {
  window.localStorage.clear();
  window.history.replaceState(null, '', '#workbench');
  (apiService.getSystemStatus as jest.Mock).mockResolvedValue({ status: 'ready' });
  window.matchMedia = jest.fn(() => ({ matches: false, addListener: jest.fn(), removeListener: jest.fn() } as unknown as MediaQueryList));
  HTMLElement.prototype.scrollIntoView = scroll; scroll.mockClear();
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  await act(async () => root.render(<App />));
});
afterEach(async () => { await act(async () => root.unmount()); container.remove();
  window.matchMedia = originalMedia; HTMLElement.prototype.scrollIntoView = originalScroll; jest.clearAllMocks(); });

test('navigation opens lazily and preserves both working forms across page switches', async () => {
  expect(input('未保存核对说明')).toBeNull();
  await act(async () => Simulate.change(input('需求'), { target: { value: '保留观影偏好' } } as any));
  await click('实验室');
  await act(async () => Simulate.change(input('未保存核对说明'), { target: { value: '未保存的候选核对' } } as any));
  await click('电影推荐'); expect(input('需求').value).toBe('保留观影偏好'); expect(document.activeElement).toBe(input('需求'));
  await click('实验室'); expect(input('未保存核对说明').value).toBe('未保存的候选核对');
  expect(getComputedStyle(container.querySelector<HTMLElement>('#experiment-panel')!).display).toBe('block');
  expect(getComputedStyle(container.querySelector<HTMLElement>('#recommend-panel')!).display).toBe('none');
  expect(apiService.getSystemStatus).toHaveBeenCalledTimes(1);
});

test('reduced motion uses instant navigation; missing status still has an experiment target', async () => {
  window.matchMedia = jest.fn(() => ({ matches: true, addListener: jest.fn(), removeListener: jest.fn() } as unknown as MediaQueryList));
  await click('电影搜索'); scroll.mockClear();
  await click('电影推荐'); expect(scroll).toHaveBeenLastCalledWith({ behavior: 'auto', block: 'start' });
  await act(async () => root.unmount()); root = createRoot(container);
  (apiService.getSystemStatus as jest.Mock).mockRejectedValue(new Error('状态不可用'));
  await act(async () => root.render(<App />)); await click('实验室');
  expect(document.activeElement?.id).toBe('experiment-panel'); expect(input('未保存核对说明')).not.toBeNull();
});

test('the independent search tab retains its draft and leaves recommendation input intact', async () => {
  expect(input('搜索片名')).toBeNull();
  await act(async () => Simulate.change(input('需求'), {target:{value:'继续保留需求'}} as any));
  await click('电影搜索');
  await act(async () => Simulate.change(input('搜索片名'), {target:{value:'搜索草稿'}} as any));
  await click('电影推荐');expect(input('需求').value).toBe('继续保留需求');
  await click('电影搜索');expect(input('搜索片名').value).toBe('搜索草稿');
});

test('history opens lazily without a request or modifying recommendation input and library storage',async()=>{
  expect(container.querySelector('#history-panel')).toBeNull();const before=window.localStorage.getItem('movie_recommendation_library');
  await act(async()=>Simulate.change(input('需求'),{target:{value:'未提交的新需求'}} as any));await click('推荐历史');
  expect(container.textContent).toContain('暂无推荐历史');expect(getComputedStyle(container.querySelector<HTMLElement>('#history-panel')!).display).toBe('block');
  await click('电影推荐');expect(input('需求').value).toBe('未提交的新需求');expect(apiService.getSystemStatus).toHaveBeenCalledTimes(1);
  expect(window.localStorage.getItem('movie_recommendation_library')).toBe(before);
});


test('home is separate and returning to work preserves drafts; hash navigation follows browser history', async () => {
  expect(container.querySelector('#top')).toBeNull();
  expect(container.querySelectorAll('[aria-label="功能页面"]')).toHaveLength(1);
  await act(async () => Simulate.change(input('需求'), { target: { value: '首页往返保留' } } as any));
  await act(async () => container.querySelector<HTMLAnchorElement>('.cinema-nav__brand')!.click());
  expect(window.location.hash).toBe('#top');
  expect(getComputedStyle(container.querySelector<HTMLElement>('#workbench')!).display).toBe('none');
  await click('开始选片');
  expect(window.location.hash).toBe('#workbench');
  expect(input('需求').value).toBe('首页往返保留');
  await act(async () => {
    window.history.replaceState(null, '', '#catalog');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(getComputedStyle(container.querySelector<HTMLElement>('#catalog-panel')!).display).toBe('block');
  expect(document.activeElement?.id).toBe('catalog-panel');
  await act(async () => root.unmount()); root = createRoot(container);
  await act(async () => root.render(<App />));
  expect(getComputedStyle(container.querySelector<HTMLElement>('#catalog-panel')!).display).toBe('block');
});

test('opening the root address shows the cinema home and Start enters the recommendation workspace', async () => {
  await act(async () => root.unmount()); root = createRoot(container);
  window.history.replaceState(null, '', '/');
  await act(async () => root.render(<App />));
  expect(container.querySelector('#top')).not.toBeNull();
  expect(getComputedStyle(container.querySelector<HTMLElement>('#workbench')!).display).toBe('none');
  await click('开始选片');
  expect(window.location.hash).toBe('#workbench');
  expect(getComputedStyle(container.querySelector<HTMLElement>('#workbench')!).display).toBe('block');
  expect(document.activeElement).toBe(input('需求'));
});

test('unknown hashes open the cinema home while explicit search links still open their destination', async () => {
  await act(async () => {
    window.history.replaceState(null, '', '#unknown');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(container.querySelector('#top')).not.toBeNull();
  await act(async () => {
    window.history.replaceState(null, '', '#catalog');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  expect(container.querySelector('#top')).toBeNull();
  expect(getComputedStyle(container.querySelector<HTMLElement>('#catalog-panel')!).display).toBe('block');
});
