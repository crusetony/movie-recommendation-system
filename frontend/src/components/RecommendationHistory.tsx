import React, { useState } from 'react';
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Stack, Typography } from '@mui/material';
import { MovieLibraryAction, MovieLibraryItem, MovieSummary, QueryIntent, RecommendationHistoryEntry } from '../types/index.ts';
import { MovieResultCard } from './MovieResultCard.tsx';

const savedTime = (value:string) => new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
const referenceText = (entry:RecommendationHistoryEntry) => entry.request.reference.kind === 'image' ? '上传图片 · 原图未保存'
  : entry.request.reference.kind === 'movie' ? `参考电影：${entry.request.reference.movie.title}` : '文字推荐';
const conditions = (value:QueryIntent): string[] => [
  ...value.genres,...value.excluded_genres.map(item=>`排除类型：${item}`),...value.origin_countries,
  ...value.original_languages.map(item=>`语言：${item}`),
  ...(value.year_from || value.year_to ? [`年份：${value.year_from ?? '不限'}–${value.year_to ?? '不限'}`]:[]),
  ...(value.runtime_min || value.runtime_max ? [`片长：${value.runtime_min ?? '不限'}–${value.runtime_max ?? '不限'}分钟`]:[]),
  ...(value.min_rating != null ? [`最低TMDB分：${value.min_rating}`]:[]),
  ...value.moods,...value.keywords.map(item=>`关键词：${item}`),...value.themes.map(item=>`主题：${item}`),
  ...value.preferred_aspects.map(item=>`喜欢：${item}`),...value.disliked_aspects.map(item=>`不喜欢：${item}`),
  ...value.director_names.map(item=>`导演：${item}`),...(value.excluded_director_names || []).map(item=>`排除导演：${item}`),
  ...value.cast_names.map(item=>`演员：${item}`),...(value.seed_title?[`参考片名：${value.seed_title}`]:[]),
  ...(value.avoid_violence?['偏好低暴力（软条件）']:[]),
];

