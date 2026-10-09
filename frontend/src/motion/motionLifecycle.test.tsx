import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { usePointerMotion } from './usePointerMotion.ts';
import { MotionReveal } from './MotionReveal.tsx';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let container: HTMLDivElement;
let root: Root;
let clock = 0;
let serial = 0;
let hidden = false;
let frames: Map<number, FrameRequestCallback>;
let observers: { callback: IntersectionObserverCallback; disconnect: jest.Mock }[];
let changeListeners: Set<EventListener>;
let media: MediaQueryList;
const originalMatchMedia = window.matchMedia;
const originalObserver = window.IntersectionObserver;
const hiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
const renderCount = jest.fn();
const Subject = () => {
  const ref = usePointerMotion<HTMLDivElement>();
  renderCount();
  return <div ref={ref}>poster</div>;
};
const advance = () => {
  clock += 1000 / 60;
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach((callback) => callback(clock));
};
const settle = () => { for (let i = 0; i < 120 && frames.size; i++) advance(); };
const pointer = (node: Element, type = 'pointermove', pointerType = 'mouse') => {
  const event = new Event(type);
  Object.defineProperties(event, { pointerType: { value: pointerType }, clientX: { value: 200 }, clientY: { value: 100 } });
  node.dispatchEvent(event);
};
const notifyMedia = () => changeListeners.forEach((callback) => callback(new Event('change')));

beforeEach(() => {
  clock = 0; serial = 0; hidden = false;
  frames = new Map(); observers = []; changeListeners = new Set(); renderCount.mockClear();
  media = { matches: true, addEventListener: (_: string, callback: EventListener) => changeListeners.add(callback),
    removeEventListener: (_: string, callback: EventListener) => changeListeners.delete(callback) } as unknown as MediaQueryList;
  window.matchMedia = jest.fn(() => media);
  window.IntersectionObserver = jest.fn((callback) => {
    const observer = { callback, observe: jest.fn(), disconnect: jest.fn() };
    observers.push(observer); return observer;
  }) as unknown as typeof IntersectionObserver;
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => { frames.set(++serial, callback); return serial; });
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => { frames.delete(id); });
  jest.spyOn(performance, 'now').mockImplementation(() => clock);
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove(); jest.restoreAllMocks();
  window.matchMedia = originalMatchMedia; window.IntersectionObserver = originalObserver;
  if (hiddenDescriptor) Object.defineProperty(document, 'hidden', hiddenDescriptor);
  else delete (document as any).hidden;
});
const mount = async () => {
  await act(async () => root.render(<React.StrictMode><Subject /></React.StrictMode>));
  const node = container.firstElementChild as HTMLDivElement;
  jest.spyOn(node, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 200, height: 100 } as DOMRect);
  return node;
};

test('pointer bursts share one RAF, stop when settled, and never rerender React each frame', async () => {
  const node = await mount();
  const renders = renderCount.mock.calls.length;
  expect(frames.size).toBe(0);
  pointer(node); pointer(node); pointer(node);
  expect(frames.size).toBe(1);
  settle();
  expect(frames.size).toBe(0);
  expect(node.style.getPropertyValue('--motion-x')).toBe('1.0000');
  expect(node.dataset.motionActive).toBe('false');
  pointer(node, 'pointerleave'); settle();
  expect(node.style.getPropertyValue('--motion-x')).toBe('0.0000');
  expect(node.style.getPropertyValue('--motion-engagement')).toBe('0.0000');
  expect(renderCount).toHaveBeenCalledTimes(renders);
});

test('touch and ineligible media never start pointer motion; changing media cancels a live frame', async () => {
  const node = await mount();
  pointer(node, 'pointermove', 'touch'); expect(frames.size).toBe(0);
  pointer(node); advance(); expect(frames.size).toBe(1);
  (media as any).matches = false; notifyMedia();
  expect(frames.size).toBe(0);
  expect(node.style.getPropertyValue('--motion-x')).toBe('0.0000');
  pointer(node); expect(frames.size).toBe(0);
});

test('offscreen, hidden document, native scroll, cancellation and unmount release animation work', async () => {
  const node = await mount();
  pointer(node); advance();
  const observer = observers[observers.length - 1];
  observer.callback([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver);
  expect(frames.size).toBe(0); expect(node.dataset.motionVisible).toBe('false');
  pointer(node); expect(frames.size).toBe(0);
  observer.callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
  pointer(node); advance(); hidden = true; document.dispatchEvent(new Event('visibilitychange'));
  expect(frames.size).toBe(0);
  hidden = false; document.dispatchEvent(new Event('visibilitychange'));
  pointer(node); advance(); window.dispatchEvent(new Event('scroll'));
  expect(frames.size).toBe(0);
  pointer(node); advance(); pointer(node, 'pointercancel'); expect(frames.size).toBe(0);
  pointer(node); await act(async () => root.unmount());
  expect(frames.size).toBe(0); expect(changeListeners.size).toBe(0);
  expect(observer.disconnect).toHaveBeenCalled();
  root = createRoot(container);
});

test('reveal waits for actual visibility, plays once and drops the observer; reduced motion stays readable', async () => {
  (media as any).matches = false; // This component asks for reduce, rather than no-preference.
  await act(async () => root.render(<MotionReveal><button>readable</button></MotionReveal>));
  const node = container.firstElementChild as HTMLDivElement;
  expect(node.classList.contains('is-entering')).toBe(false);
  const observer = observers[0];
  observer.callback([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver);
  expect(observer.disconnect).not.toHaveBeenCalled();
  observer.callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
  expect(node.classList.contains('is-entering')).toBe(true);
  expect(observer.disconnect).toHaveBeenCalledTimes(1);
  (media as any).matches = true; notifyMedia();
  expect(node.classList.contains('is-entering')).toBe(false);
  expect(node.textContent).toBe('readable');
});
