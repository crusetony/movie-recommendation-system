import React from 'react';
import { Tab, Tabs } from '@mui/material';

export const CinemaNavigation = ({ page, home, onHome, onPage, children }: {
  page: number; home: boolean; onHome: () => void; onPage: (value: number) => void;
  children?: React.ReactNode;
}) => (
  <nav className="cinema-nav" aria-label="主导航">
    <a className="cinema-nav__brand" href="#top" onClick={event => { event.preventDefault(); onHome(); }} aria-label="电影推荐系统，回到开场">
      <svg viewBox="0 0 44 44" aria-hidden="true"><circle cx="22" cy="22" r="20" /><path d="M12 10h20v24H12zM12 16h20M12 28h20M17 10v24M27 10v24" /><path d="m20 19 6 3-6 3z" /></svg>
      <span>CINEMA<span className="cinema-nav__subtitle">智能电影推荐系统</span></span>
    </a>
    <Tabs className="cinema-nav__tabs" value={home || page === 1 ? false : page} onChange={(_, value) => onPage(value)}
      variant="scrollable" scrollButtons={false} aria-label="功能页面">
      <Tab value={0} id="recommend-tab" aria-controls="recommend-panel" label="电影推荐" />
      <Tab value={3} id="catalog-tab" aria-controls="catalog-panel" label="电影搜索" />
      <Tab value={4} id="history-tab" aria-controls="history-panel" label="推荐历史" />
      <Tab value={2} id="library-tab" aria-controls="library-panel" label="我的片单" />
    </Tabs>
    <div className="cinema-nav__actions">
      <button type="button" className="cinema-nav__experiment" aria-current={!home && page === 1 ? 'page' : undefined}
        onClick={() => onPage(1)} aria-controls="experiment-panel">实验室</button>
      {children}
    </div>
  </nav>
);
