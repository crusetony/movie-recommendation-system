import React, { act } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { Simulate } from 'react-dom/test-utils';
import { MovieRecommendation } from './MovieRecommendation.tsx';
import { apiService } from '../services/api.ts';
import { MovieLibraryItem, MovieSummary, RecommendationResponse } from '../types/index.ts';
import { historyEntryFixture, historyResponseFixture } from '../testFixtures/recommendationHistory.ts';
import { moviePromptIdeas } from '../data/moviePromptIdeas.ts';

jest.mock('../services/api.ts', () => ({ apiService: {
  recommendMovies: jest.fn(), recommendMoviesMultimodal: jest.fn(), searchMovies: jest.fn(),
} }));
jest.mock('./MovieResultCard.tsx', () => ({ MovieResultCard: ({ movie, onViewMovie, onLibraryToggle, onRatingChange }: any) => <>
  <button onClick={() => onViewMovie(movie)}>详情{movie.tmdb_id}</button><button onClick={() => onLibraryToggle(movie, 'watched')}>看过{movie.tmdb_id}</button><button onClick={() => onRatingChange(movie, 9)}>评分{movie.tmdb_id}</button></> }));
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const movie: MovieSummary = { tmdb_id: 123, title: '测试电影', original_title: 'Fixture' };
const response = {
  success:true,user_query:'保留我的需求',
  recommendations: [movie, { ...movie, tmdb_id: 456 }], total_found: 2, has_more: true, next_discover_page: 2, warnings: [],
  diagnostics: { duration_ms: 10, llm_calls: 0, llm_tokens: 0, tmdb_requests: 0, configured_model: 'test', semantic_mode: 'lexical_fallback',
    visual_mode: 'unavailable', text_vector_cache_hits: 0, text_vector_cache_misses: 0, diversity_mode: 'disabled', feedback_count: 0 },
  query_analysis: { genres: [], excluded_genres: [], origin_countries: [], original_languages: [], moods: [], keywords: [], themes: [],
    preferred_aspects: [], disliked_aspects: [], director_names: [], cast_names: [], parser: 'rules', input_mode: 'preference' },
} as unknown as RecommendationResponse;
const watched = { ...movie, favorite: false, watch_state: 'watched', updated_at: '2026-10-08' } as MovieLibraryItem;
const props = { systemReady: true, library: [], onLibraryToggle: jest.fn(),
  onRatingChange: jest.fn(), onViewMovie: jest.fn(), referenceRequest: null, onReferenceHandled: jest.fn(), onBusyChange: jest.fn() };
let container: HTMLDivElement, root: Root;
const originalMedia = window.matchMedia, originalScroll = HTMLElement.prototype.scrollIntoView;
const render = (overrides: Partial<React.ComponentProps<typeof MovieRecommendation>> = {}) => act(async () => root.render(<MovieRecommendation {...props} {...overrides} />));
const query = () => container.querySelector<HTMLTextAreaElement>('#movie-query')!;
const submit = () => act(async () => Simulate.submit(container.querySelector('form')!));
const button = (label: string) => Array.from(container.querySelectorAll('button')).find(item => item.textContent === label)!;
beforeEach(async () => {
  window.matchMedia = jest.fn(() => ({ matches: false, addListener: jest.fn(), removeListener: jest.fn() } as unknown as MediaQueryList));
  HTMLElement.prototype.scrollIntoView = jest.fn();
  (apiService.recommendMovies as jest.Mock).mockResolvedValue(response);
  (apiService.recommendMoviesMultimodal as jest.Mock).mockResolvedValue(response);
  (apiService.searchMovies as jest.Mock).mockResolvedValue([]);
  container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
  await render(); await act(async () => Simulate.change(query(), { target: { value: '保留我的需求' } } as any));
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); window.matchMedia = originalMedia;
  HTMLElement.prototype.scrollIntoView = originalScroll; jest.clearAllMocks(); jest.restoreAllMocks(); });

test('watched ids are excluded in the next request; returned cards stay visible and favorites do not become feedback', async () => {
  await render({ library: [watched, { ...watched, tmdb_id: 789, favorite: true, watch_state: 'none' }] });
  await submit();
  expect(apiService.recommendMovies).toHaveBeenCalledWith(expect.objectContaining({ exclude_movie_ids: [123], discover_page: 1, feedback: [] }));
  expect(button('详情123')).toBeTruthy(); expect(button('详情456')).toBeTruthy();
  expect(container.textContent).toContain('实时推荐结果（2 部）');
});

