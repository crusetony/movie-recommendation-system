import React from 'react';
import { Button, Stack } from '@mui/material';
import { BookmarkBorderRounded, FavoriteBorderRounded, VisibilityOutlined } from '@mui/icons-material';
import { MovieLibraryAction, MovieLibraryItem } from '../types/index.ts';

export const MovieLibraryActions = ({ item, onToggle, includeWatched = true }: {
  item?: MovieLibraryItem; onToggle: (action: MovieLibraryAction) => void; includeWatched?: boolean;
}) => <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap className="cinema-library-actions" aria-label="片单操作">
  {([
    ['favorite', '收藏', item?.favorite, <FavoriteBorderRounded fontSize="small" />],
    ['want_to_watch', '想看', item?.watch_state === 'want_to_watch', <BookmarkBorderRounded fontSize="small" />],
    ['watched', '看过', item?.watch_state === 'watched', <VisibilityOutlined fontSize="small" />],
  ] as const).filter(([action]) => includeWatched || action !== 'watched').map(([action, label, selected, icon]) => <Button key={action} size="small"
    variant={selected ? 'contained' : 'outlined'} startIcon={icon}
    aria-pressed={Boolean(selected)} onClick={() => onToggle(action)}>{label}</Button>)}
</Stack>;
