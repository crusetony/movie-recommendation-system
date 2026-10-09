import { RefObject, useEffect } from 'react';

export function sceneProgress(top: number, height: number, viewport: number) {
  if (![top, height, viewport].every(Number.isFinite) || height <= 0 || viewport <= 0) return 0;
  return Math.max(0, Math.min(1, -top / height));
}

// Inspired by the layer parallax of jiejoe.com/home. Native page scrolling
// controls a small hero offset; there is no pinned scene or wheel interception.
export function useScrollScene(ref: RefObject<HTMLElement>, minWidth = 701) {
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const media = window.matchMedia(`(prefers-reduced-motion: no-preference) and (min-width: ${minWidth}px)`);
    let frame: number | null = null;
    let visible = true;
    let current = 0;
    let target = 0;
    let lastTime = 0;
    const write = () => element.style.setProperty('--scene-progress', current.toFixed(5));
    const stop = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      element.dataset.sceneActive = 'false';
    };
    const tick = (time: number) => {
      const dt = Math.max(0, Math.min((time - lastTime) / 1000, .064));
      lastTime = time;
      current += (target - current) * (1 - Math.exp(-16 * dt));
      const settled = Math.abs(target - current) < .0001;
      if (settled) current = target;
      write();
      if (settled) stop(); else frame = requestAnimationFrame(tick);
    };
    const measure = (snap = false) => {
      element.dataset.sceneVisible = String(visible && !document.hidden);
      const bounds = element.getBoundingClientRect();
      target = media.matches ? sceneProgress(bounds.top, bounds.height, window.innerHeight) : 0;
      if (snap || !media.matches || !visible || document.hidden) {
        stop(); current = target; write(); return;
      }
      if (frame === null && Math.abs(target - current) >= .0001) {
        lastTime = performance.now(); element.dataset.sceneActive = 'true'; frame = requestAnimationFrame(tick);
      }
    };
    const onScroll = () => measure();
    const onResize = () => measure(true);
    const onEligibility = () => measure(true);
    const intersection = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      measure(true);
    }, { rootMargin: '80px 0px' });
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(onResize);
    intersection?.observe(element);
    resize?.observe(element);
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onResize);
    document.addEventListener('visibilitychange', onEligibility);
    media.addEventListener('change', onEligibility);
    measure(true);
    return () => {
      stop(); intersection?.disconnect(); resize?.disconnect();
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('visibilitychange', onEligibility);
      media.removeEventListener('change', onEligibility);
    };
  }, [ref, minWidth]);
}
