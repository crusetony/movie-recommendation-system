import React, { useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, MenuItem, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { MovieOutlined } from '@mui/icons-material';
import { apiService } from '../services/api.ts';
import { MovieCatalogQuery, MovieCatalogResponse, MovieGenre, MovieLibraryAction, MovieLibraryItem, MovieSummary } from '../types/index.ts';
import { MovieLibraryActions } from './MovieLibraryActions.tsx';
import { MovieRatingControl } from './MovieRatingControl.tsx';

const CatalogPoster = ({ movie, onView }: { movie: MovieSummary; onView: () => void }) => {
  const [failed, setFailed] = useState(false);
  return <button type="button" className="cinema-library-poster" onClick={onView} aria-label={`查看${movie.title}详情`}>
    {movie.poster_url && !failed ? <img src={movie.poster_url} alt={`${movie.title}海报`} loading="lazy" onError={() => setFailed(true)} />
      : <span className="cinema-image-placeholder"><MovieOutlined />海报暂不可用</span>}
  </button>;
};

export const MovieCatalog = ({ library, onViewMovie, onToggle, onRatingChange }: {
  library: MovieLibraryItem[]; onViewMovie: (movie: MovieSummary) => void;
  onToggle: (movie: MovieSummary, action: MovieLibraryAction) => void;
  onRatingChange: (movie: MovieSummary, rating: number | null) => void;
}) => {
  const [mode, setMode] = useState<'title' | 'discover'>('title');
  const [title, setTitle] = useState('');
  const [year, setYear] = useState('');
  const [genre, setGenre] = useState('');
  const [decade, setDecade] = useState('');
  const [genres, setGenres] = useState<MovieGenre[]>([]);
  const [genreError, setGenreError] = useState<string | null>(null);
  const [genreRetry, setGenreRetry] = useState(0);
  const [result, setResult] = useState<{ data: MovieCatalogResponse; request: MovieCatalogQuery; scrollToFirst: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const lastAttempt = useRef<{ request: MovieCatalogQuery; scrollToFirst: boolean } | null>(null);
  const firstMovie = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    setGenreError(null);
    void apiService.getMovieGenres(abort.signal).then(values => {
      if (!abort.signal.aborted) setGenres(values);
    }).catch(reason => {
      if (!abort.signal.aborted) setGenreError(reason instanceof Error ? reason.message : '类型列表读取失败');
    });
    return () => abort.abort();
  }, [genreRetry]);
  useEffect(() => () => { generation.current++; controller.current?.abort(); }, []);
  useEffect(() => {
    if (!result?.scrollToFirst || !firstMovie.current) return;
    firstMovie.current.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'start',
    });
    firstMovie.current.focus({ preventScroll: true });
  }, [result]);

  const load = async (request: MovieCatalogQuery, scrollToFirst = false) => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const current = ++generation.current;
    lastAttempt.current = { request, scrollToFirst };
    setBusy(true); setError(null);
    try {
      const data = await apiService.browseMovies(request, abort.signal);
      if (!abort.signal.aborted && current === generation.current) setResult({ data, request, scrollToFirst });
    } catch (reason) {
      if (!abort.signal.aborted && current === generation.current) setError(reason instanceof Error ? reason.message : '电影搜索失败');
    } finally {
      if (!abort.signal.aborted && current === generation.current) setBusy(false);
    }
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (mode === 'title') {
      if (!title.trim() || title.trim().length > 100) { lastAttempt.current = null; setError('请输入1–100字的片名。'); return; }
      if (year && (!/^\d{4}$/.test(year) || Number(year) < 1880 || Number(year) > 2100)) {
        lastAttempt.current = null; setError('上映年份须为1880–2100的四位整数。'); return;
      }
      void load({ mode, query: title.trim(), ...(year ? { year: Number(year) } : {}), page: 1 });
    } else void load({ mode, ...(genre ? { genre_id: Number(genre) } : {}), ...(decade ? { decade: Number(decade) } : {}), page: 1 });
  };
  const cancel = () => { generation.current++; controller.current?.abort(); setBusy(false); setError(null); };
  const description = result?.request.mode === 'title'
    ? `片名“${result.request.query}”${result.request.year ? ` · ${result.request.year}年` : ''}`
    : result ? `${genres.find(value => value.id === result.request.genre_id)?.name || '全部类型'} · ${result.request.decade ? `${result.request.decade}–${result.request.decade + 9}年` : '全部年代'}` : '';

  return <Box className="cinema-catalog">
    <Typography variant="h5" component="h2">电影搜索</Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mt: 1, mb: 2 }}>按片名查找，或按类型与年代浏览。</Typography>
    <Box component="form" onSubmit={submit} className="cinema-catalog-form">
      <ToggleButtonGroup size="small" exclusive value={mode} aria-label="电影检索方式" onChange={(_, value) => { if (value) setMode(value); }} sx={{ mb: 2 }}>
        <ToggleButton value="title">按片名搜索</ToggleButton><ToggleButton value="discover">按类型与年代浏览</ToggleButton>
      </ToggleButtonGroup>
      {mode === 'title' ? <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
        <TextField label="电影片名" value={title} onChange={event => setTitle(event.target.value)} size="small" inputProps={{ maxLength: 100 }} sx={{ flex: 1 }} />
        <TextField label="上映年份（可选）" value={year} onChange={event => setYear(event.target.value)} size="small" inputProps={{ inputMode: 'numeric', maxLength: 4 }} sx={{ width: { sm: 190 } }} />
      </Stack> : <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5}>
        <TextField select label="电影类型" value={genre} onChange={event => setGenre(event.target.value)} size="small" sx={{ flex: 1 }} InputLabelProps={{shrink:true}}
          SelectProps={{displayEmpty:true,renderValue:value => genres.find(item => String(item.id) === value)?.name || '全部类型'}}>
          <MenuItem value="">全部类型</MenuItem>{genres.map(value => <MenuItem key={value.id} value={String(value.id)}>{value.name}</MenuItem>)}
        </TextField>
        <TextField select label="上映年代" value={decade} onChange={event => setDecade(event.target.value)} size="small" sx={{ flex: 1 }} InputLabelProps={{shrink:true}}
          SelectProps={{displayEmpty:true,renderValue:value => value ? `${value}–${Number(value) + 9}年` : '全部年代'}}>
          <MenuItem value="">全部年代</MenuItem>{Array.from({ length: 15 }, (_, index) => 2020 - index * 10).map(value => <MenuItem key={value} value={String(value)}>{value}–{value + 9}年</MenuItem>)}
        </TextField>
      </Stack>}
      <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>{mode === 'title' ? '可选年份用于限定首映年份。' : '类型和年代在完整检索中生效，结果按TMDB热度排序。年代按首映日期筛选。'}</Typography>
      <Stack direction="row" spacing={1} sx={{ mt: 2 }}><Button type="submit" variant="contained" disabled={busy}>{mode === 'title' ? '搜索电影' : '浏览电影'}</Button>
        {busy && <Button onClick={cancel}>取消读取</Button>}</Stack>
    </Box>
    {genreError && <Alert severity="warning" sx={{ my: 2 }} action={<Button color="inherit" onClick={() => setGenreRetry(value => value + 1)}>重读类型</Button>}>{genreError}；类型选项暂不可用。</Alert>}
    {busy && <Stack role="status" direction="row" spacing={1} alignItems="center" sx={{ my: 2 }}><CircularProgress size={18} /><Typography variant="body2">正在读取电影…{result ? '下方保留上次结果。' : ''}</Typography></Stack>}
    {error && <Alert severity="error" sx={{ my: 2 }} action={lastAttempt.current && <Button color="inherit" disabled={busy} onClick={() => void load(lastAttempt.current!.request, lastAttempt.current!.scrollToFirst)}>重试</Button>}>{error}{result ? '；下方保留上次结果和原筛选范围。' : ''}</Alert>}
    {!result && !busy && !error && <Typography color="text.secondary" variant="body2" sx={{ mt: 3 }}>输入片名或选择条件，再开始检索。</Typography>}
    {result && <>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mt: 3, mb: 2 }}>
        <Typography component="h3" variant="subtitle1" className="cinema-catalog-result-heading">{description}</Typography>
        <Typography variant="caption" color="text.secondary">TMDB返回计数 {result.data.total_results.toLocaleString()} 部 · 当前 {result.data.movies.length} 部</Typography>
      </Stack>
      {result.data.pagination_limited && <Alert severity="info" sx={{ mb: 2 }}>计数由TMDB返回，不代表电影库总量。本次检索仅可浏览前500页，每页最多20部；{result.request.mode === 'title' ? '可补充片名或上映年份后分别检索。' : '可缩小类型或年代范围后分别浏览。'}</Alert>}
      {result.data.movies.length === 0 ? <Alert severity="info">当前检索页没有找到电影，可调整片名、年份或浏览条件。</Alert>
        : <Box className="cinema-library-grid">{result.data.movies.map((movie, index) => {
          const item = library.find(value => value.tmdb_id === movie.tmdb_id);
          // Partial search metadata must not erase richer, previously saved genres.
          const saved = { ...movie, genres: movie.genres?.length ? movie.genres : item?.genres };
          return <Box key={movie.tmdb_id} ref={index === 0 ? firstMovie : undefined} tabIndex={index === 0 ? -1 : undefined}
            sx={{ scrollMarginTop: '100px' }} className={`cinema-library-card cinema-catalog-card${item?.watch_state === 'watched' ? ' cinema-catalog-card--watched' : ''}`}>
            <CatalogPoster key={movie.poster_url || movie.tmdb_id} movie={movie} onView={() => onViewMovie(movie)} />
            <Box className="cinema-library-card__body">
              <button type="button" className="cinema-library-title" onClick={() => onViewMovie(movie)}>{movie.title}</button>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ my: 1 }}>
                {movie.release_date && <Chip label={movie.release_date.slice(0, 4)} size="small" />}
                {(movie.genres || []).map(value => <Chip key={value} label={value} size="small" variant="outlined" />)}
                {item?.watch_state === 'watched' && <Chip label="已看过" size="small" className="cinema-watched-tag" />}
              </Stack>
              {movie.vote_average != null && <Typography variant="caption" component="p" color="text.secondary">TMDB {movie.vote_average.toFixed(1)} · {movie.vote_count || 0}人评分</Typography>}
              <Typography variant="body2" className="cinema-library-overview" color="text.secondary" sx={{ mt: 1 }}>{movie.overview || '暂无简介，可打开详情查看。'}</Typography>
              <MovieLibraryActions item={item} onToggle={action => onToggle(saved, action)} />
              <MovieRatingControl movieId={movie.tmdb_id} title={movie.title} context="catalog" rating={item?.personal_rating ?? null} onChange={rating => onRatingChange(saved, rating)} />
              <Button size="small" onClick={() => onViewMovie(movie)}>电影详情 ↗</Button>
            </Box>
          </Box>;
        })}</Box>}
      <Stack direction="row" spacing={1.5} alignItems="center" justifyContent="center" sx={{ my: 3 }} aria-label="电影搜索分页">
        <Button variant="outlined" disabled={busy || result.data.page <= 1} onClick={() => void load({ ...result.request, page: result.data.page - 1 }, true)}>上一页</Button>
        <Typography variant="body2">第 {result.data.page} / {Math.max(result.data.available_pages, 1)} 页</Typography>
        <Button variant="outlined" disabled={busy || !result.data.has_next} onClick={() => void load({ ...result.request, page: result.data.page + 1 }, true)}>下一页</Button>
      </Stack>
    </>}
  </Box>;
};
