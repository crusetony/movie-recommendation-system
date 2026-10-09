import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { ExperimentEvaluation } from './ExperimentEvaluation.tsx';
import { apiService } from '../services/api.ts';

jest.mock('../services/api.ts', () => ({ apiService: {
  listExperimentResults: jest.fn(), listPerformanceResults: jest.fn(), getBenchmarkDataset: jest.fn(),
  getBenchmarkCandidates: jest.fn(), captureBenchmarkCandidates: jest.fn(), updateBenchmarkAnnotation: jest.fn(),
} }));

const mocked = apiService as unknown as Record<string, jest.Mock>;
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const record = (id: string) => ({ query_id: id, query: `${id} 温暖的电影`, query_type: '偏好', query_group: 'mood_subjective',
  expected_intent: {}, hard_constraints: {}, relevant_tmdb_ids: [], graded_relevance: {}, annotation_note: '',
  annotation_status: 'draft_needs_human_review', feedback: [] });
const pool = (id: string) => ({ query_id: id, available: true, reason: null, snapshot_id: `${id}-pool`, snapshot_sha256: 'a'.repeat(64),
  candidate_count: 1, candidates: [{ tmdb_id: 1, title: '真实测试候选', original_title: '', overview: '简介', genres: ['剧情'], keywords: [],
    production_countries: ['CN'], release_date: null, runtime: null, director: null, cast: [], poster_url: null, tmdb_url: 'https://www.themoviedb.org/movie/1' }] });
const deferred = () => {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise((success, failure) => { resolve = success; reject = failure; });
  return { promise, resolve, reject };
};
let container: HTMLDivElement;
let root: Root;
const originalMedia = window.matchMedia;
const originalScroll = HTMLElement.prototype.scrollIntoView;
const scroll = jest.fn();
const button = (name: string) => Array.from(document.querySelectorAll('button')).find((node) => node.textContent === name)!;
const click = async (node: HTMLElement) => { await act(async () => { node.click(); }); };
const field = (name: string) => {
  const label = Array.from(container.querySelectorAll('label')).find((node) => node.textContent === name)!;
  return document.getElementById(label.htmlFor) as HTMLInputElement;
};

beforeEach(async () => {
  jest.clearAllMocks();
  window.matchMedia = jest.fn(() => ({ matches: false } as MediaQueryList));
  HTMLElement.prototype.scrollIntoView = scroll;
  mocked.listExperimentResults.mockResolvedValue([]);
  mocked.listPerformanceResults.mockResolvedValue([]);
  mocked.getBenchmarkDataset.mockResolvedValue({ dataset_revision: 'b'.repeat(64),
    summary: { total_count: 80, verified_count: 1, machine_reviewed_count: 0, reviewed_count: 1, draft_count: 79,
      ready_for_formal_evaluation: false, ready_for_machine_evaluation: false,
      blockers: [], machine_blockers: [], machine_validated_bound_count: 1,
      group_counts: {}, verified_group_counts: {}, reviewed_group_counts: {} },
    records: [record('q1'), record('q2')] });
  mocked.getBenchmarkCandidates.mockImplementation(async (id) => ({ query_id: id, available: false, reason: '缺少快照', candidate_count: 0, candidates: [] }));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<React.StrictMode><ExperimentEvaluation /></React.StrictMode>); });
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  window.matchMedia = originalMedia;
  HTMLElement.prototype.scrollIntoView = originalScroll;
});

test('capture resolves to candidates, disables repeated clicks and never saves human labels', async () => {
  const pending = deferred();
  mocked.captureBenchmarkCandidates.mockReturnValue(pending.promise);
  await click(button('采集本条候选'));
  expect(button('正在采集本条候选…').disabled).toBe(true);
  await click(button('正在采集本条候选…'));
  expect(mocked.captureBenchmarkCandidates).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve({ ...pool('q1'), reused: false, elapsed_ms: 10, request_diagnostics: { tmdb_requests: 2 } }));
  expect(container.textContent).toContain('采集完成 · 1 部');
  expect(button('复用本条候选').disabled).toBe(false);
  expect(container.textContent).toContain('未标注（不等于 0 分）');
  expect(button('确认人工复核并标记已复核').disabled).toBe(true);
  expect(mocked.updateBenchmarkAnnotation).not.toHaveBeenCalled();
});

test('failed capture retains form and exposes a working retry', async () => {
  await act(async () => Simulate.change(field('核对说明'), { target: { value: '未保存说明' } } as any));
  mocked.captureBenchmarkCandidates.mockRejectedValueOnce(new Error('TMDB暂时不可用')).mockResolvedValueOnce({
    ...pool('q1'), reused: true, elapsed_ms: 2, request_diagnostics: {} });
  await click(button('采集本条候选'));
  await click(button('确认继续'));
  expect(container.textContent).toContain('TMDB暂时不可用');
  expect(field('核对说明').value).toBe('未保存说明');
  await click(button('重试采集本条候选'));
  await click(button('确认继续'));
  expect(container.textContent).toContain('复用已有候选 · 1 部');
  expect(field('核对说明').value).toBe('未保存说明');
  expect(mocked.updateBenchmarkAnnotation).not.toHaveBeenCalled();
});

test('cancelled query switch keeps unsaved text; confirmed switch discards it', async () => {
  await act(async () => Simulate.change(field('核对说明'), { target: { value: '尚未保存' } } as any));
  const select = container.querySelector('[role="combobox"]')!;
  await act(async () => Simulate.mouseDown(select, { button: 0 }));
  await click(Array.from(document.querySelectorAll('[role="option"]')).find((node) => node.textContent?.startsWith('q2')) as HTMLElement);
  expect(document.body.textContent).toContain('当前有未保存的标注内容');
  await click(button('取消'));
  expect(field('核对说明').value).toBe('尚未保存');
  await act(async () => Simulate.mouseDown(select, { button: 0 }));
  await click(Array.from(document.querySelectorAll('[role="option"]')).find((node) => node.textContent?.startsWith('q2')) as HTMLElement);
  await click(button('确认继续'));
  expect(container.textContent).toContain('q2 温暖的电影');
  expect(field('核对说明').value).toBe('');
});

