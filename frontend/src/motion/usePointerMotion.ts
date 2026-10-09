import { useEffect, useRef } from 'react';
import { Spring, springSettled, stepSpring } from './spring.ts';

// Motion design reference: feitangyuan/motion-web, references/handfeel.md
// (CC BY-NC 4.0). Local implementation uses an exact damped spring and idle teardown.
export function usePointerMotion<T extends HTMLElement>(minWidth = 0) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const media = window.matchMedia(`(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)${minWidth ? ` and (min-width: ${minWidth}px)` : ''}`);
    let visible = true;
    let frame: number | null = null;
    let lastTime = 0;
    let target = [0, 0, 0];
    let springs: Spring[] = target.map(() => ({ position: 0, velocity: 0 }));
    const write = () => {
      const [x, y, engagement] = springs.map((spring) => spring.position);
      element.style.setProperty('--motion-x', x.toFixed(4));
      element.style.setProperty('--motion-y', y.toFixed(4));
      element.style.setProperty('--motion-light-x', `${((x + 1) * 50).toFixed(2)}%`);
      element.style.setProperty('--motion-light-y', `${((y + 1) * 50).toFixed(2)}%`);
      element.style.setProperty('--motion-engagement', engagement.toFixed(4));
    };
    const reset = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      target = [0, 0, 0];
      springs = target.map(() => ({ position: 0, velocity: 0 }));
      element.dataset.motionActive = 'false';
      write();
    };
    const allowed = () => media.matches && visible && !document.hidden;
    const tick = (time: number) => {
      if (!allowed()) { reset(); return; }
      const dt = (time - lastTime) / 1000;
      lastTime = time;
      springs = springs.map((spring, index) => stepSpring(spring, target[index], dt));
      const settled = springs.every((spring, index) => springSettled(spring, target[index]));
      if (settled) springs = target.map((position) => ({ position, velocity: 0 }));
      write();
      frame = settled ? null : requestAnimationFrame(tick);
      element.dataset.motionActive = settled ? 'false' : 'true';
    };
    const start = () => {
      if (frame !== null) return;
      lastTime = performance.now();
      element.dataset.motionActive = 'true';
      frame = requestAnimationFrame(tick);
    };
    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !allowed()) return;
      const bounds = element.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return;
      target = [
        Math.max(-1, Math.min(1, (event.clientX - bounds.left) / bounds.width * 2 - 1)),
        Math.max(-1, Math.min(1, (event.clientY - bounds.top) / bounds.height * 2 - 1)), 1,
      ];
      start();
    };
    const leave = () => {
      target = [0, 0, 0];
      if (allowed()) start(); else reset();
    };
    const suspend = () => {
      if (frame !== null || springs.some((spring) => spring.position !== 0)) reset();
    };
    const eligibilityChanged = () => {
      element.dataset.motionVisible = String(visible && !document.hidden);
      if (!allowed()) reset();
    };
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      eligibilityChanged();
    });
    observer?.observe(element);
    element.addEventListener('pointerenter', move);
    element.addEventListener('pointermove', move);
    element.addEventListener('pointerleave', leave);
    element.addEventListener('pointercancel', reset);
    // Native scrolling stays native. Re-center a hovered surface when it moves.
    window.addEventListener('scroll', suspend, { passive: true, capture: true });
    window.addEventListener('blur', suspend);
    media.addEventListener('change', eligibilityChanged);
    document.addEventListener('visibilitychange', eligibilityChanged);
    eligibilityChanged();
    return () => {
      reset();
      observer?.disconnect();
      element.removeEventListener('pointerenter', move);
      element.removeEventListener('pointermove', move);
      element.removeEventListener('pointerleave', leave);
      element.removeEventListener('pointercancel', reset);
      window.removeEventListener('scroll', suspend, true);
      window.removeEventListener('blur', suspend);
      media.removeEventListener('change', eligibilityChanged);
      document.removeEventListener('visibilitychange', eligibilityChanged);
    };
  }, [minWidth]);
  return ref;
}
