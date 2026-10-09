import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { MovieLibrary } from './MovieLibrary.tsx';
import { MovieLibraryItem } from '../types/index.ts';
import { useMovieLibrary } from '../services/useMovieLibrary.ts';
import { MOVIE_LIBRARY_STORAGE_KEY, saveMovieLibrary } from '../services/movieLibraryStorage.ts';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const item = (id: number, rating: number | null, extra = {}): MovieLibraryItem => ({ tmdb_id: id,
  title: `核对影片${id}`, original_title: '', favorite: true, watch_state: rating === null ? 'want_to_watch' : 'watched',
  personal_rating: rating, genres: ['剧情'], updated_at: '2026-10-08', ...extra });
const onView = jest.fn();
const Harness = () => {
  const library = useMovieLibrary([]);
  return <MovieLibrary items={library.items} warning={library.warning} onViewMovie={onView}
    onToggle={library.toggle} onRatingChange={library.rate} onRemove={library.remove} onClear={library.clear} onRecommend={jest.fn()} />;
};
let container: HTMLDivElement, root: Root;
const profile = () => container.querySelector<HTMLElement>('.cinema-rating-profile')!;
const render = async (items: MovieLibraryItem[]) => {
  saveMovieLibrary(items);
  await act(async () => root.render(<Harness />));
};
const button = (name: string, scope: Element = container) => Array.from(scope.querySelectorAll<HTMLButtonElement>('button')).find(value => value.textContent === name)!;
const click = async (element: HTMLElement) => act(async () => element.click());
const card = (name: string) => Array.from(container.querySelectorAll<HTMLElement>('.cinema-library-card')).find(value => value.querySelector('.cinema-library-title')?.textContent === name)!;
const rate = async (name: string, score: number) => {
  const radio = card(name).querySelector<HTMLInputElement>(`input[value="${score / 2}"]`)!;
  await act(async () => Simulate.change(radio, { target: { value: String(score / 2), checked: true } } as any));
};
beforeEach(() => { window.localStorage.clear(); container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.clearAllMocks(); jest.restoreAllMocks(); });

test('starts compact, separates positive/negative evidence, marks single-film limits, and opens the existing details callback', async () => {
  await render([item(1, 9), item(2, 2), item(3, 6, { genres: ['动画'] }), item(4, null, { genres: ['喜剧'] })]);
  expect(profile().hasAttribute('open')).toBe(false);
  await click(profile().querySelector('summary')!);
  expect(profile().hasAttribute('open')).toBe(true);
  expect(profile().textContent).toContain('有效评分 3 部 · 正向 1 / 负向 1 / 中性 1');
  expect(profile().textContent).toContain('未评分 1 部不参与');
  expect(profile().textContent).toContain('正向 1 部 · 单部，样本不足');
  expect(profile().textContent).toContain('负向 1 部 · 单部，样本不足');
  expect(profile().querySelector('.cinema-profile-group__label')?.textContent).toBe('剧情');
  await click(profile().querySelector('.cinema-profile-group summary')!);
  await click(profile().querySelector<HTMLButtonElement>('[aria-label="查看评分来源：核对影片1，9分"]')!);
  expect(onView).toHaveBeenCalledWith(expect.objectContaining({ tmdb_id: 1, personal_rating: 9 }));
  await click(button('导演署名', profile()));
  expect(profile().textContent).toContain('导演署名资料缺失 / 无效：3 部');
  expect(profile().textContent).toContain('方向性证据不足');
});

test('rating edits and clear/removal update both the profile and saved library; search does not narrow the profile', async () => {
  await render([item(1, 9), item(2, 2)]);
  const input = container.querySelector<HTMLInputElement>('input')!;
  await act(async () => Simulate.change(input, { target: { value: '核对影片1' } } as any));
  expect(container.querySelectorAll('.cinema-library-card')).toHaveLength(1);
  expect(profile().textContent).toContain('有效评分 2 部');
  await rate('核对影片1', 3);
  expect(profile().textContent).toContain('正向 0 / 负向 2 / 中性 0');
  expect(JSON.parse(window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY)!).items.find((value: MovieLibraryItem) => value.tmdb_id === 1).personal_rating).toBe(3);
  await click(button('清除评分', card('核对影片1')));
  expect(profile().textContent).toContain('有效评分 1 部');
  await click(button('移除', card('核对影片1')));
  await click(document.querySelector<HTMLButtonElement>('[role="dialog"] button:last-child')!);
  expect(profile().textContent).toContain('有效评分 1 部');
  await act(async () => Simulate.change(input, { target: { value: '' } } as any));
  await click(button('移除', card('核对影片2')));
  await click(document.querySelector<HTMLButtonElement>('[role="dialog"] button:last-child')!);
  expect(profile().textContent).toContain('尚无有效评分，画像证据不足');
  expect(JSON.parse(window.localStorage.getItem(MOVIE_LIBRARY_STORAGE_KEY)!).items).toEqual([]);
});

test('neutral rerating removes directional evidence; watch changes and confirmed full clear withdraw the remaining profile', async () => {
  await render([item(1, 9), item(2, 2)]);
  await rate('核对影片1', 6);
  expect(profile().textContent).toContain('正向 0 / 负向 1 / 中性 1');
  await click(button('想看', card('核对影片2')));
  expect(profile().textContent).toContain('当前评分全为中性');
  await click(button('看过', card('核对影片1')));
  expect(profile().textContent).toContain('有效评分 0 部');
  const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
  await click(button('清空片单'));
  expect(container.querySelectorAll('.cinema-library-card')).toHaveLength(2);
  confirm.mockReturnValue(true);
  await click(button('清空片单'));
  expect(profile().textContent).toContain('有效评分 0 部');
  expect(container.querySelectorAll('.cinema-library-card')).toHaveLength(0);
});

test('large category sets remain compact and all groups can be reviewed', async () => {
  await render([item(1, 10, { genres: ['类型1', '类型2', '类型3', '类型4', '类型5'] })]);
  expect(profile().querySelectorAll('.cinema-profile-group')).toHaveLength(4);
  await click(button('查看全部 5 项', profile()));
  expect(profile().querySelectorAll('.cinema-profile-group')).toHaveLength(5);
  await click(button('收起更多条目', profile()));
  expect(profile().querySelectorAll('.cinema-profile-group')).toHaveLength(4);
});
