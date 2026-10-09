import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { SystemStatus } from './SystemStatus.tsx';
import { SystemStatus as Status } from '../types/index.ts';

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const status = { status: 'ready', tmdb_reachable: true, parser_mode: 'llm+rules', cache_enabled: false,
  visual_model_enabled: false, visual_model_loaded: false, metrics: { configured_model: 'fixture' }, message: 'fixture ready' } as Status;
let root: Root, container: HTMLDivElement;
beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const open = () => act(async () => container.querySelector<HTMLButtonElement>('button')!.click());

test('healthy status starts compact and the dialog reports disabled cache/model honestly', async () => {
  const retry = jest.fn();
  await act(async () => root.render(<SystemStatus status={status} onRetry={retry} />));
  expect(container.textContent).toBe('系统就绪');
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  await open();
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.textContent).toContain('内存缓存未启用');
  expect(dialog.textContent).toContain('多模态未启用');
  await act(async () => Array.from(dialog.querySelectorAll('button')).find(el => el.textContent === '重新检测')!.click());
  expect(retry).toHaveBeenCalledTimes(1);
});

test('visual degradation stays identifiable without opening the dialog and its cause remains available', async () => {
  await act(async () => root.render(<SystemStatus status={{ ...status, visual_model_enabled: true, visual_fallback_reason: 'fixture failure' }} onRetry={jest.fn()} />));
  expect(container.textContent).toBe('有限模式');
  await open();
  expect(document.querySelector('[role="alert"]')?.textContent).toBe('fixture failure');
});


test('a failed refresh cannot keep claiming the stale healthy state', async () => {
  await act(async () => root.render(<SystemStatus status={status} error="读取失败" onRetry={jest.fn()} />));
  expect(container.textContent).toBe('系统异常');
  await open();
  expect(document.querySelector('[role="alert"]')?.textContent).toContain('以下为上次检测的信息');
});
