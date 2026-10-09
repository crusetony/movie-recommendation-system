import React, { useState } from 'react';
import { ArrowOutwardRounded } from '@mui/icons-material';
import { usePointerMotion } from '../motion/usePointerMotion.ts';
import { MovieSummary } from '../types/index.ts';

// Visual selections are independent of recommendation ranking and reference-image inputs.
// IDs and poster paths are verified against TMDB and existing local candidate snapshots.
const POSTERS = [
  { id: 103, title: '出租车司机', year: '1976', name: 'TAXI DRIVER', path: 'ekstpH614fwDX8DUln1a2Opz0N8.jpg' },
  { id: 18329, title: '春光乍泄', year: '1997', name: 'HAPPY TOGETHER', path: 't1NLR7b0xwiifdTyT3Z3Hewo6r9.jpg' },
  { id: 78, title: '银翼杀手', year: '1982', name: 'BLADE RUNNER', path: 'bLxQbj7fpVoVLX01qrYXpAD4jNP.jpg' },
];
export const HeroPosters = ({ onViewMovie }: { onViewMovie?: (movie: MovieSummary) => void }) => {
  const ref = usePointerMotion<HTMLDivElement>(701);
  const [active, setActive] = useState(0);
  const [failed, setFailed] = useState<number[]>([]);
  const movie = POSTERS[active];
  const openDetails = (item: typeof POSTERS[number]) => onViewMovie?.({
    tmdb_id: item.id, title: item.title, original_title: '', release_date: item.year,
    poster_url: `https://image.tmdb.org/t/p/w500/${item.path}`,
  });
  return <section className="cinema-hero__gallery" aria-label="电影海报精选">
    <div className="cinema-poster-exhibit" ref={ref}>
      <div className="cinema-poster-exhibit__list" aria-label="电影精选，移入预览、点击查看简介">
        {POSTERS.map((item, index) => <button type="button" key={item.id} className="cinema-poster-exhibit__row"
          aria-label={onViewMovie ? `查看${item.title}电影简介` : `展示${item.title}海报`}
          aria-haspopup={onViewMovie ? 'dialog' : undefined} aria-pressed={active === index} aria-controls="cinema-poster-preview"
          onPointerEnter={event => { if (event.pointerType === 'mouse') setActive(index); }}
          onFocus={() => setActive(index)} onClick={() => { setActive(index); openDetails(item); }}>
          <span className="cinema-poster-exhibit__number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
          <span className="cinema-poster-exhibit__name"><strong>{item.name}</strong><small>{item.title} / {item.year}</small></span>
          <ArrowOutwardRounded className="cinema-poster-exhibit__arrow" aria-hidden="true" />
        </button>)}
      </div>
      <a id="cinema-poster-preview" className="cinema-poster-exhibit__preview"
        href={`https://www.themoviedb.org/movie/${movie.id}`} target="_blank" rel="noopener noreferrer"
        aria-label={`${movie.title}（${movie.year}），${onViewMovie ? '查看电影详情' : '在 TMDB 查看详情'}`}
        aria-haspopup={onViewMovie ? 'dialog' : undefined}
        onClick={event => { if (onViewMovie) { event.preventDefault(); openDetails(movie); } }}>
        <div className="cinema-poster-exhibit__image">
          {POSTERS.map((item, index) => <div key={item.id} className={`cinema-poster-exhibit__layer${index === active ? ' is-active' : ''}`} aria-hidden={index !== active}>
            {failed.includes(item.id) ? <span className="cinema-hero-poster__fallback">{item.name}<small>海报暂时无法加载</small></span>
              : <img src={`https://image.tmdb.org/t/p/w500/${item.path}`} alt={index === active ? `${item.title}海报` : ''} width={500} height={750}
                decoding="async" draggable={false} onError={() => setFailed(previous => previous.includes(item.id) ? previous : [...previous, item.id])} />}
          </div>)}
        </div>
        <span className="cinema-poster-exhibit__detail">{movie.title} <ArrowOutwardRounded fontSize="inherit" /></span>
      </a>
    </div>
    <p className="cinema-hero__gallery-note">POSTER SELECTION <span>移入预览 · 点击片名或海报查看简介</span></p>
  </section>;
};