test('pending read blocks switching and a late response cannot update a remounted workbench', async () => {
  const late = deferred();
  mocked.getBenchmarkCandidates.mockImplementation((id) => id === 'q2' ? late.promise
    : Promise.resolve({ query_id: id, available: false, reason: '缺少快照', candidate_count: 0, candidates: [] }));
  const select = container.querySelector('[role="combobox"]')!;
  await act(async () => Simulate.mouseDown(select, { button: 0 }));
  await click(Array.from(document.querySelectorAll('[role="option"]')).find((node) => node.textContent?.startsWith('q2')) as HTMLElement);
  expect(select.getAttribute('aria-disabled')).toBe('true');
  await act(async () => root.unmount());
  root = createRoot(container);
  await act(async () => root.render(<ExperimentEvaluation />));
  await act(async () => late.resolve(pool('q2')));
  expect(container.textContent).toContain('q1 温暖的电影');
  expect(container.textContent).not.toContain('q2-pool');
  expect(container.textContent).not.toContain('真实测试候选');
});

test('a changed pool clears only form grades after successful capture', async () => {
  mocked.getBenchmarkCandidates.mockResolvedValue(pool('q2'));
  const select = container.querySelector('[role="combobox"]')!;
  await act(async () => Simulate.mouseDown(select, { button: 0 }));
  await click(Array.from(document.querySelectorAll('[role="option"]')).find((node) => node.textContent?.startsWith('q2')) as HTMLElement);
  await click(button('2 · 高度相关'));
  expect(field('相关 TMDB ID').value).toBe('1');
  const pending = deferred();
  mocked.captureBenchmarkCandidates.mockReturnValue(pending.promise);
  await click(button('重新采集并重新核对'));
  await click(button('确认继续'));
  expect(field('相关 TMDB ID').value).toBe('1');
  await act(async () => pending.resolve({ ...pool('q2'), snapshot_id: 'q2-new-pool', reused: false, elapsed_ms: 2, request_diagnostics: {} }));
  expect(field('相关 TMDB ID').value).toBe('');
  expect(field('分级相关性 JSON').value).toBe('{}');
  expect(container.textContent).toContain('候选池已更换，表单等级已清空');
  expect(mocked.updateBenchmarkAnnotation).not.toHaveBeenCalled();
});

const prepareAnnotation = async () => {
  mocked.getBenchmarkCandidates.mockResolvedValue(pool('q2'));
  await act(async () => Simulate.mouseDown(container.querySelector('[role="combobox"]')!, { button: 0 }));
  await click(Array.from(document.querySelectorAll('[role="option"]')).find(node => node.textContent?.startsWith('q2')) as HTMLElement);
  await click(button('2 · 高度相关'));
  await act(async () => Simulate.change(field('核对说明'), { target: { value: '仅测试夹具，不是真实标注' } } as any));
  const dataset = await mocked.getBenchmarkDataset.mock.results[0].value;
  return { ...dataset, dataset_revision: 'c'.repeat(64), records: dataset.records.map((item: any) => item.query_id === 'q2'
    ? { ...item, annotation_status: 'verified', graded_relevance: { '1': 2 }, relevant_tmdb_ids: [1],
      annotation_note: '仅测试夹具，不是真实标注', annotation_snapshot_id: 'q2-pool', annotation_snapshot_sha256: 'a'.repeat(64) }
    : item) };
};

test.each([
  ['human review', true, false, 'smooth'],
  ['reduced motion human review', true, true, 'auto'],
  ['draft', false, false, null],
])('%s returns to progress only after a successful confirmed save', async (_, verified, reduced, behavior) => {
  const updated = await prepareAnnotation();
  window.matchMedia = jest.fn(() => ({ matches: reduced } as MediaQueryList));
  const pending = deferred();
  mocked.updateBenchmarkAnnotation.mockReturnValue(pending.promise);
  await click(button(verified ? '确认人工复核并标记已复核' : '保存标注草稿'));
  expect(scroll).not.toHaveBeenCalled();
  if (verified) {
    expect(mocked.updateBenchmarkAnnotation).not.toHaveBeenCalled();
    await click(button('确认继续'));
  }
  expect(scroll).not.toHaveBeenCalled();
  expect(mocked.updateBenchmarkAnnotation).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(updated));
  if (verified) {
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.instances[0]).toBe(container.querySelector('#annotation-overview'));
    expect(scroll).toHaveBeenCalledWith({ behavior, block: 'start' });
    expect(document.activeElement?.id).toBe('annotation-overview');
  } else expect(scroll).not.toHaveBeenCalled();
});

test('a cancelled or failed human save stays at the form and retains annotation text', async () => {
  await prepareAnnotation();
  await click(button('确认人工复核并标记已复核'));
  await click(button('取消'));
  expect(mocked.updateBenchmarkAnnotation).not.toHaveBeenCalled();
  expect(scroll).not.toHaveBeenCalled();
  mocked.updateBenchmarkAnnotation.mockRejectedValue(new Error('数据版本已变化'));
  await click(button('确认人工复核并标记已复核'));
  await click(button('确认继续'));
  expect(container.textContent).toContain('数据版本已变化');
  expect(field('核对说明').value).toBe('仅测试夹具，不是真实标注');
  expect(scroll).not.toHaveBeenCalled();
});
