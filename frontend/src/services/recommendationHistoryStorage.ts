import { RecommendationHistoryEntry, RecommendationHistoryRequest, RecommendationResponse } from '../types/index.ts';

export const RECOMMENDATION_HISTORY_STORAGE_KEY = 'movie_recommendation_history';
export const MAX_HISTORY_ITEMS = 20;
export const MAX_HISTORY_CHARACTERS = 1024 * 1024; // About 2 MiB in UTF-16; quota failure still handled.
type Rule = (value: any) => any;
const invalid = (): never => { throw new Error('历史数据不完整或版本无法识别'); };
const text = (limit = 12000): Rule => value => typeof value === 'string' && value.length <= limit ? value : invalid();
const number: Rule = value => typeof value === 'number' && Number.isFinite(value) ? value : invalid();
const integer = (min = 0, max = Number.MAX_SAFE_INTEGER): Rule => value => Number.isInteger(value) && value >= min && value <= max ? value : invalid();
const boolean: Rule = value => typeof value === 'boolean' ? value : invalid();
const oneOf = (...values: string[]): Rule => value => values.includes(value) ? value : invalid();
const nullable = (rule: Rule): Rule => value => value === null ? null : rule(value);
const list = (rule: Rule, limit = 100): Rule => value => Array.isArray(value) && value.length <= limit ? value.map(rule) : invalid();
const texts = list(text(2000));
const fields = (value: any, required: Record<string, Rule>, optional: Record<string, Rule> = {}) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  const result: Record<string, any> = {};
  for (const [key, rule] of Object.entries(required)) result[key] = rule(value[key]);
  for (const [key, rule] of Object.entries(optional)) if (value[key] !== undefined) result[key] = rule(value[key]);
  return result;
};
const rules = (keys: string, rule: Rule) => Object.fromEntries(keys.split(' ').map(key => [key, rule]));
const timestamp: Rule = value => {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return invalid();
  const date = new Date(value);
  return Number.isFinite(date.getTime()) && date.toISOString() === value ? value : invalid();
};
const imageUrl: Rule = value => {
  if (value === null) return null;
  try { const url = new URL(text(1000)(value)); return url.protocol === 'https:' && url.hostname === 'image.tmdb.org' && !url.username && !url.password && !url.search && !url.hash ? url.href : invalid(); }
  catch { return invalid(); }
};
const reference: Rule = value => {
  const kind = oneOf('none', 'movie', 'image')(value?.kind);
  if (kind !== 'movie') return { kind }; // Never retain a File, image name, path, data or object URL.
  return { kind, movie: fields(value.movie, {tmdb_id:integer(1),title:text(300),original_title:text(300)},
    {release_date:nullable(text(20)),poster_url:imageUrl}) };
};
const request: Rule = value => fields(value, {query: value => {
  const query = text(3000)(value); return query.trim() ? query : invalid();
},reference,discover_page:integer(1,500),is_refresh:boolean,excluded_count:integer(),rating_count:integer(0,200)});
const analysis: Rule = value => fields(value, {
  original_query:text(3000),input_mode:oneOf('preference','review'),parser:oneOf('rules','llm+rules'),avoid_violence:boolean,
  ...rules('genres excluded_genres origin_countries original_languages moods keywords themes preferred_aspects disliked_aspects director_names cast_names',texts),
}, {...rules('year_from year_to runtime_min runtime_max min_rating',nullable(number)),seed_title:nullable(text(300)),excluded_director_names:texts,retrieval_keywords:texts});
const component: Rule = value => fields(value, {
  key:oneOf('hard_constraints','text_relevance','visual_similarity','quality','popularity','user_feedback'),label:text(300),
  ...rules('score weight configured_weight effective_weight contribution',number),available:boolean,evidence:texts,algorithm:text(300),
});
const movie: Rule = value => {
  const result = fields(value, {
    tmdb_id:integer(1),...rules('title original_title',text(300)),overview:text(),
    ...rules('vote_average popularity match_score diversity_penalty',number),...rules('vote_count image_count rank_before_mmr rank_after_mmr',integer()),
    ...rules('genres keywords cast production_countries recall_sources',texts),original_language:text(100),
    tmdb_url:text(1000),reasoning:text(),diversity_reason:text(),
    score_breakdown:value => fields(value,{final_score:number,formula_version:text(300),components:list(component,6)}),
  }, {poster_url:imageUrl,backdrop_url:imageUrl,release_date:nullable(text(20)),runtime:nullable(number),director:nullable(text(500))});
  const url = new URL(result.tmdb_url);
  if (url.protocol !== 'https:' || url.hostname !== 'www.themoviedb.org' || url.username || url.password || url.search || url.hash || !new RegExp(`^/movie/${result.tmdb_id}(?:-|$)`).test(url.pathname)) return invalid();
  return result;
};
const diagnostics: Rule = value => fields(value, {
  ...rules('duration_ms llm_calls llm_tokens tmdb_requests semantic_load_ms semantic_encode_ms text_vector_cache_hits text_vector_cache_misses visual_load_ms visual_encode_ms visual_download_ms poster_vector_cache_hits poster_vector_cache_misses mmr_reordered_count mmr_rerank_ms feedback_count feedback_vector_count feedback_encode_ms',number),
  cache_hit:boolean,configured_model:text(300),semantic_mode:oneOf('vector_semantic','lexical_fallback'),
  visual_mode:oneOf('image_vector','unavailable'),diversity_mode:oneOf('mmr','disabled'),mmr_lambda:number,
  feedback_mode:oneOf('semantic_metadata','metadata','unavailable'),
}, {...rules('reported_model semantic_fallback_reason semantic_model semantic_model_version semantic_device visual_fallback_reason visual_reference_source visual_model visual_model_version visual_device mmr_algorithm feedback_fallback_reason',nullable(text(2000)))});
const response: Rule = value => {
  const result = fields(value,{success:boolean,user_query:text(3000),query_analysis:analysis,recommendations:list(movie,20),total_found:integer(),has_more:boolean,warnings:texts,diagnostics},
    {next_discover_page:nullable(integer(1)),tmdb_total_pages:nullable(integer())});
  if (!result.success) return invalid();
  if (new Set(result.recommendations.map((item:any)=>item.tmdb_id)).size !== result.recommendations.length) return invalid();
  return result; // evaluation_snapshot and any unknown fields are deliberately excluded.
};
export const normalizeHistoryEntry = (value: unknown): RecommendationHistoryEntry => fields(value, {
  id:value => { const id=text(100)(value);return id ? id : invalid(); },created_at:timestamp,request,response,
}) as RecommendationHistoryEntry;

