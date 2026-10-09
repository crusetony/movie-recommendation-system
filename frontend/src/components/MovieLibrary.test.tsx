import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { MovieLibrary } from './MovieLibrary.tsx';
import { MovieLibraryItem } from '../types/index.ts';
import { updateMovieLibrary } from '../services/movieLibraryStorage.ts';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const item: MovieLibraryItem = { tmdb_id: 123, title: '测试电影', original_title: 'Fixture', overview: '测试简介',
  favorite: true, watch_state: 'watched', personal_rating: 9, updated_at: '2026-10-08' };
let container: HTMLDivElement, root: Root;
const onRemove = jest.fn(), onToggle = jest.fn(), onRatingChange = jest.fn(), onClear = jest.fn();
const render = (items = [item]) => act(async () => root.render(<MovieLibrary items={items} warning={null}
  onViewMovie={jest.fn()} onToggle={onToggle} onRatingChange={onRatingChange} onRemove={onRemove} onClear={onClear} onRecommend={jest.fn()} />));
const button = (name: string) => Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(button => button.textContent === name)!;
beforeEach(async () => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); await render(); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.clearAllMocks(); jest.restoreAllMocks(); });

test('all-category removal requires confirmation, cancellation preserves the movie and a confirmed parent removal clears the card', async () => {
  await act(async () => button('移除').click());
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain('测试电影');
  expect(onRemove).not.toHaveBeenCalled();
  await act(async () => button('取消').click());
  expect(onRemove).not.toHaveBeenCalled();
  expect(container.querySelector('.cinema-library-card')).not.toBeNull();
  await act(async () => button('移除').click());
  await act(async () => button('确认移除').click());
  expect(onRemove).toHaveBeenCalledTimes(1); expect(onRemove).toHaveBeenCalledWith(123);
  expect(onToggle).not.toHaveBeenCalled();
  await render([]); expect(container.querySelector('.cinema-library-card')).toBeNull();
});

test('the library has no zero-score button and watched sits beside want-to-watch', () => {
  expect(Array.from(container.querySelectorAll('.cinema-library-actions button')).map(button => button.textContent)).toEqual(['收藏', '想看', '看过']);
  expect(button('0 分')).toBeUndefined(); expect(container.textContent).toContain('9 / 10');
});

test('title search filters cards without deleting saved records', async () => {
  const input = container.querySelector<HTMLInputElement>('input')!;
  await act(async () => Simulate.change(input, { target: { value: '不存在的片名' } } as any));
  expect(container.querySelector('.cinema-library-card')).toBeNull(); expect(onRemove).not.toHaveBeenCalled();
  await act(async () => Simulate.change(input, { target: { value: 'Fixture' } } as any));
  expect(container.querySelector('.cinema-library-card')).not.toBeNull();
});

test('clearing the entire library still requires confirmation, while individual actions remain direct', async () => {
  const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
  await act(async () => button('清空片单').click()); expect(onClear).not.toHaveBeenCalled();
  confirm.mockReturnValue(true); await act(async () => button('清空片单').click()); expect(onClear).toHaveBeenCalledTimes(1);
});

test.each([
  ['收藏', 'favorite', item, { favorite: false, watch_state: 'watched', personal_rating: 9 }],
  ['想看', 'want_to_watch', { ...item, watch_state: 'want_to_watch', personal_rating: null }, { favorite: true, watch_state: 'none', personal_rating: null }],
  ['看过', 'watched', item, { favorite: true, watch_state: 'none', personal_rating: null }],
])('removing from %s directly clears that mark and preserves the other library state', async (label, action, initial, expected) => {
  const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
  function StatefulLibrary() {
    const [saved, setSaved] = React.useState([initial as MovieLibraryItem]);
    return <><output aria-label="临时片单">{JSON.stringify(saved)}</output><MovieLibrary items={saved} warning={null}
      onViewMovie={jest.fn()} onToggle={(movie, toggleAction) => { onToggle(movie, toggleAction); setSaved(values => updateMovieLibrary(values, movie, toggleAction)); }}
      onRatingChange={onRatingChange} onRemove={onRemove} onClear={onClear} onRecommend={jest.fn()} /></>;
  }
  await act(async () => root.render(<StatefulLibrary />));
  await act(async () => Simulate.mouseDown(container.querySelector('[role="combobox"]')!, { button: 0 }));
  const options = Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'));
  expect(options.map(node => node.textContent)).toEqual(['全部', '收藏', '想看', '看过']);
  await act(async () => options.find(node => node.textContent === label)!.click());
  await act(async () => button('移除').click());
  expect(onToggle).toHaveBeenCalledWith(initial, action);
  expect(onRemove).not.toHaveBeenCalled(); expect(confirm).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(container.querySelector('.cinema-library-card')).toBeNull();
  const saved = JSON.parse(container.querySelector('output')!.textContent!);
  expect(saved).toHaveLength(1); expect(saved[0]).toMatchObject({ tmdb_id: 123, ...expected as object });
});