test('setting a reference preserves the text and starts no request; refresh resets pagination for a new reference', async () => {
  await submit();
  await act(async () => button('换一批').click());
  expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({ discover_page: 2, exclude_movie_ids: [123, 456], force_refresh: false }));
  await render({ referenceRequest: movie }); await render();
  expect(query().value).toBe('保留我的需求'); expect(apiService.recommendMoviesMultimodal).not.toHaveBeenCalled();
  await act(async () => button('换一批').click());
  expect(apiService.recommendMoviesMultimodal).toHaveBeenCalledWith(expect.objectContaining({ reference_movie_id: 123, discover_page: 1, exclude_movie_ids: [] }));
});

test('every main submission starts a fresh recommendation and keeps history passive', async () => {
  const save = jest.fn();
  await render({ onSaveHistory: save });
  await submit(); await submit();
  expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({
    query: '保留我的需求', discover_page: 1, exclude_movie_ids: [], force_refresh: true,
  }));
  expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ is_refresh: false }), response);
  await act(async () => Simulate.change(query(), { target: { value: '换成父女亲情电影' } } as any));
  await submit();
  expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({
    query: '换成父女亲情电影', discover_page: 1, exclude_movie_ids: [], force_refresh: true,
  }));
});

test('a failed repeat submission does not advance or forget the last successful batch', async () => {
  await submit();
  (apiService.recommendMovies as jest.Mock).mockRejectedValueOnce(new Error('temporary failure'));
  await submit();
  expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({
    discover_page: 1, exclude_movie_ids: [], force_refresh: true,
  }));
  await act(async () => button('换一批').click());
  expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({
    discover_page: 2, exclude_movie_ids: [123, 456], force_refresh: false,
  }));
});

test('random inspiration fills a new editable prompt without submitting and the next recommendation starts fresh', async () => {
  const save = jest.fn();
  jest.spyOn(Math, 'random').mockReturnValue(0);
  await render({ onSaveHistory: save, library: [watched] });
  const inspiration = () => Array.from(container.querySelectorAll('button')).find(item => item.textContent?.startsWith('随机灵感'))!;
  expect(container.querySelectorAll('.cinema-inspiration')).toHaveLength(1);
  for (const oldLabel of ['一场烧脑悬疑', '温柔的治愈时刻', '去往遥远的星球']) expect(container.textContent).not.toContain(oldLabel);
  let previous = query().value;
  for (let index = 0; index < 4; index += 1) {
    const calls = (apiService.recommendMovies as jest.Mock).mock.calls.length;
    await act(async () => inspiration().click());
    expect(query().value).not.toBe(previous);
    expect(moviePromptIdeas).toContain(query().value);
    expect(document.activeElement).toBe(query());
    expect(apiService.recommendMovies).toHaveBeenCalledTimes(calls);
    previous = query().value;
    await submit();
    expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({
      query: query().value, discover_page: 1, exclude_movie_ids: [123], force_refresh: true,
    }));
    expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ is_refresh: false, excluded_count: 1 }), response);
  }
  expect(save).toHaveBeenCalledTimes(4);
  expect((apiService.recommendMovies as jest.Mock).mock.calls[0][0].query)
    .toBe((apiService.recommendMovies as jest.Mock).mock.calls[2][0].query);
  await act(async () => Simulate.change(query(), { target: { value: '我自己修改后的需求' } } as any));
  expect(query().value).toBe('我自己修改后的需求');
});

test('replacing an existing reference requires confirmation and cancel keeps the prior exact id', async () => {
  await render({ referenceRequest: movie }); await render();
  const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false);
  await render({ referenceRequest: { ...movie, tmdb_id: 789, title: '测试电影乙' } }); await render();
  expect(confirm).toHaveBeenCalledTimes(1); await submit();
  expect(apiService.recommendMoviesMultimodal).toHaveBeenCalledWith(expect.objectContaining({ reference_movie_id: 123 }));
});

test('watching and rating forward library actions and keep all current cards', async () => {
  await submit(); await act(async () => button('看过123').click());
  expect(props.onLibraryToggle).toHaveBeenCalledWith(movie, 'watched');
  await act(async () => button('评分123').click());
  expect(props.onRatingChange).toHaveBeenCalledWith(movie, 9);
  await render({ library: [{ ...watched, personal_rating: 9 }] });
  expect(button('详情123')).toBeTruthy(); expect(button('详情456')).toBeTruthy();
});

