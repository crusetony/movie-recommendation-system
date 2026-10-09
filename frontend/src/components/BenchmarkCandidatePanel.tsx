import React, { useState } from 'react';
import { Alert, Box, Link, Paper, Stack, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { BenchmarkCandidate, BenchmarkCandidates, MachineJudgment } from '../types/index.ts';

type Props = {
  snapshot: BenchmarkCandidates;
  grades: Record<string, number>;
  disabled: boolean;
  onGrade: (movieId: number, grade: number | null) => void;
  review?: MachineJudgment[];
};

const CandidateCard = ({ movie, grade, disabled, onGrade, review }: {
  movie: BenchmarkCandidate;
  grade: number | undefined;
  disabled: boolean;
  onGrade: Props['onGrade'];
  review?: MachineJudgment;
}) => {
  const [imageFailed, setImageFailed] = useState(false);
  return (
    <Paper variant="outlined" sx={{ p: 1.5 }} data-testid={`annotation-candidate-${movie.tmdb_id}`}>
      <Stack direction="row" spacing={1.5}>
        <Box sx={{ width: 72, flexShrink: 0 }}>
          {movie.poster_url && !imageFailed ? (
            <Box component="img" src={movie.poster_url} alt={`${movie.title}海报`}
              loading="lazy" onError={() => setImageFailed(true)}
              sx={{ width: 72, height: 108, objectFit: 'cover', borderRadius: 1 }} />
          ) : <Typography variant="caption" color="text.secondary">海报不可用</Typography>}
        </Box>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography variant="subtitle2">{movie.title}</Typography>
          <Typography variant="caption" color="text.secondary" display="block">
            {movie.original_title} · {movie.release_date?.slice(0, 4) || '年份未知'} · {movie.runtime ? `${movie.runtime}分钟` : '片长未知'}
          </Typography>
          <Typography variant="caption" display="block">
            {movie.genres.join(' / ') || '类型未知'} · {movie.production_countries.join(' / ') || '国家未知'}
          </Typography>
          <Typography variant="caption" display="block">导演：{movie.director || '未知'}</Typography>
          <Typography variant="caption" display="block">主演：{movie.cast.join(' / ') || '未知'}</Typography>
          <Link href={movie.tmdb_url} target="_blank" rel="noopener noreferrer" variant="caption">
            TMDB · {movie.tmdb_id}
          </Link>
        </Box>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1, display: '-webkit-box', WebkitLineClamp: 7, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
        {movie.overview || '快照中没有简介，请参考 TMDB 详情核对。'}
      </Typography>
      {movie.keywords.length > 0 && <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 0.5 }}>
        关键词：{movie.keywords.join(' / ')}
      </Typography>}
      {review && <Box sx={{ mt: 1, p: 1, bgcolor: 'action.hover', borderRadius: 1 }}>
        <Typography variant="caption" component="p">机器核对{review.grade == null ? ' · 不可判断' : ` · ${review.grade} 分`}：{review.reason}</Typography>
        {review.unknowns.length > 0 && <Typography variant="caption" component="p" color="text.secondary">证据范围外：{review.unknowns.join('；')}</Typography>}
      </Box>}
      <ToggleButtonGroup exclusive size="small" value={grade ?? null} disabled={disabled}
        aria-label={`${movie.title}相关等级`}
        onChange={(_, value: number | null) => onGrade(movie.tmdb_id, value)}
        sx={{ mt: 1, display: 'flex', flexWrap: 'wrap' }}>
        <ToggleButton value={0}>0 · 不相关</ToggleButton>
        <ToggleButton value={1}>1 · 部分相关</ToggleButton>
        <ToggleButton value={2}>2 · 高度相关</ToggleButton>
      </ToggleButtonGroup>
      <Typography variant="caption" component="p" sx={{ mt: 0.5 }} color={grade === undefined ? 'warning.main' : 'text.secondary'}>
        {grade === undefined ? (review?.grade === null ? '不可判断（不等于 0 分）' : '未标注（不等于 0 分）') : `已标注 ${grade} 分；再点一次可撤销`}
      </Typography>
    </Paper>
  );
};

export const BenchmarkCandidatePanel = ({ snapshot, grades, disabled, onGrade, review }: Props) => {
  const labeled = snapshot.candidates.filter((movie) => grades[String(movie.tmdb_id)] !== undefined).length;
  return (
    <Box sx={{ mb: 2 }}>
      <Typography variant="subtitle2">真实候选电影 · 已标注 {labeled}/{snapshot.candidate_count}</Typography>
      <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1 }}>
        快照：{snapshot.snapshot_id} · 文件更新时间：{snapshot.snapshot_updated_at ? new Date(snapshot.snapshot_updated_at).toLocaleString() : '未知'}
      </Typography>
      <Alert severity="info" sx={{ mb: 1.5 }}>
        候选按 TMDB ID 排列，不显示算法分数。0=不相关，1=部分相关，2=高度相关；
        {review ? '已完成机器核对，未知项保留不可判断；你可以选择调整。' : '手动确认需要完整等级；部分标注可以保存草稿。'}范围：{snapshot.scope}，不是 TMDB 全部电影或完整召回池。
      </Alert>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' }, gap: 1.5, maxHeight: 680, overflowY: 'auto', pr: 0.5 }}>
        {snapshot.candidates.map((movie) => <CandidateCard key={movie.tmdb_id} movie={movie}
          grade={grades[String(movie.tmdb_id)]} disabled={disabled} onGrade={onGrade} review={review?.find((item) => item.tmdb_id === movie.tmdb_id)} />)}
      </Box>
    </Box>
  );
};
