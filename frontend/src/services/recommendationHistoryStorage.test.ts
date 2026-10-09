import { createHistoryEntry, limitHistory, loadRecommendationHistory, MAX_HISTORY_CHARACTERS, normalizeHistoryEntry, RECOMMENDATION_HISTORY_STORAGE_KEY, saveRecommendationHistory } from './recommendationHistoryStorage.ts';
import { historyEntryFixture, historyRequestFixture, historyResponseFixture } from '../testFixtures/recommendationHistory.ts';

beforeEach(()=>window.localStorage.clear());
afterEach(()=>jest.restoreAllMocks());

test('fresh history reads without writing; success round-trips scores, order, explanations, warnings and diagnostics',()=>{
  const write=jest.spyOn(Storage.prototype,'setItem');
  expect(loadRecommendationHistory()).toEqual({items:[],warning:null});expect(write).not.toHaveBeenCalled();
  const entry=createHistoryEntry(historyRequestFixture,historyResponseFixture);
  expect(entry.id).toBeTruthy();expect(saveRecommendationHistory([entry])).toBeNull();
  expect(loadRecommendationHistory().items).toEqual([entry]);
  expect(entry.response).toEqual(historyResponseFixture);
});

test('English retrieval topics round-trip while older history remains compatible', () => {
  const response = { ...historyResponseFixture, query_analysis: {
    ...historyResponseFixture.query_analysis, retrieval_keywords: ['space travel', 'astronaut'],
  }};
  const entry = createHistoryEntry(historyRequestFixture, response);
  expect(saveRecommendationHistory([entry])).toBeNull();
  expect(loadRecommendationHistory().items[0].response.query_analysis.retrieval_keywords).toEqual(['space travel', 'astronaut']);
});

test('allowlist removes raw snapshots, feedback, unknown fields and all uploaded image data',()=>{
  const value:any={...historyEntryFixture(),credential:'not stored',request:{...historyRequestFixture,feedback:[{rating:9}],reference:{kind:'image',file:new File(['private'],'private.png'),name:'private.png',preview:'blob:private',base64:'private'}},
    response:{...historyResponseFixture,evaluation_snapshot:{labels:'not stored'},secret:'not stored',recommendations:[{...historyResponseFixture.recommendations[0],private_field:'not stored'}]}};
  expect(saveRecommendationHistory([value])).toBeNull();const raw=window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY)!;
  expect(raw).not.toMatch(/credential|evaluation_snapshot|secret|feedback":|private|preview|base64|file"/);
  expect(loadRecommendationHistory().items[0].request.reference).toEqual({kind:'image'});
});

test('movie reference keeps the exact TMDB id and summary but omits user scores and unrelated metadata',()=>{
  const value:any={...historyEntryFixture(),request:{...historyRequestFixture,reference:{kind:'movie',movie:{tmdb_id:62,title:'参考影片',original_title:'Reference',poster_url:null,personal_rating:10,score_breakdown:{}}}}};
  const entry=normalizeHistoryEntry(value);expect(entry.request.reference).toEqual({kind:'movie',movie:{tmdb_id:62,title:'参考影片',original_title:'Reference',poster_url:null}});
});

test.each(['{broken',JSON.stringify({schema_version:99,items:[]}),JSON.stringify({schema_version:1,items:[{bad:true}]})])('corrupt and newer data remains intact until explicit reset: %s',raw=>{
  window.localStorage.setItem(RECOMMENDATION_HISTORY_STORAGE_KEY,raw);
  expect(loadRecommendationHistory().warning).toBeTruthy();
  expect(saveRecommendationHistory([historyEntryFixture()])).toBeTruthy();
  expect(window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY)).toBe(raw);
  expect(saveRecommendationHistory([],true)).toBeNull();expect(loadRecommendationHistory().items).toEqual([]);
});

test('recent twenty distinct runs stay newest-first, including repeated queries',()=>{
  const entries=Array.from({length:25},(_,index)=>historyEntryFixture(String(index),index));
  expect(saveRecommendationHistory([...entries,entries[24]])).toBeNull();
  const restored=loadRecommendationHistory().items;expect(restored).toHaveLength(20);
  expect(restored.map(item=>item.id)).toEqual(Array.from({length:20},(_,index)=>String(24-index)));
});

test('text budget evicts oldest entries while retaining the newest complete response',()=>{
  const movie=historyResponseFixture.recommendations[0];
  const response={...historyResponseFixture,recommendations:Array.from({length:20},(_,index)=>({...movie,tmdb_id:index+1,tmdb_url:`https://www.themoviedb.org/movie/${index+1}`,overview:'甲'.repeat(12000),reasoning:'乙'.repeat(12000)}))};
  const entries=Array.from({length:3},(_,index)=>({...historyEntryFixture(String(index),index),response}));
  expect(saveRecommendationHistory(entries)).toBeNull();const raw=window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY)!;
  expect(raw.length).toBeLessThanOrEqual(MAX_HISTORY_CHARACTERS);expect(loadRecommendationHistory().items.map(item=>item.id)).toEqual(['2','1']);
  expect(limitHistory([{...entries[2],response:{...response,recommendations:response.recommendations.map(item=>({...item,keywords:Array(100).fill('字'.repeat(2000))}))}}])).toEqual([]);
});

test('quota and unavailable storage preserve old bytes and return a warning',()=>{
  saveRecommendationHistory([historyEntryFixture()]);const before=window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY);
  jest.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('QuotaExceeded');});
  expect(saveRecommendationHistory([])).toBeTruthy();expect(window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY)).toBe(before);
  jest.spyOn(Storage.prototype,'getItem').mockImplementation(()=>{throw new Error('Disabled');});
  expect(loadRecommendationHistory()).toMatchObject({items:[],warning:expect.any(String)});
});

test('successful empty results are retained; unsuccessful responses are rejected',()=>{
  const empty=createHistoryEntry(historyRequestFixture,{...historyResponseFixture,recommendations:[],total_found:0,has_more:false,next_discover_page:null});
  expect(saveRecommendationHistory([empty])).toBeNull();expect(loadRecommendationHistory().items[0].response.recommendations).toEqual([]);
  expect(()=>createHistoryEntry(historyRequestFixture,{...historyResponseFixture,success:false})).toThrow();
});

test.each([
  (value:any)=>{value.created_at='2026-02-30';},
  (value:any)=>{value.response.recommendations[0].tmdb_url='javascript:alert(1)';},
  (value:any)=>{value.response.recommendations[0].poster_url='https://untrusted.example/image.jpg';},
  (value:any)=>{value.response.recommendations[0].score_breakdown.final_score=null;},
  (value:any)=>{value.response.recommendations.push(value.response.recommendations[0]);},
])('malformed dates, URLs, scores and duplicate movies cannot be rendered as valid history',mutate=>{
  const value=JSON.parse(JSON.stringify(historyEntryFixture()));mutate(value);
  const raw=JSON.stringify({schema_version:1,items:[value]});window.localStorage.setItem(RECOMMENDATION_HISTORY_STORAGE_KEY,raw);
  expect(loadRecommendationHistory()).toMatchObject({items:[],warning:expect.any(String)});
  expect(window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY)).toBe(raw);
});
