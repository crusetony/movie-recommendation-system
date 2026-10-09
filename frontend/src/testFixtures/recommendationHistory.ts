// Synthetic responses for isolated tests only; never experimental truth or performance evidence.
import { RecommendationHistoryEntry, RecommendationHistoryRequest, RecommendationResponse } from '../types/index.ts';
export const historyRequestFixture:RecommendationHistoryRequest={query:'核对太空电影',reference:{kind:'none'},discover_page:1,is_refresh:false,excluded_count:0,rating_count:0};
export const historyResponseFixture:RecommendationResponse={success:true,user_query:'核对太空电影',total_found:1,has_more:true,next_discover_page:2,tmdb_total_pages:3,warnings:['核对警告'],
  query_analysis:{original_query:'核对太空电影',input_mode:'preference',parser:'rules',avoid_violence:false,genres:['科幻'],excluded_genres:['恐怖'],origin_countries:[],original_languages:[],moods:[],keywords:[],themes:['太空'],preferred_aspects:[],disliked_aspects:[],director_names:[],cast_names:[]},
  recommendations:[{tmdb_id:90001,title:'核对影片',original_title:'Fixture',overview:'虚构核对简介',poster_url:null,release_date:'1994-01-01',runtime:100,vote_average:7,vote_count:100,popularity:10,genres:['科幻'],keywords:[],director:null,cast:[],original_language:'en',production_countries:['US'],backdrop_url:null,image_count:0,recall_sources:['fixture'],tmdb_url:'https://www.themoviedb.org/movie/90001',match_score:0.72,reasoning:'原响应核对说明',rank_before_mmr:1,rank_after_mmr:1,diversity_penalty:0,diversity_reason:'未启用',
    score_breakdown:{final_score:0.72,formula_version:'fixture-version',components:[{key:'quality',label:'质量核对',score:0.72,weight:1,configured_weight:1,effective_weight:1,contribution:0.72,available:true,evidence:['核对证据'],algorithm:'fixture'}]}}],
  diagnostics:{duration_ms:10,cache_hit:false,llm_calls:0,llm_tokens:0,tmdb_requests:0,configured_model:'fixture',reported_model:null,
    semantic_mode:'lexical_fallback',semantic_fallback_reason:'核对词项降级',semantic_load_ms:0,semantic_encode_ms:0,text_vector_cache_hits:0,text_vector_cache_misses:0,
    visual_mode:'unavailable',visual_fallback_reason:'核对无视觉',visual_load_ms:0,visual_encode_ms:0,visual_download_ms:0,poster_vector_cache_hits:0,poster_vector_cache_misses:0,
    diversity_mode:'disabled',mmr_lambda:1,mmr_reordered_count:0,mmr_rerank_ms:0,feedback_mode:'unavailable',feedback_count:0,feedback_vector_count:0,feedback_encode_ms:0},
};
export const historyEntryFixture=(id='fixture',second=0):RecommendationHistoryEntry=>({id,created_at:new Date(Date.UTC(2026,9,8,0,0,second)).toISOString(),request:historyRequestFixture,response:historyResponseFixture});
