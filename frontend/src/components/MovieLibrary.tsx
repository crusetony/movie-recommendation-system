import React, { useState } from 'react';
import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { MovieOutlined } from '@mui/icons-material';
import { MovieLibraryAction, MovieLibraryItem, MovieSummary } from '../types/index.ts';
import { MovieLibraryActions } from './MovieLibraryActions.tsx';
import { MovieRatingControl } from './MovieRatingControl.tsx';
import { MovieRatingProfile } from './MovieRatingProfile.tsx';

const LibraryPoster = ({ item, onOpen }: { item: MovieLibraryItem; onOpen: () => void }) => {
  const [failed, setFailed] = useState(false);
  return <button type="button" className="cinema-library-poster" onClick={onOpen} aria-label={`查看${item.title}详情`}>
    {item.poster_url && !failed ? <img src={item.poster_url} loading="lazy" alt={`${item.title}海报`} onError={() => setFailed(true)} />
      : <span className="cinema-image-placeholder"><MovieOutlined />海报暂不可用</span>}
  </button>;
};

export const MovieLibrary = ({ items, warning, onViewMovie, onToggle, onRatingChange, onRemove, onClear, onRecommend }: {
  items: MovieLibraryItem[]; warning: string | null;
  onViewMovie: (movie: MovieSummary) => void;
  onToggle: (movie: MovieSummary, action: MovieLibraryAction) => void;
  onRatingChange: (movie: MovieSummary, rating: number | null) => void;
  onRemove: (movieId: number) => void; onClear: () => void; onRecommend: () => void;
}) => {
  const [filter, setFilter] = useState<'all' | MovieLibraryAction>('all');
  const [search, setSearch] = useState('');
  const [pendingRemove, setPendingRemove] = useState<MovieLibraryItem | null>(null);
  const activeFilter = filter === 'favorite' || filter === 'want_to_watch' || filter === 'watched' ? filter : 'all';
  const visible = items.filter(item => (activeFilter === 'all' || (activeFilter === 'favorite' ? item.favorite : item.watch_state === activeFilter))
    && `${item.title} ${item.original_title}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return <Box className="cinema-library">
    <Stack direction="row" justifyContent="space-between" alignItems="start" spacing={2} sx={{ mb: 2 }}>
      <Box>
        <Typography variant="h5" component="h2">我的片单 <Typography component="span" color="text.secondary" variant="body2">{items.length} 部</Typography></Typography></Box>
      {items.length > 0 && <Button size="small" color="inherit" onClick={() => {
        if (window.confirm('清空本机的片单和个人评分？评分偏好也会一并清除。')) onClear();
      }}>清空片单</Button>}
    </Stack>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>记录收藏、想看、看过与个人评分 · 保存在当前浏览器</Typography>
    {warning && <Alert severity="warning" sx={{ mb: 2 }}>{warning}</Alert>}
    <MovieRatingProfile items={items} onViewMovie={onViewMovie} />
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} sx={{ mb: 2.5 }}>
      <TextField size="small" label="搜索我的片单" value={search} onChange={event => setSearch(event.target.value)} sx={{ flex: 1 }} />
      <TextField select size="small" label="片单分类" value={activeFilter} onChange={event => setFilter(event.target.value as 'all' | MovieLibraryAction)} sx={{ minWidth: 160 }}>
        {[['all', '全部'], ['favorite', '收藏'], ['want_to_watch', '想看'], ['watched', '看过']].map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
      </TextField>
    </Stack>
    {visible.length === 0 ? <Alert severity="info" action={items.length === 0 ? <Button color="inherit" onClick={onRecommend}>去选片</Button> : undefined}>
      {items.length === 0 ? '还没有收藏的故事。在电影详情或推荐结果中，把喜欢的电影加入片单。' : '没有符合当前搜索或分类的电影。'}</Alert>
      : <Box className="cinema-library-grid">{visible.map(item => <Box key={item.tmdb_id} className="cinema-library-card">
        <LibraryPoster key={item.poster_url || item.tmdb_id} item={item} onOpen={() => onViewMovie(item)} />
        <Box className="cinema-library-card__body">
          <button className="cinema-library-title" type="button" onClick={() => onViewMovie(item)}>{item.title}</button>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ my: 1 }}>
            {item.release_date && <Chip size="small" label={item.release_date.slice(0, 4)} />}
            {(item.genres || []).slice(0, 3).map(genre => <Chip key={genre} size="small" variant="outlined" label={genre} />)}
          </Stack>
          <Typography variant="body2" color="text.secondary" className="cinema-library-overview">{item.overview || '暂无简介，可打开详情查看。'}</Typography>
          <MovieLibraryActions item={item} onToggle={action => onToggle(item, action)} />
          <MovieRatingControl movieId={item.tmdb_id} title={item.title} context="library" rating={item.personal_rating ?? null}
            onChange={rating => onRatingChange(item, rating)} />
          <Stack direction="row" justifyContent="space-between" sx={{ mt: 1.5 }}>
            <Button size="small" onClick={() => onViewMovie(item)}>电影详情 ↗</Button>
            <Button size="small" color="inherit" onClick={() => {
              if (activeFilter === 'all') setPendingRemove(item);
              else onToggle(item, activeFilter);
            }}>移除</Button>
          </Stack>
        </Box>
      </Box>)}</Box>}
    <Dialog open={pendingRemove !== null} onClose={() => setPendingRemove(null)} aria-labelledby="library-remove-title">
      <DialogTitle id="library-remove-title">移除这部电影？</DialogTitle>
      <DialogContent><DialogContentText>
        将《{pendingRemove?.title}》从片单中移除？这部电影的收藏、想看、看过标记和个人评分都会删除。
      </DialogContentText></DialogContent>
      <DialogActions>
        <Button onClick={() => setPendingRemove(null)}>取消</Button>
        <Button color="error" onClick={() => {
          if (pendingRemove) onRemove(pendingRemove.tmdb_id);
          setPendingRemove(null);
        }}>确认移除</Button>
      </DialogActions>
    </Dialog>
  </Box>;
};
