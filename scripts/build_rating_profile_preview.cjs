// Production library/profile components with synthetic, memory-only ratings.
// No API, localStorage or experiment-label access. Separate from the user's app.
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '..');
const frontendRequire = createRequire(path.join(root, 'frontend/package.json'));
const webpack = frontendRequire('webpack');
const output = path.join(root, 'experiments/validation/rating-profile-2026-10-08/preview');
fs.mkdirSync(output, { recursive: true });
fs.copyFileSync(path.join(root, 'frontend/src/index.css'), path.join(output, 'styles.css'));
fs.writeFileSync(path.join(output, 'preview-entry.js'), `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { ThemeProvider, CssBaseline, Box, Button, Typography } from '@mui/material';
import theme from ${JSON.stringify(path.join(root, 'frontend/src/theme.ts'))};
import { MovieLibrary } from ${JSON.stringify(path.join(root, 'frontend/src/components/MovieLibrary.tsx'))};
import { updateMovieLibrary, setMovieRating } from ${JSON.stringify(path.join(root, 'frontend/src/services/movieLibraryStorage.ts'))};
const sample = (id, title, rating, metadata = {}) => ({tmdb_id: id, title, original_title: '',
  favorite: true, watch_state: rating === null ? 'want_to_watch' : 'watched', personal_rating: rating,
  updated_at: '2026-10-08T00:00:00Z', ...metadata});
const fixtures = [
  sample(900001, '核对影片A', 9, {genres:['剧情','悬疑'], director:'核对导演甲', release_date:'1994-09-10', production_countries:['US']}),
  sample(900002, '核对影片B', 8, {genres:['剧情'], director:'核对导演甲', release_date:'1997-04-01', production_countries:['US']}),
  sample(900003, '核对影片C', 2, {genres:['剧情','动作'], director:'核对导演乙', release_date:'2011-01-01', production_countries:['GB']}),
  sample(900004, '核对影片D', 6, {genres:['奇幻'], director:'核对导演丙', release_date:'2003-01-01', production_countries:['JP']}),
  sample(900005, '核对缺资料影片', 4),
  sample(900006, '核对未评分影片', null, {genres:['喜剧']}),
];
function Preview() {
  const [items, setItems] = React.useState(fixtures);
  const [selected, setSelected] = React.useState(null);
  // Optional initial expansion for responsive screenshots; no user storage is read.
  React.useEffect(() => {
    if (new URLSearchParams(window.location.search).has('expanded')) {
      document.querySelector('.cinema-rating-profile').open = true;
      document.querySelector('.cinema-profile-group').open = true;
    }
  }, []);
  return <ThemeProvider theme={theme}><CssBaseline /><Box sx={{maxWidth:1120,mx:'auto',px:{xs:2,sm:3},py:3}}>
    <Typography component="h1" variant="h5">评分画像 · 隔离核对</Typography>
    <Typography variant="body2" color="text.secondary" sx={{my:2}}>全部为虚构测试样本，评分只在内存中更新，不写用户片单、不联网。</Typography>
    <Button onClick={() => {setItems(fixtures); setSelected(null);}}>重置核对样本</Button>
    {selected && <Typography role="status" sx={{my:2}}>来源追溯：{selected.title} · {selected.personal_rating} / 10</Typography>}
    <MovieLibrary items={items} warning={null} onViewMovie={setSelected}
      onToggle={(movie, action) => setItems(values => updateMovieLibrary(values, movie, action))}
      onRatingChange={(movie, rating) => setItems(values => setMovieRating(values, movie, rating))}
      onRemove={id => setItems(values => values.filter(movie => movie.tmdb_id !== id))}
      onClear={() => setItems([])} onRecommend={() => {}} />
  </Box></ThemeProvider>;
}
createRoot(document.getElementById('root')).render(<Preview />);
`);
fs.writeFileSync(path.join(output, 'index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>评分画像隔离核对</title><link rel="stylesheet" href="styles.css"></head><body><div id="root"></div><script defer src="preview.js"></script></body></html>\n');
process.env.BABEL_ENV = 'production';
webpack({ mode: 'production', entry: path.join(output, 'preview-entry.js'), output: {path:output,filename:'preview.js'},
  resolve: {extensions:['.tsx','.ts','.js'],modules:[path.join(root,'frontend/node_modules'),'node_modules']},
  module: {rules:[{test:/\.[jt]sx?$/,exclude:/node_modules/,use:{loader:frontendRequire.resolve('babel-loader'),
    options:{presets:[frontendRequire.resolve('babel-preset-react-app')],babelrc:false,configFile:false}}}]},
}, (error, stats) => {
  if (error || stats.hasErrors()) { console.error(error || stats.toString({all:false,errors:true})); process.exitCode = 1; }
  else console.log('Rating profile preview built (six synthetic, memory-only fixtures): ' + path.relative(root, output));
});