export const RecommendationHistory = ({items,warning,onRemove,onClear,onReuse,reuseDisabled,library,onViewMovie,onLibraryToggle,onRatingChange}: {
  items:RecommendationHistoryEntry[];warning:string|null;onRemove:(id:string)=>void;onClear:()=>void;
  onReuse:(entry:RecommendationHistoryEntry)=>void;reuseDisabled:boolean;
  library:MovieLibraryItem[];onViewMovie:(movie:MovieSummary)=>void;
  onLibraryToggle:(movie:MovieSummary,action:MovieLibraryAction)=>void;onRatingChange:(movie:MovieSummary,rating:number|null)=>void;
}) => {
  const [selectedId,setSelectedId] = useState<string|null>(null);
  const [pendingDelete,setPendingDelete] = useState<string|null>(null);
  const selected=items.find(item=>item.id===selectedId);
  const parsed=selected ? conditions(selected.response.query_analysis) : [];
  return <Box className="cinema-history">
    <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between" flexWrap="wrap" useFlexGap>
      <Box>
        <Typography component="h2" variant="h5">推荐历史 · {items.length} 次</Typography></Box>
      {(items.length>0 || warning) && <Button variant="outlined" color="inherit" onClick={()=>setPendingDelete('all')}>清空历史</Button>}
    </Stack>
    <Typography variant="body2" color="text.secondary" sx={{my:2}}>最近20次成功推荐 · 保存在当前浏览器，查看历史不重新推荐</Typography>
    {warning && <Alert severity="warning" sx={{mb:2}}>{warning}</Alert>}
    {!items.length && <Alert severity="info">暂无推荐历史。完成一次推荐后，需求、结果和当时的评分说明会自动保存在这里。</Alert>}
    <Box className="cinema-history-list">{items.map(entry=><Box key={entry.id} className="cinema-history-entry" data-history-id={entry.id} data-selected={selectedId===entry.id}>
      <Typography component="h3" variant="subtitle2">{savedTime(entry.created_at)} · {entry.response.recommendations.length} 部{entry.request.is_refresh?' · 换一批':''}</Typography>
      <Typography variant="body2" className="cinema-history-query">{entry.request.query}</Typography>
      <Typography variant="caption" color="text.secondary" component="p" sx={{my:1}}>{referenceText(entry)} · 第{entry.request.discover_page}页 · 当时使用{entry.request.rating_count}条评分</Typography>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Button variant={selectedId===entry.id?'contained':'outlined'} size="small" onClick={()=>setSelectedId(entry.id)}>查看这次结果</Button>
        <Button size="small" disabled={reuseDisabled} onClick={()=>onReuse(entry)}>填入这次需求</Button>
        <Button size="small" color="inherit" onClick={()=>setPendingDelete(entry.id)}>删除这条</Button>
      </Stack>
    </Box>)}</Box>
    {selected && <Box className="cinema-history-snapshot" sx={{mt:3}}>
      <Typography component="h3" variant="h6">历史结果（{selected.response.recommendations.length} 部）</Typography>
      <Alert severity="info" sx={{my:2}}>保存于{savedTime(selected.created_at)}，以下顺序、分数和说明来自当时响应。当前片单状态实时同步，修改评分不会重算这份历史。</Alert>
      <Typography variant="body2" sx={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',mb:2}}>{selected.request.query}</Typography>
      <details className="cinema-history-analysis"><summary>当时解析的条件</summary>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{mt:1}}>{parsed.map((value,index)=><Chip key={index} label={value} size="small" variant="outlined" />)}</Stack>
        {!parsed.length && <Typography variant="body2" color="text.secondary">当时未解析到具体筛选条件。</Typography>}
        <Typography variant="caption" color="text.secondary">{selected.response.query_analysis.input_mode==='review'?'影评解析':'偏好描述'} · {selected.response.query_analysis.parser==='llm+rules'?'AI + 规则解析':'规则解析'}</Typography>
      </details>
      <Box className="cinema-history-diagnostics" sx={{my:2}}>
        <Typography variant="subtitle2">当时的调用与算法</Typography>
        <Typography variant="body2" color="text.secondary">{(selected.response.diagnostics.duration_ms/1000).toFixed(2)}秒 · DeepSeek {selected.response.diagnostics.llm_calls}次 / {selected.response.diagnostics.llm_tokens} Tokens · TMDB {selected.response.diagnostics.tmdb_requests}次 · {selected.response.diagnostics.cache_hit?'命中推荐缓存':'实时计算'}</Typography>
        <Typography variant="body2" color="text.secondary">{selected.response.diagnostics.reported_model || selected.response.diagnostics.configured_model} · {selected.response.diagnostics.semantic_mode==='vector_semantic'?'向量语义':'词项匹配降级'} · {selected.response.diagnostics.visual_mode==='image_vector'?'视觉向量':'视觉未参与'} · {selected.response.diagnostics.diversity_mode==='mmr'?`MMR λ=${selected.response.diagnostics.mmr_lambda}`:'未启用重排'}</Typography>
        {[selected.response.diagnostics.semantic_fallback_reason,selected.response.diagnostics.visual_fallback_reason,selected.response.diagnostics.feedback_fallback_reason].filter(Boolean).map((value,index)=><Typography key={index} variant="caption" color="text.secondary" component="p">{value}</Typography>)}
      </Box>
      {selected.response.warnings.map((value,index)=><Alert key={index} severity="warning" sx={{mb:1}}>{value}</Alert>)}
      {!selected.response.recommendations.length && <Alert severity="info">这次请求成功，但没有找到符合条件的电影。</Alert>}
      {selected.response.recommendations.map((movie,index)=><MovieResultCard key={`${selected.id}-${movie.tmdb_id}`} movie={movie} index={index} ratingContext="history"
        libraryItem={library.find(item=>item.tmdb_id===movie.tmdb_id)} onViewMovie={onViewMovie} onLibraryToggle={onLibraryToggle} onRatingChange={onRatingChange} />)}
    </Box>}
    <Dialog open={pendingDelete!==null} onClose={()=>setPendingDelete(null)} aria-labelledby="history-delete-title">
      <DialogTitle id="history-delete-title">{pendingDelete==='all'?'清空推荐历史？':'删除这条推荐历史？'}</DialogTitle>
      <DialogContent><DialogContentText>{pendingDelete==='all'?'将删除当前浏览器中的全部推荐历史，包括无法读取的历史数据。':'将删除这次保存的需求、结果和评分说明。'}片单、个人评分和当前推荐结果会保留。</DialogContentText></DialogContent>
      <DialogActions><Button onClick={()=>setPendingDelete(null)}>取消</Button><Button color="error" onClick={()=>{if(pendingDelete==='all'){onClear();setSelectedId(null);}else if(pendingDelete){onRemove(pendingDelete);if(selectedId===pendingDelete)setSelectedId(null);}setPendingDelete(null);}}>确认删除</Button></DialogActions>
    </Dialog>
  </Box>;
};
