import React, { useId, useMemo, useState } from 'react';
import { Box, Button, Tab, Tabs, Typography } from '@mui/material';
import { MovieLibraryItem, MovieSummary } from '../types/index.ts';
import { buildRatingProfile, ProfileDimensionKey, RatingEvidence } from '../services/ratingProfile.ts';

const EvidenceList = ({ evidence, onViewMovie }: { evidence: RatingEvidence[]; onViewMovie: (movie: MovieSummary) => void }) =>
  <ul className="cinema-profile-evidence">{evidence.map(({ movie, rating }) => <li key={movie.tmdb_id}>
    <button type="button" onClick={() => onViewMovie(movie)} aria-label={`查看评分来源：${movie.title}，${rating}分`}>{movie.title} ↗</button>
    <span>{rating} / 10</span>
  </li>)}</ul>;

const directionSummary = (count: number, label: string) => `${label} ${count} 部${count === 1 ? ' · 单部，样本不足' : ''}`;

export const MovieRatingProfile = ({ items, onViewMovie }: {
  items: MovieLibraryItem[]; onViewMovie: (movie: MovieSummary) => void;
}) => {
  const profile = useMemo(() => buildRatingProfile(items), [items]);
  const [dimensionKey, setDimensionKey] = useState<ProfileDimensionKey>('genres');
  const [showAll, setShowAll] = useState(false);
  const id = useId();
  const dimension = profile.dimensions.find(value => value.key === dimensionKey)!;
  const groups = showAll ? dimension.groups : dimension.groups.slice(0, 4);
  return <details className="cinema-rating-profile">
    <summary>
      <span className="cinema-rating-profile__title">评分偏好画像</span>
      <span className="cinema-rating-profile__count" aria-live="polite">有效评分 {profile.ratedCount} 部 · 正向 {profile.counts.positive} / 负向 {profile.counts.negative} / 中性 {profile.counts.neutral}</span>
      <span className="cinema-rating-profile__toggle" aria-hidden="true">⌄</span>
    </summary>
    <Box className="cinema-rating-profile__body">
      <Typography variant="body2" color="text.secondary">7–10 分正向，1–5 分负向，6 分中性。未评分 {profile.unratedCount} 部不参与；收藏、想看不推断喜好。这里展示整张片单的评分，与片名搜索及分类无关。</Typography>
      <Typography variant="caption" component="p" color="text.secondary" sx={{ mt: 1 }}>同一类型可以同时有高分和低分，各方向独立统计、不抵消；一部电影可计入多个类型或地区。重复记录也只是已评分样本，不代表稳定喜好、厌恶或统计结论。</Typography>
      {profile.ratedCount === 0 ? <Typography sx={{ mt: 2 }}>尚无有效评分，画像证据不足。给看过的电影打分后，会在这里显示来源。</Typography> : <>
        {profile.counts.positive + profile.counts.negative < 2 && <Typography variant="body2" className="cinema-profile-note" sx={{ mt: 2 }}>
          {profile.counts.positive + profile.counts.negative === 0 ? '当前评分全为中性，不形成方向性偏好。' : '仅有 1 部方向性评分，样本不足，先保留单部记录。'}
        </Typography>}
        <Tabs value={dimensionKey} variant="scrollable" scrollButtons="auto" aria-label="评分画像维度"
          onChange={(_, value) => { setDimensionKey(value); setShowAll(false); }} className="cinema-profile-tabs">
          {profile.dimensions.map(value => <Tab key={value.key} value={value.key} label={value.label}
            id={`${id}-${value.key}-tab`} aria-controls={`${id}-${value.key}-panel`} />)}
        </Tabs>
        <Box role="tabpanel" id={`${id}-${dimension.key}-panel`} aria-labelledby={`${id}-${dimension.key}-tab`}>
          <Typography variant="caption" component="p" color="text.secondary" sx={{ my: 1.5 }}>
            {dimension.label}资料有效：正向 {dimension.known.positive} / {profile.counts.positive} 部，负向 {dimension.known.negative} / {profile.counts.negative} 部，中性 {dimension.known.neutral} / {profile.counts.neutral} 部；缺失 {dimension.missing.length} 部。
          </Typography>
          {dimension.key === 'director' && <Typography variant="caption" component="p" color="text.secondary" sx={{ mb: 1.5 }}>按片单已存署名汇总，可能只包含首位导演；不补齐联合导演、不合并别名。</Typography>}
          {dimension.key === 'countries' && <Typography variant="caption" component="p" color="text.secondary" sx={{ mb: 1.5 }}>沿用已存国家 / 地区名称或代码，不推断导演国籍。</Typography>}
          {groups.length === 0 && <Typography variant="body2" className="cinema-profile-note">{profile.counts.positive + profile.counts.negative === 0 ? '中性评分不进入高低分统计。' : `缺少可用${dimension.label}资料，方向性证据不足。`}</Typography>}
          {groups.map(group => <details key={`${dimension.key}-${group.label}`} className="cinema-profile-group">
            <summary><span className="cinema-profile-group__label">{group.label}</span>
              <span className="cinema-profile-positive">{directionSummary(group.positive.length, '正向')}</span>
              <span className="cinema-profile-negative">{directionSummary(group.negative.length, '负向')}</span>
              <span className="cinema-profile-group__source">来源 ⌄</span></summary>
            <Box className="cinema-profile-group__body">
              {group.positive.length > 0 && <><Typography variant="caption" className="cinema-profile-positive">正向来源 · 7–10 分</Typography><EvidenceList evidence={group.positive} onViewMovie={onViewMovie} /></>}
              {group.negative.length > 0 && <><Typography variant="caption" className="cinema-profile-negative">负向来源 · 1–5 分</Typography><EvidenceList evidence={group.negative} onViewMovie={onViewMovie} /></>}
            </Box>
          </details>)}
          {dimension.groups.length > 4 && <Button size="small" onClick={() => setShowAll(value => !value)} sx={{ mt: 1 }}>{showAll ? '收起更多条目' : `查看全部 ${dimension.groups.length} 项`}</Button>}
          {dimension.missing.length > 0 && <details key={dimension.key} className="cinema-profile-extra">
            <summary>{dimension.label}资料缺失 / 无效：{dimension.missing.length} 部 · 不推断</summary>
            <EvidenceList evidence={dimension.missing} onViewMovie={onViewMovie} />
          </details>}
        </Box>
        {profile.neutral.length > 0 && <details className="cinema-profile-extra"><summary>中性来源：{profile.neutral.length} 部 · 6 分不形成方向</summary><EvidenceList evidence={profile.neutral} onViewMovie={onViewMovie} /></details>}
      </>}
      <Typography variant="caption" component="p" color="text.secondary" sx={{ mt: 2 }}>仅用已存公开元数据，不推断摄影、节奏或情绪。改分、清除评分及移除后随片单同步更新。画像在本页派生，不单独保存或再次加入推荐计分；后续推荐仍使用最近最多 200 条评分。</Typography>
    </Box>
  </details>;
};
