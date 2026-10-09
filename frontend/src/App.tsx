import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Container,
  CssBaseline,
  ThemeProvider,
} from '@mui/material';
import { CinemaNavigation } from './components/CinemaNavigation.tsx';
import { AppHelp } from './components/AppHelp.tsx';
import { CinematicHero } from './components/CinematicHero.tsx';
import { MovieRecommendation } from './components/MovieRecommendation.tsx';
import { MovieDetailsDialog } from './components/MovieDetailsDialog.tsx';
import { MovieLibrary } from './components/MovieLibrary.tsx';
import { MovieCatalog } from './components/MovieCatalog.tsx';
import { RecommendationHistory } from './components/RecommendationHistory.tsx';
import { ExperimentEvaluation } from './components/ExperimentEvaluation.tsx';
import { SystemStatus } from './components/SystemStatus.tsx';
import { apiService } from './services/api.ts';
import { MovieSummary, RecommendationHistoryEntry, SystemStatus as SystemStatusType } from './types/index.ts';
import { loadFeedback } from './services/feedbackStorage.ts';
import { useMovieLibrary } from './services/useMovieLibrary.ts';
import { useRecommendationHistory } from './services/useRecommendationHistory.ts';
import theme from './theme.ts';
import { useStartupScreen } from './services/useStartupScreen.ts';


const pageHashes = ['workbench', 'experiment', 'library', 'catalog', 'history'];
const readRoute = () => {
  const hash = window.location.hash.slice(1);
  return { home: !pageHashes.includes(hash), page: Math.max(0, pageHashes.indexOf(hash)) };
};

