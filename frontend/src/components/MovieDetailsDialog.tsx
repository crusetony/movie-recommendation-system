import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Dialog, DialogContent, DialogTitle,
  IconButton, Stack, Typography, useMediaQuery, useTheme,
} from '@mui/material';
import { CloseRounded, MovieOutlined } from '@mui/icons-material';
import { apiService } from '../services/api.ts';
import { MovieDetails, MovieLibraryAction, MovieLibraryItem, MovieSummary } from '../types/index.ts';
import { MovieLibraryActions } from './MovieLibraryActions.tsx';
import { MovieRatingControl } from './MovieRatingControl.tsx';

export const MovieDetailsDialog = ({ movie, onClose, onUseReference, referenceDisabled, libraryItem, onLibraryToggle, onRatingChange }: {
  movie: MovieSummary | null; onClose: () => void; onUseReference: (movie: MovieSummary) => void;
  referenceDisabled?: boolean; libraryItem?: MovieLibraryItem;
  onLibraryToggle: (movie: MovieSummary, action: MovieLibraryAction) => void;
  onRatingChange: (movie: MovieSummary, rating: number | null) => void;
}) => {
  const [loaded, setLoaded] = useState<MovieDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [failedPoster, setFailedPoster] = useState<string | null>(null);
  const mobile = useMediaQuery(useTheme().breakpoints.down('sm'));
  const movieId = movie?.tmdb_id;

  useEffect(() => {
    setLoaded(null); setError(null); setFailedPoster(null);
    if (!movieId) { setLoading(false); return; }
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    void apiService.getMovieDetails(movieId, controller.signal)
      .then(details => { if (active) setLoaded(details); })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '电影详情读取失败'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [movieId, retry]);

  // A previous response must never appear under the next movie's title.
  const details = loaded?.tmdb_id === movieId ? loaded : null;
  const displayed = details || movie;
  const posterUrl = displayed?.poster_url;
  return <Dialog open={Boolean(movie)} onClose={onClose} fullWidth maxWidth="md" fullScreen={mobile}
    aria-labelledby="movie-detail-title" className="cinema-details-dialog">
    <DialogTitle component="div" id="movie-detail-header" sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2 }}>
      <Box><Typography variant="caption" className="cinema-section-label">电影档案 / FILM NOTES</Typography>
        <Typography component="h2" variant="h5" id="movie-detail-title">{displayed?.title}</Typography></Box>
      <IconButton onClick={onClose} aria-label="关闭电影详情"><CloseRounded /></IconButton>
    </DialogTitle>
    <DialogContent>
      {loading && <Stack direction="row" alignItems="center" spacing={1} role="status" sx={{ mb: 2 }}>
        <CircularProgress size={18} /><Typography variant="body2">正在读取 TMDB 电影详情…</Typography></Stack>}
      {error && <Alert severity="warning" sx={{ mb: 2 }} action={<Button color="inherit" onClick={() => setRetry(value => value + 1)}>重试</Button>}>
        {error}。下方保留已有电影信息。</Alert>}
      {displayed && <Box className="cinema-detail-layout">
        <Box className="cinema-detail-poster">
          {posterUrl && posterUrl !== failedPoster ? <img src={posterUrl} alt={`${displayed.title}海报`}
            onError={() => setFailedPoster(posterUrl)} /> : <Box className="cinema-image-placeholder"><MovieOutlined />海报暂不可用</Box>}
        </Box>
        <Box sx={{ minWidth: 0 }}>
          {displayed.original_title && displayed.original_title !== displayed.title && <Typography color="text.secondary" sx={{ mb: 1 }}>{displayed.original_title}</Typography>}
          {details?.tagline && <Typography className="cinema-detail-tagline">{details.tagline}</Typography>}
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
            {displayed.release_date && <Chip size="small" label={displayed.release_date} />}
            {Boolean(displayed.runtime) && <Chip size="small" label={`${displayed.runtime} 分钟`} />}
            {displayed.vote_average != null && <Chip size="small" label={`TMDB ${displayed.vote_average.toFixed(1)} · ${displayed.vote_count ?? 0} 人`} />}
            {(displayed.genres || []).map(genre => <Chip key={genre} size="small" variant="outlined" label={genre} />)}
          </Stack>
          <Typography component="h3" variant="subtitle2" sx={{ mb: 1 }}>剧情简介</Typography>
          <Typography className="cinema-detail-overview">{displayed.overview || '暂无简介'}</Typography>
          <Box component="dl" className="cinema-detail-facts">
            <dt>导演</dt><dd>{displayed.director || '暂缺'}</dd>
            <dt>国家 / 地区</dt><dd>{displayed.production_countries?.join('、') || '暂缺'}</dd>
            <dt>对白语言</dt><dd>{details?.spoken_languages.join('、') || '暂缺'}</dd>
          </Box>
          <MovieLibraryActions item={libraryItem} onToggle={action => onLibraryToggle(displayed, action)} />
          <MovieRatingControl key={displayed.tmdb_id} movieId={displayed.tmdb_id} title={displayed.title} context="details"
            rating={libraryItem?.personal_rating ?? null} onChange={rating => onRatingChange(displayed, rating)} />
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 2 }}>
            <Button variant="contained" disabled={referenceDisabled} onClick={() => onUseReference(displayed)}>设为参考电影</Button>
            <Button component="a" href={`https://www.themoviedb.org/movie/${displayed.tmdb_id}`} target="_blank" rel="noopener noreferrer">TMDB 详情 ↗</Button>
          </Stack>
          <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>设为参考后可继续填写需求，点击获取推荐再开始查询。</Typography>
        </Box>
      </Box>}
      {displayed && <Box sx={{ mt: 3 }}>
        <Typography component="h3" variant="subtitle2" sx={{ mb: 1.5 }}>主要演员</Typography>
        {details?.cast_roles.length ? <Box className="cinema-detail-cast">{details.cast_roles.map((person, index) => <Box key={`${person.name}-${index}`}>
          <Typography variant="body2">{person.name}</Typography><Typography variant="caption" color="text.secondary">{person.character || '角色未提供'}</Typography>
        </Box>)}</Box> : <Typography variant="body2" color="text.secondary">{displayed.cast?.join('、') || '暂缺演职员信息'}</Typography>}
      </Box>}
    </DialogContent>
  </Dialog>;
};