export const createHistoryEntry = (request: RecommendationHistoryRequest, response: RecommendationResponse): RecommendationHistoryEntry => normalizeHistoryEntry({
  id:window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
  created_at:new Date().toISOString(),request,response,
});
export const limitHistory = (values: RecommendationHistoryEntry[]) => {
  const unique = new Map<string, RecommendationHistoryEntry>();
  values.forEach(value => { if (!unique.has(value.id)) unique.set(value.id,value); });
  const items = Array.from(unique.values()).sort((a,b) => b.created_at.localeCompare(a.created_at)).slice(0,MAX_HISTORY_ITEMS);
  while (items.length && JSON.stringify({schema_version:1,items}).length > MAX_HISTORY_CHARACTERS) items.pop();
  return items;
};
const parse = (raw: string) => {
  if (raw.length > MAX_HISTORY_CHARACTERS) return invalid();
  const value = JSON.parse(raw);
  if (value?.schema_version !== 1 || !Array.isArray(value.items) || value.items.length > MAX_HISTORY_ITEMS) return invalid();
  return value.items.map(normalizeHistoryEntry);
};
export const loadRecommendationHistory = (): {items:RecommendationHistoryEntry[];warning:string|null} => {
  try { const raw=window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY);return {items:raw===null?[]:limitHistory(parse(raw)),warning:null}; }
  catch { return {items:[],warning:'无法读取推荐历史，原数据已保留；当前操作仅在本次页面中生效，可明确清空历史后重新使用。'}; }
};
export const saveRecommendationHistory = (items:RecommendationHistoryEntry[],reset=false): string|null => {
  try {
    if (!reset) { const raw=window.localStorage.getItem(RECOMMENDATION_HISTORY_STORAGE_KEY);if(raw!==null) parse(raw); }
    window.localStorage.setItem(RECOMMENDATION_HISTORY_STORAGE_KEY,JSON.stringify({schema_version:1,items:limitHistory(items.map(normalizeHistoryEntry))}));
    return null;
  } catch { return '浏览器未能保存推荐历史，原数据未覆盖；本次修改可能在刷新后丢失。'; }
};
