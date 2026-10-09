import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Paper,
  Stack,
  TextField,
  Typography,
  Autocomplete,
  Avatar,
  IconButton,
} from '@mui/material';
import {
  Autorenew as AutorenewIcon,
  Movie as MovieIcon,
  Search as SearchIcon,
  UploadFile as UploadFileIcon,
  DeleteOutline as DeleteOutlineIcon,
} from '@mui/icons-material';

import { apiService } from '../services/api.ts';
import {
  MovieLibraryAction,
  MovieLibraryItem,
  MovieSummary,
  MovieSearchResult,
  RecommendationResponse,
  RecommendationHistoryEntry,
  RecommendationHistoryRequest,
} from '../types/index.ts';
import { MovieResultCard } from './MovieResultCard.tsx';
import { buildRatingFeedback } from '../services/ratingFeedback.ts';
import { moviePromptIdeas } from '../data/moviePromptIdeas.ts';

import { RecommendationDetails } from './RecommendationDetails.tsx';

interface MovieRecommendationProps {
  systemReady: boolean;
  onRunCompleted?: () => void;
  library: MovieLibraryItem[];
  onLibraryToggle: (movie: MovieSummary, action: MovieLibraryAction) => void;
  onRatingChange: (movie: MovieSummary, rating: number | null) => void;
  onViewMovie: (movie: MovieSummary) => void;
  referenceRequest: MovieSummary | null;
  onReferenceHandled: () => void;
  onBusyChange: (busy: boolean) => void;
  onSaveHistory?: (request: RecommendationHistoryRequest, response: RecommendationResponse) => void;
  historyRequest?: RecommendationHistoryEntry | null;
  onHistoryHandled?: () => void;
}

