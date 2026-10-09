import React, { useRef } from 'react';
import { ArrowOutwardRounded } from '@mui/icons-material';
import { useScrollScene } from '../motion/useScrollScene.ts';
import { HeroPosters } from './HeroPosters.tsx';
import { MovieSummary } from '../types/index.ts';

export const CinematicHero = ({ onStart, onViewMovie }: { onStart: () => void; onViewMovie?: (movie: MovieSummary) => void }) => {
  const ref = useRef<HTMLElement>(null);
  useScrollScene(ref);
  return <header ref={ref} className="cinema-hero" id="top">
    <div className="cinema-hero__glow cinema-hero__glow--one" aria-hidden="true" />
    <div className="cinema-hero__glow cinema-hero__glow--two" aria-hidden="true" />
    <div className="cinema-hero__lines" aria-hidden="true" />
    <div className="cinema-hero__stage">
      <div className="cinema-hero__copy">
        <div className="cinema-hero__edition cinema-kicker">FOR THE WAY YOU FEEL / 为此刻而选</div>
        <div className="cinema-hero__word" aria-hidden="true"><span>C</span><span>I</span><span>N</span><span>E</span><span>M</span><span>A</span></div>
        <div className="cinema-hero__message">
          <h1 className="cinema-hero__title"><span>从此刻的心情，</span><span>找到下一部<span className="cinema-hero__accent">电影。</span></span></h1>
          <p className="cinema-hero__description">有时想去远方，有时只想被一个故事接住。<br />写下偏好，或带来一张海报，让银幕里的世界与你相遇。</p>
        </div>
        <button type="button" className="cinema-start" onClick={onStart} aria-controls="movie-query"><span className="cinema-roll-label"><span>开始选片</span><span aria-hidden="true">开始选片</span></span><span className="cinema-start__arrow"><ArrowOutwardRounded /></span></button>
      </div>
      <HeroPosters onViewMovie={onViewMovie} />
    </div>
    <div className="cinema-hero__features" aria-label="推荐方式">
      <p><span>01 / 文字与影评</span><small>从心情与偏好，寻找故事</small></p>
      <p><span>02 / 图片与海报</span><small>让光线与色彩，表达审美</small></p>
      <p><span>03 / 个人评分</span><small>用星级记录偏好，继续探索</small></p>
    </div>
  </header>;
};
