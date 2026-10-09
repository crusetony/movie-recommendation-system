import { useEffect, useState } from 'react';

export const STARTUP_MIN_MS = 1200;
export const STARTUP_MAX_MS = 3500;
export const STARTUP_FADE_MS = 320;
type StartupStage = 'loading' | 'revealing' | 'ready';

// The HTML screen is visible even before the JavaScript bundle arrives.
// React takes over its lifetime, keeping the actual application mounted below it.
export const useStartupScreen = (): StartupStage => {
  const [stage, setStage] = useState<StartupStage>(() => document.getElementById('cinema-startup') ? 'loading' : 'ready');
  useEffect(() => {
    const screen = document.getElementById('cinema-startup');
    if (!screen) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let active = true;
    let startedReveal = false;
    let minimumPassed = reduced;
    let assetsReady = false;
    let fadeTimer: number | undefined;
    const poster = document.querySelector<HTMLImageElement>('.cinema-poster-exhibit__layer.is-active img');
    let releasePoster: () => void;
    const posterReady = new Promise<void>(resolve => { releasePoster = resolve; });
    const onPosterReady = () => releasePoster();
    if (!poster || poster.complete) onPosterReady();
    else {
      poster.addEventListener('load', onPosterReady, { once: true });
      poster.addEventListener('error', onPosterReady, { once: true });
    }
    const reveal = () => {
      if (!active || startedReveal) return;
      startedReveal = true;
      screen.dataset.state = 'leaving';
      setStage('revealing');
      const finish = () => {
        if (!active) return;
        screen.remove();
        setStage('ready');
      };
      if (reduced) finish();
      else fadeTimer = window.setTimeout(finish, STARTUP_FADE_MS);
    };
    const maybeReveal = () => { if (minimumPassed && assetsReady) reveal(); };
    const minimumTimer = window.setTimeout(() => { minimumPassed = true; maybeReveal(); }, reduced ? 0 : STARTUP_MIN_MS);
    const maximumTimer = window.setTimeout(reveal, reduced ? 1500 : STARTUP_MAX_MS);
    void Promise.allSettled([posterReady, document.fonts?.ready ?? Promise.resolve()]).then(() => {
      assetsReady = true;
      maybeReveal();
    });
    return () => {
      active = false;
      window.clearTimeout(minimumTimer);
      window.clearTimeout(maximumTimer);
      window.clearTimeout(fadeTimer);
      poster?.removeEventListener('load', onPosterReady);
      poster?.removeEventListener('error', onPosterReady);
      releasePoster();
    };
  }, []);
  return stage;
};
