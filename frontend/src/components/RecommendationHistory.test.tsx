import React,{act} from 'react';
import {createRoot,Root} from 'react-dom/client';
import {RecommendationHistory} from './RecommendationHistory.tsx';
import {historyEntryFixture} from '../testFixtures/recommendationHistory.ts';
jest.mock('./MovieResultCard.tsx',()=>({MovieResultCard:({movie,onViewMovie,onLibraryToggle,onRatingChange,ratingContext}:any)=><div data-rating-context={ratingContext}>
  <span>{movie.title} · {movie.reasoning} · {movie.score_breakdown.final_score}</span>
  <button onClick={()=>onViewMovie(movie)}>核对详情</button><button onClick={()=>onLibraryToggle(movie,'favorite')}>核对收藏</button><button onClick={()=>onRatingChange(movie,9)}>核对评分</button>
</div>}));
(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
const entry=historyEntryFixture();
const props={items:[entry],warning:null,onRemove:jest.fn(),onClear:jest.fn(),onReuse:jest.fn(),reuseDisabled:false,library:[],onViewMovie:jest.fn(),onLibraryToggle:jest.fn(),onRatingChange:jest.fn()};
let root:Root,container:HTMLDivElement;
const render=(changes:Partial<React.ComponentProps<typeof RecommendationHistory>>={})=>act(async()=>root.render(<RecommendationHistory {...props} {...changes}/>));
const click=(text:string)=>act(async()=>Array.from(document.querySelectorAll('button')).find(el=>el.textContent===text)!.click());
beforeEach(async()=>{container=document.createElement('div');document.body.appendChild(container);root=createRoot(container);await render();});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();jest.clearAllMocks();});

test('viewing shows original snapshot conditions, diagnostics and reasons without reusing or changing results',async()=>{
  const before=JSON.stringify(entry);expect(container.textContent).not.toContain('原响应核对说明');await click('查看这次结果');
  expect(container.textContent).toContain('历史结果（1 部）');expect(container.textContent).toContain('原响应核对说明');expect(container.textContent).toContain('核对词项降级');
  expect(container.textContent).toContain('科幻');expect(container.textContent).toContain('排除类型：恐怖');expect(container.textContent).toContain('fixture');
  expect(container.querySelector('[data-rating-context="history"]')).toBeTruthy();expect(props.onReuse).not.toHaveBeenCalled();
  await click('核对详情');await click('核对收藏');await click('核对评分');
  expect(props.onViewMovie).toHaveBeenCalledWith(entry.response.recommendations[0]);
  expect(props.onLibraryToggle).toHaveBeenCalledWith(entry.response.recommendations[0],'favorite');expect(props.onRatingChange).toHaveBeenCalledWith(entry.response.recommendations[0],9);
  expect(JSON.stringify(entry)).toBe(before);
});

test('reuse is explicit and disabled during a live recommendation',async()=>{
  await render({reuseDisabled:true});await click('填入这次需求');expect(props.onReuse).not.toHaveBeenCalled();
  await render();await click('填入这次需求');expect(props.onReuse).toHaveBeenCalledWith(entry);
});

test('single deletion cancellation preserves the snapshot, confirmation removes only its id',async()=>{
  await click('查看这次结果');await click('删除这条');expect(props.onRemove).not.toHaveBeenCalled();await click('取消');
  expect(container.textContent).toContain('历史结果（1 部）');await click('删除这条');await click('确认删除');
  expect(props.onRemove).toHaveBeenCalledWith(entry.id);expect(props.onClear).not.toHaveBeenCalled();expect(container.textContent).not.toContain('历史结果（1 部）');
});

test('full reset is confirmed separately, including unreadable data, and can be canceled',async()=>{
  await render({items:[],warning:'版本无法读取，原数据已保留'});await click('清空历史');await click('取消');expect(props.onClear).not.toHaveBeenCalled();
  await click('清空历史');expect(document.body.textContent).toContain('片单、个人评分和当前推荐结果会保留');await click('确认删除');expect(props.onClear).toHaveBeenCalledTimes(1);
});

test('empty successful result and image unavailability are explicit; external removal closes the selected snapshot',async()=>{
  const empty={...entry,request:{...entry.request,reference:{kind:'image' as const}},response:{...entry.response,recommendations:[],total_found:0,has_more:false}};
  await render({items:[empty]});expect(container.textContent).toContain('原图未保存');await click('查看这次结果');expect(container.textContent).toContain('请求成功，但没有找到');
  await render({items:[]});expect(container.textContent).toContain('暂无推荐历史');expect(container.textContent).not.toContain('历史结果（0 部）');
});