test('numeric ratings reach both text and multimodal requests, edits and clearing withdraw the old value', async () => {
  await render({ library: [{ ...watched, personal_rating: 9 }] }); await submit();
  expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({ feedback: [expect.objectContaining({
    schema_version: 2, action: 'rating', tmdb_id: 123, rating: 9,
  })] }));
  await render({ library: [{ ...watched, personal_rating: 2 }], referenceRequest: { ...movie, tmdb_id: 789 } });
  await render({ library: [{ ...watched, personal_rating: 2 }] }); await submit();
  expect(apiService.recommendMoviesMultimodal).toHaveBeenLastCalledWith(expect.objectContaining({ reference_movie_id: 789,
    feedback: [expect.objectContaining({ action: 'rating', rating: 2 })], exclude_movie_ids: [123], force_refresh: true }));
  await render({ library: [{ ...watched, personal_rating: null }] }); await submit();
  expect(apiService.recommendMoviesMultimodal).toHaveBeenLastCalledWith(expect.objectContaining({ feedback: [], exclude_movie_ids: [123] }));
  await render({ library: [] }); await submit();
  expect(apiService.recommendMoviesMultimodal).toHaveBeenLastCalledWith(expect.objectContaining({ feedback: [], exclude_movie_ids: [] }));
});

test('only successful completed requests enter history, including refresh request scope',async()=>{
  const save=jest.fn();await render({onSaveHistory:save});expect(save).not.toHaveBeenCalled();
  await submit();expect(save).toHaveBeenLastCalledWith({query:'保留我的需求',reference:{kind:'none'},discover_page:1,is_refresh:false,excluded_count:0,rating_count:0},response);
  await act(async()=>button('换一批').click());expect(save).toHaveBeenLastCalledWith(expect.objectContaining({discover_page:2,is_refresh:true,excluded_count:2}),response);
  (apiService.recommendMovies as jest.Mock).mockRejectedValueOnce(new Error('请求失败'));
  await submit();expect(save).toHaveBeenCalledTimes(2);expect(container.textContent).toContain('请求失败');
  (apiService.recommendMovies as jest.Mock).mockResolvedValueOnce({...response,success:false});await submit();expect(save).toHaveBeenCalledTimes(2);
});

test('reusing text fills the query without a request or result overwrite and resets next run to current ratings/page one',async()=>{
  await submit();await act(async()=>button('换一批').click());const calls=(apiService.recommendMovies as jest.Mock).mock.calls.length;
  const handled=jest.fn(),entry=historyEntryFixture();
  await render({historyRequest:entry,onHistoryHandled:handled});await render({historyRequest:null,onHistoryHandled:handled});
  expect(handled).toHaveBeenCalledTimes(1);expect(query().value).toBe(entry.request.query);
  expect(apiService.recommendMovies).toHaveBeenCalledTimes(calls);expect(container.textContent).toContain('上次推荐结果（2 部）');
  expect(container.textContent).toContain('原需求：保留我的需求');expect(button('换一批')).toBeUndefined();
  await render({library:[{...watched,personal_rating:2}]});await submit();
  expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({query:entry.request.query,discover_page:1,exclude_movie_ids:[123],feedback:[expect.objectContaining({rating:2})]}));
});

test('movie reuse restores the exact reference without triggering TMDB search or multimodal recommendation',async()=>{
  const entry={...historyEntryFixture(),request:{...historyEntryFixture().request,reference:{kind:'movie' as const,movie}}};
  await render({historyRequest:entry});await render({historyRequest:null});
  expect(apiService.recommendMoviesMultimodal).not.toHaveBeenCalled();expect(apiService.searchMovies).not.toHaveBeenCalled();
  expect(container.querySelector<HTMLInputElement>('[role="combobox"]')?.value).toBe(movie.title);
  await submit();expect(apiService.recommendMoviesMultimodal).toHaveBeenLastCalledWith(expect.objectContaining({reference_movie_id:123,query:entry.request.query,discover_page:1}));
});

test('image history clears prior references, warns the image is missing and never uploads stored image data',async()=>{
  await render({referenceRequest:movie});await render();
  const entry={...historyEntryFixture(),request:{...historyEntryFixture().request,reference:{kind:'image' as const}}};
  await render({historyRequest:entry});await render({historyRequest:null});
  expect(container.textContent).toContain('原参考图片未保存');expect(apiService.recommendMoviesMultimodal).not.toHaveBeenCalled();
  await submit();expect(apiService.recommendMovies).toHaveBeenLastCalledWith(expect.objectContaining({query:entry.request.query,discover_page:1}));
  expect(apiService.recommendMoviesMultimodal).not.toHaveBeenCalled();
});

