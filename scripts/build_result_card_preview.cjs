// Offline visual verification of the production component with a saved response.
// No recommendation/API requests, storage writes or benchmark-label mutations.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');

const root = path.resolve(__dirname, '..');
const frontendRequire = createRequire(path.join(root, 'frontend/package.json'));
const webpack = frontendRequire('webpack');
const libraryPreview = process.argv.includes('--library');
const output = path.join(root, 'experiments/validation/result-cards-2026-10-08', libraryPreview ? 'library' : '');
const snapshotPath = path.join(root, 'experiments/snapshots-vector-v2/q001-af6415abbea0.json');
const snapshotBytes = fs.readFileSync(snapshotPath);
const response = JSON.parse(snapshotBytes);
const candidates = response.recommendations;
if (!response.success || !candidates?.length) throw new Error('Saved response has no valid recommendations');
// Preserve original records; include both short and long synopsis examples.
const shortest = candidates.reduce((a, b) => a.overview.length < b.overview.length ? a : b);
const longest = candidates.reduce((a, b) => a.overview.length > b.overview.length ? a : b);
const movies = [...new Map([candidates[0], shortest, longest].map(movie => [movie.tmdb_id, movie])).values()];
fs.mkdirSync(output, { recursive: true });
fs.copyFileSync(path.join(root, 'frontend/src/index.css'), path.join(output, 'styles.css'));
fs.writeFileSync(path.join(output, 'source.json'), JSON.stringify({
  snapshot: path.relative(root, snapshotPath),
  snapshot_sha256: crypto.createHash('sha256').update(snapshotBytes).digest('hex'),
  tmdb_ids: movies.map(movie => movie.tmdb_id),
  mode: 'offline_saved_response',
  recommendation_api_calls: 0,
  deepseek_calls: 0,
  tmdb_data_api_calls: 0,
  note: 'Poster images may load from the public TMDB image CDN; this is not a new recommendation run.',
}, null, 2) + '\n');
fs.writeFileSync(path.join(output, 'preview-entry.js'), `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { ThemeProvider, CssBaseline, Box, Typography } from '@mui/material';
import theme from ${JSON.stringify(path.join(root, 'frontend/src/theme.ts'))};
import { MovieResultCard } from ${JSON.stringify(path.join(root, 'frontend/src/components/MovieResultCard.tsx'))};
import { updateMovieLibrary, setMovieRating } from ${JSON.stringify(path.join(root, 'frontend/src/services/movieLibraryStorage.ts'))};
const movies = ${JSON.stringify(movies)};
function Preview() {
  const [library, setLibrary] = React.useState([]);
  return React.createElement(ThemeProvider, { theme },
    React.createElement(CssBaseline),
    React.createElement(Box, {sx: {maxWidth: 1120, mx: 'auto', px: {xs: 2, sm: 3}, py: 3}},
      React.createElement(Typography, {variant: 'h5', component: 'h1'}, '推荐结果 · 离线版式核对'),
      React.createElement(Typography, {variant: 'body2', color: 'text.secondary', sx: {mt: 1, mb: 3}},
        '使用已保存的真实候选，未重新调用推荐服务。片单与评分仅在此预览内生效，不保存。'),
      ...movies.map((movie, index) => React.createElement(MovieResultCard, {key: movie.tmdb_id, movie, index,
        libraryItem: library.find(item => item.tmdb_id === movie.tmdb_id),
        onLibraryToggle: (value, action) => setLibrary(items => updateMovieLibrary(items, value, action)),
        onRatingChange: (value, rating) => setLibrary(items => setMovieRating(items, value, rating)),
      }))));
}
createRoot(document.getElementById('root')).render(React.createElement(Preview));
`);
fs.writeFileSync(path.join(output, 'index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>推荐卡片离线核对</title><link rel="stylesheet" href="styles.css"></head><body><div id="root"></div><script defer src="preview.js"></script></body></html>\n');
process.env.BABEL_ENV = 'production';
webpack({
  mode: 'production',
  entry: path.join(output, 'preview-entry.js'),
  output: { path: output, filename: 'preview.js' },
  resolve: { extensions: ['.tsx', '.ts', '.js'], modules: [path.join(root, 'frontend/node_modules'), 'node_modules'] },
  module: { rules: [{
    test: /\.[jt]sx?$/, exclude: /node_modules/,
    use: { loader: frontendRequire.resolve('babel-loader'), options: { presets: [frontendRequire.resolve('babel-preset-react-app')], babelrc: false, configFile: false } },
  }] },
}, (error, stats) => {
  if (error || stats.hasErrors()) {
    console.error(error || stats.toString({ all: false, errors: true }));
    process.exitCode = 1;
  } else console.log(`Offline preview built: ${path.relative(root, output)} (${movies.length} real records)`);
});
