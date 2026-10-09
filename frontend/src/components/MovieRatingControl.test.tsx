import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { MovieRatingControl } from './MovieRatingControl.tsx';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let container: HTMLDivElement, root: Root;
const onChange = jest.fn();
const render = (rating: number | null = null) => act(async () => root.render(<MovieRatingControl
  movieId={123} title="测试电影" context="details" rating={rating} onChange={onChange} />));
beforeEach(async () => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); await render(); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); onChange.mockReset(); });

test.each([[0.5, 1], [1.5, 3], [4.5, 9], [5, 10]])('selecting %s stars gives %s / 10', async (stars, score) => {
  const radio = container.querySelector<HTMLInputElement>(`input[value="${stars}"]`)!;
  await act(async () => Simulate.change(radio, { target: { value: String(stars), checked: true } } as any));
  expect(onChange).toHaveBeenCalledWith(score);
  await render(score); expect(container.querySelector('.cinema-personal-rating__value')?.textContent).toBe(`${score} / 10`);
});

test('five stars have ten half-star choices and no zero-score button; clearing returns to unscored', async () => {
  expect(container.querySelectorAll('input[type="radio"]')).toHaveLength(11); // ten scores + empty
  expect(container.textContent).toContain('尚未评分');
  expect(Array.from(container.querySelectorAll('button')).some(button => button.textContent === '0 分')).toBe(false);
  await render(9);
  await act(async () => Array.from(container.querySelectorAll('button')).find(button => button.textContent === '清除评分')!.click());
  expect(onChange).toHaveBeenLastCalledWith(null); await render(null); expect(container.textContent).toContain('尚未评分');
});
