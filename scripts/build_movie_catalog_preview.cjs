// Real saved catalog metadata; user actions are isolated to React memory.
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '..');
const requireFrontend = createRequire(path.join(root, 'frontend/package.json'));
const output = path.join(root, 'experiments/validation/movie-catalog-2026-10-08/preview');
const source = JSON.parse(fs.readFileSync(path.join(output, '../title-year.json'), 'utf8'));
const movie = source.movies.find(value => value.tmdb_id === 103);
if (!movie) throw new Error('Missing saved catalog fixture');
fs.mkdirSync(output, {recursive:true});
fs.copyFileSync(path.join(root,'frontend/src/index.css'),path.join(output,'styles.css'));
fs.writeFileSync(path.join(output,'entry.js'), `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {ThemeProvider,CssBaseline,Box,Typography} from '@mui/material';
import theme from ${JSON.stringify(path.join(root,'frontend/src/theme.ts'))};
import {MovieCatalog} from ${JSON.stringify(path.join(root,'frontend/src/components/MovieCatalog.tsx'))};
import {apiService} from ${JSON.stringify(path.join(root,'frontend/src/services/api.ts'))};
import {setMovieRating,updateMovieLibrary} from ${JSON.stringify(path.join(root,'frontend/src/services/movieLibraryStorage.ts'))};
const movie=${JSON.stringify(movie)};
apiService.getMovieGenres=async()=>[{id:18,name:'剧情'},{id:80,name:'犯罪'}];
apiService.browseMovies=async request=>({mode:request.mode,movies:[movie],page:request.page,total_pages:1,available_pages:1,total_results:1,has_next:false,pagination_limited:false});
function Preview(){
  const [items,setItems]=React.useState([]);
  const [selected,setSelected]=React.useState(null);
  return <ThemeProvider theme={theme}><CssBaseline/><Box sx={{maxWidth:1120,mx:'auto',px:{xs:2,sm:3},py:3}}>
    <Typography component="h1" variant="h5">电影搜索 · 隔离交互核对</Typography>
    <Typography variant="body2" sx={{my:2}}>已存真实电影资料，测试评分只在内存中更新，不写用户片单、不调用API；海报可来自TMDB图片CDN。</Typography>
    <Typography role="status">核对片单：{items.length ? items.map(value=>value.title+' / '+value.watch_state+' / '+(value.personal_rating??'未评分')+' / 收藏'+value.favorite).join('；'):'空'}</Typography>
    {selected && <Typography>详情入口：{selected.title}</Typography>}
    <MovieCatalog library={items} onViewMovie={setSelected} onToggle={(value,action)=>setItems(list=>updateMovieLibrary(list,value,action))}
      onRatingChange={(value,rating)=>setItems(list=>setMovieRating(list,value,rating))}/>
  </Box></ThemeProvider>;
}
createRoot(document.getElementById('root')).render(<Preview/>);
`);
fs.writeFileSync(path.join(output,'index.html'),'<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>电影搜索隔离核对</title><link rel="stylesheet" href="styles.css"></head><body><div id="root"></div><script defer src="preview.js"></script></body></html>');
process.env.BABEL_ENV='production';
requireFrontend('webpack')({mode:'production',entry:path.join(output,'entry.js'),output:{path:output,filename:'preview.js'},
  plugins:[new (requireFrontend('webpack').DefinePlugin)({'process.env.REACT_APP_API_URL':JSON.stringify('')})],
  resolve:{extensions:['.tsx','.ts','.js'],modules:[path.join(root,'frontend/node_modules'),'node_modules']},
  module:{rules:[{test:/\.[jt]sx?$/,exclude:/node_modules/,use:{loader:requireFrontend.resolve('babel-loader'),options:{presets:[requireFrontend.resolve('babel-preset-react-app')],babelrc:false,configFile:false}}}]},
},(error,stats)=>{if(error||stats.hasErrors()){console.error(error||stats.toString({all:false,errors:true}));process.exitCode=1;}else console.log('Isolated movie catalog preview built.');});
