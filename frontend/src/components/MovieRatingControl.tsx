import React, { useState } from 'react';
import { Box, Button, Rating, Stack, Typography } from '@mui/material';

export const MovieRatingControl = ({ movieId, title, rating, onChange, context }: {
  movieId: number; title: string; rating: number | null; context: 'result' | 'details' | 'library' | 'catalog' | 'history';
  onChange: (rating: number | null) => void;
}) => {
  const [hover, setHover] = useState(-1);
  const displayed = hover >= 0 ? Math.round(hover * 2) : rating;
  return <Box className="cinema-personal-rating" aria-label={`${title}个人评分`}>
    <Stack direction="row" spacing={1.25} flexWrap="wrap" useFlexGap alignItems="center">
      <Typography variant="caption" className="cinema-personal-rating__label">我的评分</Typography>
      <Rating name={`personal-rating-${context}-${movieId}`} value={rating === null ? null : rating / 2}
        max={5} precision={0.5} getLabelText={value => `${Math.round(value * 2)} 分`}
        emptyLabelText="未评分" onChange={(_event, value) => { setHover(-1); onChange(value === null ? null : Math.round(value * 2)); }}
        onChangeActive={(_event, value) => setHover(value)} />
      <Typography variant="body2" className="cinema-personal-rating__value" aria-live="polite">
        {displayed === null ? '尚未评分' : `${displayed} / 10`}
      </Typography>
      {rating !== null && <Button size="small" variant="text" color="inherit" onClick={() => { setHover(-1); onChange(null); }}>清除评分</Button>}
    </Stack>
  </Box>;
};
