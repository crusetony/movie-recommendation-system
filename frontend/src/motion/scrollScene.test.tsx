import React, { act, useRef } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { sceneProgress, useScrollScene } from './useScrollScene.ts';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const originalMedia = window.matchMedia, originalIO = window.IntersectionObserver, originalResize = window.ResizeObserver;
const hiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
let container: HTMLDivElement, root: Root, node: HTMLDivElement;
let clock: number, serial: number, hidden: boolean, top: number, height: number;
let frames: Map<number, FrameRequestCallback>, listeners: Set<EventListener>, media: MediaQueryList;
let observe: IntersectionObserverCallback;
const disconnect = jest.fn(), renderCount = jest.fn();
const Subject = () => {
  const ref = useRef<HTMLDivElement>(null); useScrollScene(ref); renderCount(); return <div ref={ref}>首页内容</div>;
};
const scroll = () => window.dispatchEvent(new Event('scroll'));
const advance = () => { clock += 1000 / 60; const pending = [...frames.values()]; frames.clear(); pending.forEach(cb => cb(clock)); };
const settle = () => { for (let i = 0; i < 120 && frames.size; i++) advance(); };
const notify = () => listeners.forEach(cb => cb(new Event('change')));

beforeEach(async () => {
  clock = 0; serial = 0; hidden = false; top = 0; height = 900;
  frames = new Map(); listeners = new Set(); disconnect.mockClear(); renderCount.mockClear();
  media = { matches: true, addEventListener: (_: string, cb: EventListener) => listeners.add(cb),
    removeEventListener: (_: string, cb: EventListener) => listeners.delete(cb) } as unknown as MediaQueryList;
  window.matchMedia = jest.fn(() => media);
  window.IntersectionObserver = jest.fn(cb => { observe = cb; return { observe: jest.fn(), disconnect }; }) as unknown as typeof IntersectionObserver;
  window.ResizeObserver = undefined as any;
  jest.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => { frames.set(++serial, cb); return serial; });
  jest.spyOn(window, 'cancelAnimationFrame').mockImplementation(id => { frames.delete(id); });
  jest.spyOn(performance, 'now').mockImplementation(() => clock);
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  await act(async () => root.render(<React.StrictMode><Subject /></React.StrictMode>));
  node = container.firstElementChild as HTMLDivElement;
  jest.spyOn(node, 'getBoundingClientRect').mockImplementation(() => ({ top, height }) as DOMRect);
  window.dispatchEvent(new Event('resize'));
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); jest.restoreAllMocks();
  window.matchMedia = originalMedia; window.IntersectionObserver = originalIO; window.ResizeObserver = originalResize;
  if (hiddenDescriptor) Object.defineProperty(document, 'hidden', hiddenDescriptor); else delete (document as any).hidden;
});

test('hero offsets clamp at endpoints and safely reject invalid geometry', () => {
  expect(sceneProgress(100, 900, 900)).toBe(0); expect(sceneProgress(0, 900, 900)).toBe(0);
  expect(sceneProgress(-450, 900, 900)).toBe(.5); expect(sceneProgress(-1800, 900, 900)).toBe(1);
  expect(sceneProgress(0, 0, 900)).toBe(0); expect(sceneProgress(NaN, 900, 900)).toBe(0);
});

test('native scroll bursts share one frame, settle and never rerender React each frame', () => {
  const renders = renderCount.mock.calls.length;
  top = -450; scroll(); scroll(); scroll(); expect(frames.size).toBe(1); settle();
  expect(frames.size).toBe(0); expect(node.style.getPropertyValue('--scene-progress')).toBe('0.50000');
  expect(node.dataset.sceneActive).toBe('false'); expect(renderCount).toHaveBeenCalledTimes(renders);
  height = 600; window.dispatchEvent(new Event('resize'));
  expect(Number(node.style.getPropertyValue('--scene-progress'))).toBe(.75);
});

test('reduced motion and mobile eligibility cancel live work and retain readable content', () => {
  top = -900; scroll(); advance(); expect(frames.size).toBe(1);
  (media as any).matches = false; notify(); expect(frames.size).toBe(0);
  expect(node.style.getPropertyValue('--scene-progress')).toBe('0.00000'); expect(node.textContent).toBe('首页内容');
  scroll(); expect(frames.size).toBe(0); (media as any).matches = true; notify();
  expect(node.style.getPropertyValue('--scene-progress')).toBe('1.00000');
});

test('offscreen and hidden scenes stop; returning onscreen snaps to native position', () => {
  top = -450; scroll(); advance();
  observe([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver);
  expect(frames.size).toBe(0); top = -900; scroll(); expect(frames.size).toBe(0);
  observe([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
  expect(node.style.getPropertyValue('--scene-progress')).toBe('1.00000');
  top = 0; scroll(); advance(); hidden = true; document.dispatchEvent(new Event('visibilitychange'));
  expect(frames.size).toBe(0);
});

test('unmount removes observers, eligibility listeners and pending frames', async () => {
  top = -450; scroll(); expect(frames.size).toBe(1); await act(async () => root.unmount());
  expect(frames.size).toBe(0); expect(listeners.size).toBe(0); expect(disconnect).toHaveBeenCalled();
  root = createRoot(container);
});