export const MovieRecommendation: React.FC<MovieRecommendationProps> = ({
  systemReady,
  onRunCompleted,
  library, onLibraryToggle, onRatingChange,
  onViewMovie, referenceRequest, onReferenceHandled, onBusyChange,
  onSaveHistory, historyRequest, onHistoryHandled,
}) => {
  const [query, setQuery] = useState('');
  const [lastRequest, setLastRequest] = useState<{ query: string; image: File | null; movieId: number | null } | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RecommendationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restoredHistory, setRestoredHistory] = useState<{imageMissing:boolean} | null>(null);
  const [seenMovieIds, setSeenMovieIds] = useState<number[]>([]);
  const [nextDiscoverPage, setNextDiscoverPage] = useState(1);
  const [referenceImage, setReferenceImage] = useState<File | null>(null);
  const [referencePreview, setReferencePreview] = useState<string | null>(null);
  const [referenceMovie, setReferenceMovie] = useState<MovieSearchResult | null>(null);
  const [movieSearchText, setMovieSearchText] = useState('');
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [movieOptions, setMovieOptions] = useState<MovieSearchResult[]>([]);
  const [movieSearchLoading, setMovieSearchLoading] = useState(false);
  const watchedMovieIds = useMemo(() => library.filter(item => item.watch_state === 'watched').map(item => item.tmdb_id), [library]);
  const feedback = useMemo(() => buildRatingFeedback(library), [library]);

  useEffect(() => { onBusyChange(loading); }, [loading, onBusyChange]);

  useEffect(() => {
    if (!historyRequest || loading) return;
    setQuery(historyRequest.request.query);
    const reference=historyRequest.request.reference;
    setReferenceOpen(reference.kind !== 'none');
    setReferenceImage(null);
    setReferenceMovie(reference.kind==='movie'?reference.movie:null);
    setMovieSearchText(reference.kind==='movie'?reference.movie.title:'');
    setMovieOptions(reference.kind==='movie'?[reference.movie]:[]);
    setLastRequest(null);setSeenMovieIds([]);setNextDiscoverPage(1);setError(null);
    setRestoredHistory({imageMissing:reference.kind==='image'});
    onHistoryHandled?.();
  },[historyRequest,loading,onHistoryHandled]);

  useEffect(() => {
    if (!referenceRequest || loading) return;
    const replacing = Boolean(referenceImage || (referenceMovie && referenceMovie.tmdb_id !== referenceRequest.tmdb_id));
    if (!replacing || window.confirm(`将当前视觉参考更换为《${referenceRequest.title}》？已填写的观影需求会保留。`)) {
      setReferenceOpen(true);
      setReferenceMovie(referenceRequest);
      setReferenceImage(null);
      setMovieSearchText(referenceRequest.title);
      setError(null);
      setRestoredHistory(current=>current?{imageMissing:false}:null);
    }
    onReferenceHandled();
  }, [referenceRequest, loading, referenceImage, referenceMovie, onReferenceHandled]);

  useEffect(() => {
    if (!referenceImage) {
      setReferencePreview(null);
      return undefined;
    }
    const objectUrl = URL.createObjectURL(referenceImage);
    setReferencePreview(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [referenceImage]);

  useEffect(() => {
    const value = movieSearchText.trim();
    if (value.length < 2 || referenceMovie?.title === value) {
      setMovieOptions(referenceMovie ? [referenceMovie] : []);
      return undefined;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      setMovieSearchLoading(true);
      void apiService.searchMovies(value)
        .then(options => { if (active) setMovieOptions(options); })
        .catch(() => { if (active) setMovieOptions([]); })
        .finally(() => { if (active) setMovieSearchLoading(false); });
    }, 350);
    return () => { active = false; window.clearTimeout(timer); setMovieSearchLoading(false); };
  }, [movieSearchText, referenceMovie]);

  const handleReferenceImage = (file?: File) => {
    if (!file) return;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      setError('参考图片仅支持 JPEG、PNG 或 WebP');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setError('参考图片不能超过 5MB');
      return;
    }
    setReferenceOpen(true);
    setReferenceImage(file);
    setReferenceMovie(null);
    setMovieSearchText('');
    setError(null);
    setRestoredHistory(current=>current?{imageMissing:false}:null);
  };

  const requestRecommendations = async (isRefresh: boolean) => {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) {
      setError('请先描述你想看的电影');
      return;
    }
    if (!systemReady) {
      setError('TMDB 尚未连接，请先检查系统状态');
      return;
    }

    const sameQuery = Boolean(lastRequest && normalizedQuery === lastRequest.query && referenceImage === lastRequest.image
      && (referenceMovie?.tmdb_id ?? null) === lastRequest.movieId);
    const continuingQuery = isRefresh && sameQuery;
    const excludedIds = Array.from(new Set([...(continuingQuery ? seenMovieIds : []), ...watchedMovieIds]));
    const requestedPage = continuingQuery ? nextDiscoverPage : 1;
    setLoading(true);
    setError(null);

    try {
      const request = {
        query: normalizedQuery,
        max_recommendations: 20,
        exclude_movie_ids: excludedIds,
        discover_page: requestedPage,
        force_refresh: !continuingQuery,
        feedback,
      };
      const response = referenceImage || referenceMovie
        ? await apiService.recommendMoviesMultimodal({
            ...request,
            reference_image: referenceImage || undefined,
            reference_movie_id: referenceMovie?.tmdb_id,
          })
        : await apiService.recommendMovies(request);
      setResult(response);
      if (response.success) onSaveHistory?.({query:normalizedQuery,
        reference:referenceImage?{kind:'image'}:referenceMovie?{kind:'movie',movie:referenceMovie}:{kind:'none'},
        discover_page:requestedPage,is_refresh:continuingQuery,excluded_count:excludedIds.length,rating_count:feedback.length},response);
      if (!response.success) return;
      setLastRequest({ query: normalizedQuery, image: referenceImage, movieId: referenceMovie?.tmdb_id ?? null });
      const newIds = response.recommendations.map((movie) => movie.tmdb_id);
      setSeenMovieIds(
        continuingQuery ? Array.from(new Set([...seenMovieIds, ...newIds])) : newIds,
      );
      setNextDiscoverPage(response.next_discover_page ?? requestedPage);
      setRestoredHistory(null);
      onRunCompleted?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : '推荐请求失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    void requestRecommendations(false);
  };

  const visibleRecommendations = result?.recommendations || [];
  const intent = result?.query_analysis;
  const summary = intent ? [
    ...intent.genres, ...intent.excluded_genres.map(value => `排除${value}`), ...intent.origin_countries,
    ...(intent.year_from || intent.year_to ? [`${intent.year_from || '不限'}—${intent.year_to || '现在'}`] : []),
    ...(intent.runtime_max ? [`不超过${intent.runtime_max}分钟`] : []), ...intent.themes, ...intent.moods,
  ] : [];

  return (
    <Box className="cinema-query-panel">
      <Paper variant="outlined" className="cinema-composer" sx={{ p: { xs: 2, sm: 3 }, borderRadius: '16px', mb: 3 }}>
      {restoredHistory && <Alert severity={restoredHistory.imageMissing?'warning':'info'} sx={{mb:2}}>
        已填入历史需求，尚未发起推荐；再次推荐将使用当前片单评分，从第一页开始。
        {restoredHistory.imageMissing?'原参考图片未保存，请重新选择图片；也可直接发起文字推荐。':''}
      </Alert>}
      <Typography variant="h5" component="h2" gutterBottom>
        今晚，想看什么故事？
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>写下偏好或一段影评，寻找下一部电影。</Typography>

      <Box component="form" onSubmit={handleSubmit}>
        <TextField
          id="movie-query"
          fullWidth
          multiline
          minRows={3}
          label="观影需求"
          placeholder="例如：想看一部节奏快的韩国悬疑片；或粘贴一段影评，说明你喜欢的主题、叙事和视觉风格"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={loading}
          inputProps={{ maxLength: 3000 }}
          helperText={`${query.length} / 3000 字符`}
        />
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }} aria-label="观影灵感">
          <Button type="button" size="small" variant="outlined" className="cinema-inspiration"
            disabled={loading} onClick={() => {
              const choices = moviePromptIdeas.filter(text => text !== query);
              setQuery(choices[Math.floor(Math.random() * choices.length)]);
              document.getElementById('movie-query')?.focus({ preventScroll: true });
            }}>
            随机灵感 <span aria-hidden="true">↗</span>
          </Button>
        </Stack>
        <Stack direction="row" spacing={1.5} className="cinema-composer-actions" sx={{ mt: 2 }}>
          <Button
            type="submit"
            variant="contained"
            disabled={loading || !systemReady || !query.trim()}
            startIcon={loading ? <CircularProgress size={18} /> : <SearchIcon />}
          >
            {loading ? '正在实时查询…' : '获取推荐'}
          </Button>
          {result && !restoredHistory && (
            <Button
              type="button"
              variant="outlined"
              disabled={loading || !systemReady || !result.has_more}
              startIcon={<AutorenewIcon />}
              onClick={() => void requestRecommendations(true)}
            >
              {result.has_more ? '换一批' : '已展示全部'}
            </Button>
          )}
        </Stack>
        <Button type="button" className="cinema-reference-toggle" size="small" aria-expanded={referenceOpen}
          aria-controls="visual-reference" onClick={() => setReferenceOpen(value => !value)} disabled={loading}>
          {referenceImage ? '图片参考已添加' : referenceMovie ? `参考电影：${referenceMovie.title}` : '添加图片或参考电影'} {referenceOpen ? '−' : '+'}
        </Button>
        <Box id="visual-reference" className="cinema-reference-editor" sx={{ display: referenceOpen ? 'block' : 'none', mt: 1 }}>
        <Box>
          <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 1.5 }}>
            上传一张图片，或从 TMDB 选择一部参考电影。两种方式只会启用其中一种。
          </Typography>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems={{ md: 'flex-start' }}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Button
                component="label"
                variant="outlined"
                startIcon={<UploadFileIcon />}
                disabled={loading}
              >
                {referenceImage ? '更换图片' : '上传参考图片'}
                <input
                  hidden
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={(event) => {
                    handleReferenceImage(event.target.files?.[0]);
                    event.target.value = '';
                  }}
                />
              </Button>
              {referenceImage && referencePreview && (
                <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mt: 1.5 }}>
                  <Box
                    component="img"
                    src={referencePreview}
                    alt="本地参考图预览"
                    sx={{ width: 72, height: 96, objectFit: 'cover', borderRadius: 1 }}
                  />
                  <Box sx={{ minWidth: 0, flex: 1 }}>
                    <Typography variant="body2" noWrap title={referenceImage.name}>
                      {referenceImage.name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">来源：本地上传</Typography>
                  </Box>
                  <IconButton
                    aria-label="删除参考图片"
                    onClick={() => setReferenceImage(null)}
                    disabled={loading}
                  >
                    <DeleteOutlineIcon />
                  </IconButton>
                </Stack>
              )}
            </Box>

            <Autocomplete
              sx={{ flex: 1, minWidth: { xs: '100%', md: 320 } }}
              options={movieOptions}
              value={referenceMovie}
              inputValue={movieSearchText}
              loading={movieSearchLoading}
              disabled={loading}
              filterOptions={(options) => options}
              isOptionEqualToValue={(option, value) => option.tmdb_id === value.tmdb_id}
              getOptionLabel={(option) => option.title}
              onInputChange={(_event, value) => setMovieSearchText(value)}
              onChange={(_event, value) => {
                setReferenceMovie(value);
                if (value) {setReferenceImage(null);setRestoredHistory(current=>current?{imageMissing:false}:null);}
              }}
              renderOption={(props, option) => (
                <Box component="li" {...props} key={option.tmdb_id}>
                  <Avatar
                    variant="rounded"
                    src={option.poster_url || undefined}
                    alt=""
                    sx={{ width: 36, height: 52, mr: 1.5 }}
                  >
                    <MovieIcon fontSize="small" />
                  </Avatar>
                  <Box>
                    <Typography variant="body2">{option.title}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {option.release_date?.slice(0, 4) || '年份未知'} · TMDB
                    </Typography>
                  </Box>
                </Box>
              )}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="选择参考电影"
                  placeholder="输入片名搜索 TMDB"
                  helperText={referenceMovie ? '来源：TMDB 电影海报' : '至少输入 2 个字符'}
                  InputProps={{
                    ...params.InputProps,
                    startAdornment: referenceMovie ? (
                      <Avatar
                        variant="rounded"
                        src={referenceMovie.poster_url || undefined}
                        alt={`${referenceMovie.title}海报`}
                        sx={{ width: 28, height: 40, mr: 1 }}
                      />
                    ) : params.InputProps.startAdornment,
                    endAdornment: (
                      <>
                        {movieSearchLoading ? <CircularProgress size={18} /> : null}
                        {params.InputProps.endAdornment}
                      </>
                    ),
                  }}
                />
              )}
            />
          </Stack>
          {referenceMovie && <Button size="small" sx={{ mt: 1 }} onClick={() => onViewMovie(referenceMovie)}>
            查看《{referenceMovie.title}》详情
          </Button>}
        </Box>
        </Box>
        {feedback.length > 0 && (
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1}
            alignItems={{ sm: 'center' }}
            justifyContent="space-between"
            sx={{ mt: 1.5 }}
          >
            <Typography variant="caption" color="text.secondary">
              下一次推荐将参考 {feedback.length} 部个人评分
            </Typography>

          </Stack>
        )}
      </Box>

      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && <Box role="status" aria-live="polite" sx={{ mb: 3 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>正在寻找与你共鸣的故事…</Typography>
        <LinearProgress sx={{ height: 3, borderRadius: 2 }} />
      </Box>}

      {result && (
        <Box>
          <Box className="cinema-results-heading" sx={{ mb: 2 }}>
            <Typography variant="h6" component="h2">{restoredHistory?'上次推荐结果':'实时推荐结果'}（{visibleRecommendations.length} 部）</Typography>
            {restoredHistory && <Typography variant="body2" color="text.secondary">下方仍为上次响应，原需求：{result.user_query}</Typography>}
            <Typography variant="body2" color="text.secondary" className="cinema-intent-summary">
              已理解：{summary.slice(0, 4).join(' · ') || '按当前文字需求推荐'}{summary.length > 4 ? ` · 另${summary.length - 4}项` : ''}
            </Typography>
          </Box>
          {result.warnings.map(warning => <Alert key={warning} severity="warning" sx={{ mb: 1 }}>{warning}</Alert>)}
          {result.diagnostics.semantic_fallback_reason && <Alert severity="warning" sx={{ mb: 1 }}>文本语义已降级：{result.diagnostics.semantic_fallback_reason}</Alert>}
          {result.diagnostics.visual_reference_source && result.diagnostics.visual_fallback_reason && <Alert severity="warning" sx={{ mb: 1 }}>视觉参考未完整参与：{result.diagnostics.visual_fallback_reason}</Alert>}
          {result.diagnostics.feedback_count > 0 && result.diagnostics.feedback_fallback_reason && <Alert severity="warning" sx={{ mb: 1 }}>评分反馈：{result.diagnostics.feedback_fallback_reason}</Alert>}
          <RecommendationDetails result={result} />
          {visibleRecommendations.length > 0 ? (
            visibleRecommendations.map((movie, index) => (
              <MovieResultCard
                key={movie.tmdb_id} movie={movie} index={index}
                onViewMovie={onViewMovie}
                libraryItem={library.find(item => item.tmdb_id === movie.tmdb_id)}
                onLibraryToggle={onLibraryToggle}
                onRatingChange={onRatingChange}
              />
            ))
          ) : (
            <Alert severity="info">没有找到合适的电影，请尝试减少限制条件。</Alert>
          )}
        </Box>
      )}
    </Box>
  );
};
