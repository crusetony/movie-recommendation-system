import React, { useState } from 'react';
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Card,
  CardContent, CardMedia, Chip, Divider, LinearProgress, Stack, Typography,
  useMediaQuery,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon, Movie as MovieIcon,
  CheckCircleOutlineRounded,
} from '@mui/icons-material';
import { MovieLibraryAction, MovieLibraryItem, MovieSummary, MovieRecommendation as MovieRecommendationType } from '../types/index.ts';
import { MotionReveal } from '../motion/MotionReveal.tsx';
import { MovieLibraryActions } from './MovieLibraryActions.tsx';
import { MovieRatingControl } from './MovieRatingControl.tsx';

const MoviePoster = ({ movie, onOpen }: { movie: MovieRecommendationType; onOpen?: () => void }) => {
  const [imageFailed, setImageFailed] = useState(false);
  return <Box className="cinema-poster" {...(onOpen ? {
    component: 'button', type: 'button', onClick: onOpen, 'aria-label': `查看${movie.title}详情`,
  } : {})}>
    <div className="cinema-poster__surface">
      {movie.poster_url && !imageFailed ? <CardMedia
        component="img" alt={`${movie.title}海报`} src={movie.poster_url} loading="lazy"
        draggable={false}
        onError={() => setImageFailed(true)} className="cinema-poster__image"
        sx={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center' }}
      /> : <Stack alignItems="center" justifyContent="center" spacing={1} sx={{ width: '100%', height: '100%' }}>
        <MovieIcon sx={{ fontSize: 56, color: '#8198b1' }} />
        <Typography variant="caption" sx={{ color: '#bfcede' }}>海报暂不可用</Typography>
      </Stack>}
    </div>
  </Box>;
};

interface MovieResultCardProps {
  movie: MovieRecommendationType;
  index: number;
  onViewMovie?: (movie: MovieSummary) => void;
  libraryItem?: MovieLibraryItem;
  onLibraryToggle?: (movie: MovieSummary, action: MovieLibraryAction) => void;
  onRatingChange?: (movie: MovieSummary, rating: number | null) => void;
  ratingContext?: 'result' | 'history';
}

// Rendering stays separate from requests and feedback persistence so it can also
// be checked with saved responses, without making a new recommendation request.
export const MovieResultCard = ({ movie, index, onViewMovie, libraryItem, onLibraryToggle, onRatingChange, ratingContext = 'result' }: MovieResultCardProps) => {
  const [overviewExpanded, setOverviewExpanded] = useState(false);
  const overviewId = `overview-${ratingContext}-${movie.tmdb_id}`;
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const watched = libraryItem?.watch_state === 'watched';
  return (
    <MotionReveal key={movie.tmdb_id} index={index}>
      <Card
        key={movie.tmdb_id}
        variant="outlined"
        className={`cinema-movie-card${watched ? ' cinema-movie-card--watched' : ''}`}
        data-watched={watched}
        sx={{ mb: 2.5, overflow: 'clip' }}
      >
        <span className="cinema-movie-index" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
        <Box className="cinema-movie-layout">
          <MoviePoster key={movie.poster_url || movie.tmdb_id} movie={movie} onOpen={onViewMovie ? () => onViewMovie(movie) : undefined} />

          <CardContent className="cinema-movie-content">
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="baseline">
              <Typography variant="h6" component="h3" className="cinema-movie-title">
                {onViewMovie ? <button type="button" className="cinema-film-title-link" onClick={() => onViewMovie(movie)}>{movie.title}</button> : movie.title}
              </Typography>
              {watched && <Chip icon={<CheckCircleOutlineRounded />} size="small" label="已看过" className="cinema-watched-tag" />}
              {movie.original_title && movie.original_title !== movie.title && (
                <Typography variant="body2" color="text.secondary">
                  {movie.original_title}
                </Typography>
              )}
            </Stack>

            <Typography variant="body2" color="text.secondary" className="cinema-movie-metadata" sx={{ my: 1 }}>
              {[
                `TMDB ${movie.vote_average.toFixed(1)}（${movie.vote_count}人）`,
                movie.release_date?.slice(0, 4), movie.runtime ? `${movie.runtime}分钟` : null,
                ...movie.genres,
              ].filter(Boolean).join(' · ')}
            </Typography>
            {!onViewMovie && movie.director && <Typography variant="caption" color="text.secondary">导演 {movie.director}</Typography>}
            <Box className="cinema-movie-synopsis">
              <Typography component="p" variant="caption" className="cinema-movie-section-label">剧情简介</Typography>
              <Typography variant="body2" component="p" id={overviewId} className="cinema-movie-overview" data-expanded={overviewExpanded}>
                {movie.overview || '暂无中文简介'}
              </Typography>
              {movie.overview && <Button type="button" size="small" color="inherit" className="cinema-overview-toggle"
                aria-expanded={overviewExpanded} aria-controls={overviewId} onClick={() => setOverviewExpanded(value => !value)}>
                {overviewExpanded ? '收起简介' : '展开简介'}
              </Button>}
            </Box>

            <Alert severity="info" icon={false} className="cinema-movie-reason">
              {movie.reasoning}
            </Alert>
            <Box className="cinema-movie-actions">
              {onLibraryToggle && <MovieLibraryActions item={libraryItem} onToggle={action => onLibraryToggle(movie, action)} />}
              {onRatingChange && <MovieRatingControl movieId={movie.tmdb_id} title={movie.title} context={ratingContext}
                rating={libraryItem?.personal_rating ?? null} onChange={rating => onRatingChange(movie, rating)} />}
              {onViewMovie ? <Button size="small" onClick={() => onViewMovie(movie)}>电影详情 ↗</Button>
                : <Button component="a" href={movie.tmdb_url} target="_blank" rel="noopener noreferrer" size="small">TMDB 详情 ↗</Button>}
            </Box>


          </CardContent>
        </Box>

        <Box className="cinema-movie-score-area">
          <Accordion
            disableGutters
            elevation={0}
            className="cinema-score"
            TransitionProps={{ timeout: reducedMotion ? 0 : 320 }}
            sx={{ border: 1, borderColor: 'divider', borderRadius: '12px !important' }}
          >
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Box sx={{ width: '100%', pr: 1 }}>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={0.5} justifyContent="space-between">
                  <Typography variant="subtitle2">算法评分构成</Typography>
                  <Typography variant="caption" color="text.secondary">
                    综合评分 {(movie.score_breakdown.final_score * 100).toFixed(1)} / 100
                  </Typography>
                </Stack>
              </Box>
            </AccordionSummary>
            <AccordionDetails sx={{ pt: 0 }}>
              <Stack spacing={1.5}>
                {movie.recall_sources.length > 0 && (
                  <Typography variant="caption" color="text.secondary">
                    召回来源：{movie.recall_sources.join('、')}
                  </Typography>
                )}

                {movie.score_breakdown.components.map((component, componentIndex) => (
                  <Box key={component.key} sx={{ opacity: component.available ? 1 : 0.55 }}>
                    <Stack direction="row" justifyContent="space-between" spacing={2}>
                      <Typography variant="body2">
                        {component.label}
                        {component.key === 'text_relevance'
                          ? component.algorithm === 'multilingual-minilm-cosine-v1'
                            ? '（向量语义）'
                            : '（词项降级）'
                          : ''}
                        {component.available
                          ? ` · 配置 ${(component.configured_weight * 100).toFixed(0)}% · 实际 ${(component.effective_weight * 100).toFixed(1)}%`
                          : ` · 配置 ${(component.configured_weight * 100).toFixed(0)}% · 未参与`}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {component.available ? `${(component.score * 100).toFixed(0)} 分` : '未参与'}
                      </Typography>
                    </Stack>
                    <LinearProgress
                      className="cinema-score__track"
                      style={{ transitionDelay: `${componentIndex * 35}ms` }}
                      variant="determinate"
                      value={component.available ? component.score * 100 : 0}
                      color={component.available ? 'primary' : 'inherit'}
                      sx={{ height: 7, borderRadius: 4, my: 0.5 }}
                    />
                    <Typography variant="caption" color="text.secondary">
                      {component.evidence.join('；')}
                    </Typography>
                    <Typography variant="caption" color="text.disabled" sx={{ display: 'block' }}>
                      算法：{component.algorithm}
                    </Typography>
                  </Box>
                ))}
                <Divider />
                <Box>
                  <Typography variant="body2">
                    多样性重排 · 融合排序第 {movie.rank_before_mmr} 位 → MMR 第 {movie.rank_after_mmr} 位
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    多样性惩罚 {(movie.diversity_penalty * 100).toFixed(1)}% · {movie.diversity_reason}
                  </Typography>
                </Box>
              </Stack>
            </AccordionDetails>
          </Accordion>
        </Box>
      </Card>
    </MotionReveal>
  );
};
