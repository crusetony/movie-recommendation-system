import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { STARTUP_FADE_MS, STARTUP_MAX_MS, STARTUP_MIN_MS, useStartupScreen } from './useStartupScreen.ts';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const originalMedia = window.matchMedia;
let root: Root, container: HTMLDivElement, poster: HTMLImageElement, screen: HTMLDivElement;
const Harness = () => <output>{useStartupScreen()}</output>;
const advance = async (ms: number) => act(async () => { jest.advanceTimersByTime(ms); });
const mount = async () => act(async () => { root.render(<React.StrictMode><Harness /></React.StrictMode>); });
beforeEach(() => {
  jest.useFakeTimers();
  window.matchMedia = jest.fn(() => ({ matches: false } as MediaQueryList));
  screen = document.createElement('div'); screen.id = 'cinema-startup'; document.body.appendChild(screen);
  const layer = document.createElement('div'); layer.className = 'cinema-poster-exhibit__layer is-active';
  poster = document.createElement('img'); Object.defineProperty(poster, 'complete', { value: false }); layer.appendChild(poster);
  container = document.createElement('div'); container.appendChild(layer); document.body.appendChild(container);
  // Keep the poster outside React's root so load/error events simulate a real asset.
  const host = document.createElement('div'); container.appendChild(host); root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); screen.remove();
  window.matchMedia = originalMedia; jest.useRealTimers();
});

test('loaded assets keep the opening beat, then fade once and remove the screen', async () => {
  await mount();
  await act(async () => { poster.dispatchEvent(new Event('load')); });
  await advance(STARTUP_MIN_MS - 1); expect(container.textContent).toBe('loading');
  await advance(1); expect(container.textContent).toBe('revealing'); expect(screen.dataset.state).toBe('leaving');
  await advance(STARTUP_FADE_MS); expect(container.textContent).toBe('ready'); expect(screen.isConnected).toBe(false);
  await advance(STARTUP_MAX_MS); expect(container.textContent).toBe('ready');
});

test('failed posters also release the screen instead of trapping the user', async () => {
  await mount();
  await act(async () => { poster.dispatchEvent(new Event('error')); });
  await advance(STARTUP_MIN_MS); await advance(STARTUP_FADE_MS);
  expect(container.textContent).toBe('ready'); expect(screen.isConnected).toBe(false);
});

test('a stalled asset cannot hold the application beyond the maximum wait', async () => {
  await mount(); await advance(STARTUP_MAX_MS - 1); expect(container.textContent).toBe('loading');
  await advance(1); expect(container.textContent).toBe('revealing');
  await advance(STARTUP_FADE_MS); expect(container.textContent).toBe('ready');
});

test('reduced motion skips the deliberate delay and animated exit', async () => {
  window.matchMedia = jest.fn(() => ({ matches: true } as MediaQueryList));
  await mount(); await act(async () => { poster.dispatchEvent(new Event('load')); });
  expect(container.textContent).toBe('ready'); expect(screen.isConnected).toBe(false);
});

test('unmount releases listeners and timers; navigation without a startup screen needs no second animation', async () => {
  await mount(); await act(async () => root.unmount());
  expect(jest.getTimerCount()).toBe(0);
  poster.dispatchEvent(new Event('load')); await advance(STARTUP_MAX_MS); expect(screen.isConnected).toBe(true);
  screen.remove(); root = createRoot(container);
  await mount(); expect(container.textContent).toBe('ready'); expect(jest.getTimerCount()).toBe(0);
});
