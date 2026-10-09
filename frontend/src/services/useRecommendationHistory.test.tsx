import React,{act} from 'react';
import {createRoot,Root} from 'react-dom/client';
import {useRecommendationHistory} from './useRecommendationHistory.ts';
import {loadRecommendationHistory,RECOMMENDATION_HISTORY_STORAGE_KEY,saveRecommendationHistory} from './recommendationHistoryStorage.ts';
import {historyEntryFixture,historyRequestFixture,historyResponseFixture} from '../testFixtures/recommendationHistory.ts';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
let api:ReturnType<typeof useRecommendationHistory>,root:Root,container:HTMLDivElement;
const Harness=()=>{api=useRecommendationHistory();return <div>{api.items.length} / {api.warning}</div>;};
beforeEach(async()=>{window.localStorage.clear();container=document.createElement('div');document.body.appendChild(container);root=createRoot(container);await act(async()=>root.render(<Harness/>));});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();jest.restoreAllMocks();});

test('records and deletes history independently of library, feedback and experiment storage',async()=>{
  window.localStorage.setItem('movie_recommendation_library','keep library');window.localStorage.setItem('movie_recommendation_feedback','keep feedback');
  await act(async()=>api.record(historyRequestFixture,historyResponseFixture));expect(api.items).toHaveLength(1);
  const id=api.items[0].id;expect(loadRecommendationHistory().items[0].id).toBe(id);
  await act(async()=>api.remove(id));expect(api.items).toEqual([]);
  await act(async()=>api.record(historyRequestFixture,historyResponseFixture));await act(async()=>api.clear());
  expect(loadRecommendationHistory().items).toEqual([]);expect(window.localStorage.getItem('movie_recommendation_library')).toBe('keep library');
  expect(window.localStorage.getItem('movie_recommendation_feedback')).toBe('keep feedback');
});

test('storage events sync another tab, and adding a run merges existing stored runs',async()=>{
  saveRecommendationHistory([historyEntryFixture('other')]);
  await act(async()=>window.dispatchEvent(new StorageEvent('storage',{key:RECOMMENDATION_HISTORY_STORAGE_KEY})));
  expect(api.items[0].id).toBe('other');
  saveRecommendationHistory([historyEntryFixture('newer-other',1),historyEntryFixture('other')]);
  await act(async()=>api.record(historyRequestFixture,historyResponseFixture));expect(api.items).toHaveLength(3);
});

test('failed persistence keeps this page usable and does not overwrite prior valid data',async()=>{
  saveRecommendationHistory([historyEntryFixture('existing')]);const raw=window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY);
  jest.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('Quota');});
  await act(async()=>api.record(historyRequestFixture,historyResponseFixture));expect(api.items).toHaveLength(2);expect(api.warning).toContain('未能保存');
  expect(window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY)).toBe(raw);
});

test('failed or oversized results do not replace prior history, and corrupt bytes need explicit reset',async()=>{
  await act(async()=>api.record(historyRequestFixture,{...historyResponseFixture,success:false}));expect(api.items).toEqual([]);expect(api.warning).toContain('无法完整保存');
  const response={...historyResponseFixture,recommendations:historyResponseFixture.recommendations.map(item=>({...item,keywords:Array(100).fill('字'.repeat(2000))}))};
  // Twenty distinct movies produce a complete response larger than the local budget.
  response.recommendations=Array.from({length:20},(_,index)=>({...response.recommendations[0],tmdb_id:index+1,tmdb_url:`https://www.themoviedb.org/movie/${index+1}`}));
  await act(async()=>api.record(historyRequestFixture,response));expect(api.items).toEqual([]);expect(api.warning).toContain('超过');
  window.localStorage.setItem(RECOMMENDATION_HISTORY_STORAGE_KEY,'{broken');
  await act(async()=>api.record(historyRequestFixture,historyResponseFixture));expect(api.items).toHaveLength(1);expect(api.warning).toBeTruthy();
  expect(window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY)).toBe('{broken');
  await act(async()=>api.clear());expect(api.items).toEqual([]);expect(api.warning).toBeNull();expect(loadRecommendationHistory().items).toEqual([]);
});
