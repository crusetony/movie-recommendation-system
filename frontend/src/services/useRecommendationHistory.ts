import { useEffect, useRef, useState } from 'react';
import { RecommendationHistoryRequest, RecommendationResponse } from '../types/index.ts';
import { createHistoryEntry, limitHistory, loadRecommendationHistory, RECOMMENDATION_HISTORY_STORAGE_KEY, saveRecommendationHistory } from './recommendationHistoryStorage.ts';

export const useRecommendationHistory = () => {
  const [initial] = useState(loadRecommendationHistory);
  const [items,setItems] = useState(initial.items);
  const [warning,setWarning] = useState(initial.warning);
  const current=useRef(items);
  const commit = (next:typeof items,reset=false) => {
    current.current=limitHistory(next);setItems(current.current);
    setWarning(saveRecommendationHistory(current.current,reset));
  };
  const latest = () => [...current.current,...loadRecommendationHistory().items];
  const record = (request:RecommendationHistoryRequest,response:RecommendationResponse) => {
    try {
      const entry=createHistoryEntry(request,response);
      if (!limitHistory([entry]).length) {setWarning('这次结果超过历史保存空间，未存入历史；当前推荐结果仍可查看。');return;}
      commit([entry,...latest()]);
    } catch { setWarning('这次结果无法完整保存到历史；当前推荐结果仍可查看，已有历史未覆盖。'); }
  };
  useEffect(() => {
    const sync = (event:StorageEvent) => {
      if(event.key!==RECOMMENDATION_HISTORY_STORAGE_KEY && event.key!==null) return;
      const loaded=loadRecommendationHistory();
      if (!loaded.warning) {current.current=loaded.items;setItems(loaded.items);}
      setWarning(loaded.warning);
    };
    window.addEventListener('storage',sync);return () => window.removeEventListener('storage',sync);
  },[]);
  return {items,warning,record,remove:(id:string)=>commit(latest().filter(item=>item.id!==id)),clear:()=>commit([],true)};
};