test('upload success records only an image marker and the actual response',async()=>{
  const save=jest.fn();URL.createObjectURL=jest.fn(()=> 'blob:fixture');URL.revokeObjectURL=jest.fn();
  (apiService.recommendMoviesMultimodal as jest.Mock).mockResolvedValueOnce(historyResponseFixture);
  await render({onSaveHistory:save});const file=new File(['fixture'],'private-image-name.png',{type:'image/png'});
  await act(async()=>Simulate.change(container.querySelector<HTMLInputElement>('input[type="file"]')!,{target:{files:[file]}} as any));await submit();
  expect(apiService.recommendMoviesMultimodal).toHaveBeenLastCalledWith(expect.objectContaining({reference_image:file}));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({reference:{kind:'image'}}),historyResponseFixture);
  expect(JSON.stringify(save.mock.calls[0][0])).not.toContain('private-image-name');
});


test('optional reference editor collapses without losing the exact selected reference or firing a request', async () => {
  const editor = () => container.querySelector<HTMLElement>('#visual-reference')!;
  expect(getComputedStyle(editor()).display).toBe('none');
  await act(async () => button('添加图片或参考电影 +').click());
  expect(getComputedStyle(editor()).display).toBe('block');
  await render({ referenceRequest: movie }); await render();
  expect(getComputedStyle(editor()).display).toBe('block');
  await act(async () => button('参考电影：测试电影 −').click());
  expect(getComputedStyle(editor()).display).toBe('none');
  expect(apiService.recommendMoviesMultimodal).not.toHaveBeenCalled();
  await submit();
  expect(apiService.recommendMoviesMultimodal).toHaveBeenLastCalledWith(expect.objectContaining({ reference_movie_id: 123 }));
});

test('result comes before closed diagnostics and a real degradation stays outside the disclosure', async () => {
  (apiService.recommendMovies as jest.Mock).mockResolvedValueOnce({ ...response, diagnostics: {
    ...response.diagnostics, semantic_fallback_reason: '测试模型不可用',
  }});
  await submit();
  const technical = container.querySelector<HTMLDetailsElement>('.cinema-technical-details')!;
  expect(technical.open).toBe(false);
  expect(technical.querySelector('summary')?.textContent).toContain('技术详情');
  const alert = Array.from(container.querySelectorAll('[role="alert"]')).find(el => el.textContent?.includes('测试模型不可用'))!;
  expect(technical.contains(alert)).toBe(false);
  expect(container.querySelector('.cinema-results-heading')!.compareDocumentPosition(technical) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await act(async () => { technical.open = true; });
  expect(technical.textContent).toContain('本次调用诊断');
  expect(technical.textContent).toContain('系统理解到的需求');
});

test('complete analysis shows all retrieval terms while old responses remain usable', async () => {
  const terms = ['space travel', 'astronaut', 'robot', 'identity', 'friendship', 'revenge', 'redemption', 'loneliness'];
  (apiService.recommendMovies as jest.Mock).mockResolvedValueOnce({ ...response, query_analysis: {
    ...response.query_analysis, themes: ['太空探索'], keywords: ['太空探索'], retrieval_keywords: terms,
  }});
  await submit();
  const technical = container.querySelector<HTMLDetailsElement>('.cinema-technical-details')!;
  expect(technical.open).toBe(false);
  expect(container.querySelector('.cinema-intent-summary')?.textContent).toContain('太空探索');
  for (const term of terms) expect(technical.textContent).toContain(`检索词 ${term}`);
  await submit(); // The existing response fixture omits retrieval_keywords.
  expect(container.textContent).toContain('实时推荐结果（2 部）');
});


test('omitted optional references and zero ratings are explained in details without becoming warning banners', async () => {
  (apiService.recommendMovies as jest.Mock).mockResolvedValueOnce({ ...response, diagnostics: {
    ...response.diagnostics, visual_reference_source: null, visual_fallback_reason: '未提供参考图片或参考电影',
    feedback_count: 0, feedback_fallback_reason: '尚无有效评分',
  }});
  await submit();
  expect(container.querySelectorAll('[role="alert"]')).toHaveLength(0);
  expect(container.querySelector('.cinema-technical-details')!.textContent).toContain('未提供参考图片或参考电影');
});


test.each([
  { visual_reference_source: 'movie_poster', visual_fallback_reason: '参考图编码失败' },
  { feedback_count: 2, feedback_fallback_reason: '评分向量编码失败' },
])('active visual/rating degradation remains visible before technical details: %j', async diagnostics => {
  (apiService.recommendMovies as jest.Mock).mockResolvedValueOnce({ ...response, diagnostics: { ...response.diagnostics, ...diagnostics } });
  await submit();
  const alerts = Array.from(container.querySelectorAll('[role="alert"]'));
  expect(alerts).toHaveLength(1);
  expect(container.querySelector('.cinema-technical-details')!.contains(alerts[0])).toBe(false);
});