function App() {
  const startup = useStartupScreen();
  const [page, setPage] = useState(() => readRoute().page);
  const [home, setHome] = useState(() => readRoute().home);
  const [systemStatus, setSystemStatus] = useState<SystemStatusType | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [jumpTarget, setJumpTarget] = useState<'query' | 'workbench' | 'top' | null>(null);
  const [experimentOpened, setExperimentOpened] = useState(() => readRoute().page === 1);
  const [libraryOpened, setLibraryOpened] = useState(() => readRoute().page === 2);
  const [catalogOpened, setCatalogOpened] = useState(() => readRoute().page === 3);
  const [historyOpened, setHistoryOpened] = useState(() => readRoute().page === 4);
  const [legacyFeedback] = useState(() => loadFeedback());
  const library = useMovieLibrary(legacyFeedback);
  const history = useRecommendationHistory();
  const [historyRequest,setHistoryRequest] = useState<RecommendationHistoryEntry|null>(null);
  const historyHandled=useCallback(()=>setHistoryRequest(null),[]);
  const [detailMovie, setDetailMovie] = useState<MovieSummary | null>(null);
  const [referenceRequest, setReferenceRequest] = useState<MovieSummary | null>(null);
  const [recommendationBusy, setRecommendationBusy] = useState(false);
  const referenceHandled = useCallback(() => setReferenceRequest(null), []);

  const toggleLibrary = library.toggle;

  const changePage = (value: number) => {
    if (value === 1) setExperimentOpened(true);
    if (value === 2) setLibraryOpened(true);
    if (value === 3) setCatalogOpened(true);
    if (value === 4) setHistoryOpened(true);
    setPage(value);
    setHome(false);
    const hash = `#${pageHashes[value]}`;
    if (window.location.hash !== hash) window.history.pushState(null, '', hash);
    setJumpTarget(value === 0 ? 'query' : 'workbench');
  };
  const openRecommendation = () => { changePage(0); setJumpTarget('query'); };
  const openHome = () => {
    setHome(true);
    if (window.location.hash !== '#top') window.history.pushState(null, '', '#top');
    setJumpTarget('top');
  };

  useEffect(() => {
    const syncRoute = () => {
      const route = readRoute();
      if (route.page === 1) setExperimentOpened(true);
      if (route.page === 2) setLibraryOpened(true);
      if (route.page === 3) setCatalogOpened(true);
      if (route.page === 4) setHistoryOpened(true);
      setHome(route.home);
      setPage(route.page);
      setJumpTarget(route.home ? 'top' : 'workbench');
    };
    window.addEventListener('hashchange', syncRoute);
    window.addEventListener('popstate', syncRoute);
    return () => {
      window.removeEventListener('hashchange', syncRoute);
      window.removeEventListener('popstate', syncRoute);
    };
  }, []);
  const reuseHistory=(entry:RecommendationHistoryEntry)=>{if(recommendationBusy)return;setHistoryRequest(entry);openRecommendation();};
  const useReference = (movie: MovieSummary) => {
    if (recommendationBusy) return;
    setReferenceRequest(movie);
    setDetailMovie(null);
    openRecommendation();
  };

  useEffect(() => {
    if (!jumpTarget) return;
    const panelId = page === 1 ? 'experiment-panel' : page === 3 ? 'catalog-panel' : page === 2 ? 'library-panel' : page === 4 ? 'history-panel' : 'recommend-panel';
    const target = document.getElementById(jumpTarget === 'top' ? 'top' : jumpTarget === 'query' ? 'movie-query' : panelId);
    target?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
    target?.focus({ preventScroll: true });
    setJumpTarget(null);
  }, [page, home, jumpTarget]);

  const checkSystemStatus = useCallback(async () => {
    try {
      const status = await apiService.getSystemStatus();
      setSystemStatus(status);
      setStatusError(null);
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : '无法获取系统状态');
    }
  }, []);

  useEffect(() => {
    void checkSystemStatus();
  }, [checkSystemStatus]);

  const systemReady = systemStatus?.status === 'ready' && !statusError;

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Container component="main" maxWidth={false} disableGutters className="cinema-shell">
        <Box className={startup === 'loading' ? 'cinema-entry-pending' : undefined}
          aria-hidden={startup !== 'ready' ? true : undefined}
          ref={(element: HTMLElement | null) => element?.toggleAttribute('inert', startup !== 'ready')}>
          <CinemaNavigation page={page} home={home} onHome={openHome} onPage={changePage}>
            {systemStatus && <SystemStatus status={systemStatus} error={statusError} onRetry={() => void checkSystemStatus()} />}
            <AppHelp />
          </CinemaNavigation>
          {home && <CinematicHero onStart={openRecommendation} onViewMovie={setDetailMovie} />}
          <Box component="section" id="workbench" className="cinema-workspace" sx={{ display: home ? 'none' : 'block' }}>
          {statusError && <Alert severity="error" className="cinema-status-error" sx={{ mb: 2 }}>{statusError}</Alert>}
          {systemStatus && !systemReady && !statusError && <Alert severity="warning" className="cinema-status-error" sx={{ mb: 2 }}>{systemStatus.message}</Alert>}
          {systemReady && systemStatus?.parser_mode !== 'llm+rules' && <Alert severity="warning" className="cinema-status-error" sx={{ mb: 2 }}>当前使用有限的本地规则解析，可打开系统状态查看。</Alert>}
          {systemReady && systemStatus?.visual_fallback_reason && <Alert severity="warning" className="cinema-status-error" sx={{ mb: 2 }}>视觉模型已降级：{systemStatus.visual_fallback_reason}</Alert>}
          <Box id="recommend-panel" role="tabpanel" tabIndex={-1} aria-labelledby="recommend-tab" className="cinema-page" data-active={page === 0} sx={{ display: page === 0 ? 'block' : 'none' }}><MovieRecommendation
            systemReady={Boolean(systemReady)}
            onRunCompleted={() => void checkSystemStatus()}
            library={library.items} onLibraryToggle={toggleLibrary}
            onRatingChange={library.rate}
            onViewMovie={setDetailMovie} referenceRequest={referenceRequest} onReferenceHandled={referenceHandled}
            onBusyChange={setRecommendationBusy}
            onSaveHistory={history.record} historyRequest={historyRequest} onHistoryHandled={historyHandled}
          /></Box>
          {experimentOpened && <Box id="experiment-panel" role="region" aria-label="实验与消融" tabIndex={-1} className="cinema-page" data-active={page === 1} sx={{ display: page === 1 ? 'block' : 'none' }}><ExperimentEvaluation /></Box>}
          {libraryOpened && <Box id="library-panel" role="tabpanel" tabIndex={-1} aria-labelledby="library-tab" className="cinema-page" data-active={page === 2} sx={{ display: page === 2 ? 'block' : 'none' }}>
            <MovieLibrary items={library.items} warning={library.warning} onViewMovie={setDetailMovie} onToggle={toggleLibrary} onRatingChange={library.rate}
              onRemove={library.remove} onClear={library.clear} onRecommend={openRecommendation} />
          </Box>}
          {catalogOpened && <Box id="catalog-panel" role="tabpanel" tabIndex={-1} aria-labelledby="catalog-tab" className="cinema-page" data-active={page === 3} sx={{ display: page === 3 ? 'block' : 'none' }}>
            <MovieCatalog library={library.items} onViewMovie={setDetailMovie} onToggle={toggleLibrary} onRatingChange={library.rate} />
          </Box>}
          {historyOpened && <Box id="history-panel" role="tabpanel" tabIndex={-1} aria-labelledby="history-tab" className="cinema-page" data-active={page===4} sx={{display:page===4?'block':'none'}}>
            <RecommendationHistory items={history.items} warning={history.warning} onRemove={history.remove} onClear={history.clear}
              onReuse={reuseHistory} reuseDisabled={recommendationBusy} library={library.items} onViewMovie={setDetailMovie}
              onLibraryToggle={toggleLibrary} onRatingChange={library.rate} />
          </Box>}
          {history.warning && page!==4 && <Alert severity="warning" className="cinema-page" sx={{mb:2}}>{history.warning}</Alert>}
          {library.warning && page !== 2 && <Alert severity="warning" className="cinema-page" sx={{ mb: 2 }}>{library.warning}</Alert>}
          </Box>
          <MovieDetailsDialog movie={detailMovie} onClose={() => setDetailMovie(null)} onUseReference={useReference}
            referenceDisabled={recommendationBusy} libraryItem={library.items.find(item => item.tmdb_id === detailMovie?.tmdb_id)}
            onLibraryToggle={toggleLibrary} onRatingChange={library.rate} />

        </Box>
      </Container>
    </ThemeProvider>
  );
}

export default App;
