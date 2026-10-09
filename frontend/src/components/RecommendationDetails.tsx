import React from 'react';
import { Box, Chip, ChipProps, Paper, Stack, Typography } from '@mui/material';
import { RecommendationResponse } from '../types/index.ts';

const SummaryChip = (props: ChipProps) => <Chip {...props} size="small" variant="outlined" className="cinema-summary-tag" />;

export const RecommendationDetails = ({ result }: { result: RecommendationResponse }) => (
  <details className="cinema-technical-details">
    <summary>技术详情 · 完整解析与调用诊断</summary>
    <Box sx={{ pt: 2 }}>
          <Paper variant="outlined" className="cinema-summary-panel" sx={{ p: 2, mb: 2 }}>
            <Typography component="h3" variant="subtitle2" className="cinema-summary-heading">本次调用诊断</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <SummaryChip size="small" label={`${(result.diagnostics.duration_ms / 1000).toFixed(2)} 秒`} />
              <SummaryChip size="small" label={`DeepSeek ${result.diagnostics.llm_calls} 次`} />
              <SummaryChip size="small" label={`${result.diagnostics.llm_tokens.toLocaleString()} Tokens`} />
              <SummaryChip size="small" label={`TMDB ${result.diagnostics.tmdb_requests} 次`} />
              <SummaryChip
                size="small"
                color={result.diagnostics.cache_hit ? 'success' : 'default'}
                label={result.diagnostics.cache_hit ? '命中推荐缓存' : '实时计算'}
              />
              <SummaryChip
                size="small"
                variant="outlined"
                label={`返回模型 ${result.diagnostics.reported_model || result.diagnostics.configured_model}`}
              />
              <SummaryChip
                size="small"
                color={result.diagnostics.semantic_mode === 'vector_semantic' ? 'success' : 'warning'}
                variant="outlined"
                label={
                  result.diagnostics.semantic_mode === 'vector_semantic'
                    ? `向量语义 · ${result.diagnostics.semantic_device || '未知设备'}`
                    : '词项匹配降级'
                }
              />
              {result.diagnostics.semantic_mode === 'vector_semantic' && (
                <SummaryChip
                  size="small"
                  variant="outlined"
                  label={`文本向量缓存 ${result.diagnostics.text_vector_cache_hits}/${result.diagnostics.text_vector_cache_hits + result.diagnostics.text_vector_cache_misses}`}
                />
              )}
              <SummaryChip
                size="small"
                color={result.diagnostics.visual_mode === 'image_vector' ? 'success' : 'default'}
                variant="outlined"
                label={
                  result.diagnostics.visual_mode === 'image_vector'
                    ? `视觉向量 · ${result.diagnostics.visual_device || '未知设备'}`
                    : '视觉未参与'
                }
              />
              {result.diagnostics.visual_mode === 'image_vector' && (
                <SummaryChip
                  size="small"
                  variant="outlined"
                  label={`海报向量缓存 ${result.diagnostics.poster_vector_cache_hits}/${result.diagnostics.poster_vector_cache_hits + result.diagnostics.poster_vector_cache_misses}`}
                />
              )}
              {result.diagnostics.feedback_count > 0 && (
                <SummaryChip
                  size="small"
                  color={result.diagnostics.feedback_mode === 'unavailable' ? 'default' : 'success'}
                  variant="outlined"
                  label={`评分反馈 ${result.diagnostics.feedback_count} 条${result.diagnostics.feedback_mode === 'unavailable' ? ' · 未参与' : ''}`}
                />
              )}
            </Stack>
            {result.diagnostics.semantic_fallback_reason && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                {result.diagnostics.semantic_fallback_reason}
              </Typography>
            )}
            {result.diagnostics.visual_fallback_reason && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                视觉：{result.diagnostics.visual_fallback_reason}
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
              多样性：{result.diagnostics.diversity_mode === 'mmr'
                ? `MMR λ=${result.diagnostics.mmr_lambda.toFixed(2)}，${result.diagnostics.mmr_reordered_count} 部发生位次变化，重排 ${result.diagnostics.mmr_rerank_ms} ms`
                : '未启用重排'}
            </Typography>
            {result.diagnostics.feedback_fallback_reason && (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                反馈：{result.diagnostics.feedback_fallback_reason}
              </Typography>
            )}
          </Paper>

          <Paper variant="outlined" className="cinema-summary-panel" sx={{ p: 2, mb: 2 }}>
            <Typography component="h3" variant="subtitle2" className="cinema-summary-heading">系统理解到的需求</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {result.query_analysis.genres.map((value) => <SummaryChip key={value} label={value} />)}
              {result.query_analysis.excluded_genres.map((value) => (
                <SummaryChip key={`exclude-${value}`} color="error" variant="outlined" label={`排除 ${value}`} />
              ))}
              {result.query_analysis.origin_countries.map((value) => <SummaryChip key={value} label={value} />)}
              {result.query_analysis.original_languages.map(value => <SummaryChip key={`language-${value}`} label={`语言 ${value}`} />)}
              {result.query_analysis.keywords.map(value => <SummaryChip key={`keyword-${value}`} label={`关键词 ${value}`} />)}
              {(result.query_analysis.retrieval_keywords || []).map(value => <SummaryChip key={`retrieval-${value}`} label={`检索词 ${value}`} />)}
              {(result.query_analysis.runtime_min || result.query_analysis.runtime_max) && <SummaryChip
                label={`片长 ${result.query_analysis.runtime_min || '不限'}—${result.query_analysis.runtime_max || '不限'}分钟`} />}
              {(result.query_analysis.year_from || result.query_analysis.year_to) && <SummaryChip
                label={`年份 ${result.query_analysis.year_from || '不限'}—${result.query_analysis.year_to || '不限'}`} />}
              {result.query_analysis.min_rating != null && <SummaryChip label={`最低TMDB分 ${result.query_analysis.min_rating}`} />}
              {result.query_analysis.seed_title && <SummaryChip label={`参考片名 ${result.query_analysis.seed_title}`} />}
              {result.query_analysis.avoid_violence && <SummaryChip label="偏好低暴力（软条件）" />}
              {(result.query_analysis.excluded_director_names || []).map(value => <SummaryChip key={`exclude-director-${value}`} color="error" label={`排除导演 ${value}`} />)}
              {result.query_analysis.moods.map((value) => <SummaryChip key={value} label={value} />)}
              {result.query_analysis.themes.map((value) => (
                <SummaryChip key={`theme-${value}`} color="secondary" variant="outlined" label={`主题 ${value}`} />
              ))}
              {result.query_analysis.preferred_aspects.map((value) => (
                <SummaryChip key={`preferred-${value}`} color="success" variant="outlined" label={`喜欢 ${value}`} />
              ))}
              {result.query_analysis.disliked_aspects.map((value) => (
                <SummaryChip key={`disliked-${value}`} color="error" variant="outlined" label={`不喜欢 ${value}`} />
              ))}
              {result.query_analysis.director_names.map((value) => (
                <SummaryChip key={`director-${value}`} label={`导演 ${value}`} />
              ))}
              {result.query_analysis.cast_names.map((value) => (
                <SummaryChip key={`cast-${value}`} label={`演员 ${value}`} />
              ))}
              <SummaryChip
                size="small"
                variant="outlined"
                label={
                  result.query_analysis.input_mode === 'review'
                    ? '影评解析'
                    : result.query_analysis.parser === 'llm+rules'
                      ? 'AI + 规则解析'
                      : '规则解析'
                }
              />
            </Stack>
          </Paper>

    </Box>
  </details>
);
