import React, { useEffect, useRef } from 'react';

// Content is visible by default, including without IntersectionObserver. Reveal
// is a one-shot enhancement, never an opacity gate in front of keyboard controls.
export const MotionReveal = ({ children, index = 0 }: { children: React.ReactNode; index?: number }) => {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === 'undefined') return;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const finish = () => node.classList.remove('is-entering');
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      node.dataset.motionRevealed = 'true';
      if (!media.matches && !document.hidden) node.classList.add('is-entering');
      observer.disconnect();
    }, { threshold: 0.08 });
    observer.observe(node);
    node.addEventListener('animationend', finish);
    media.addEventListener('change', finish);
    return () => {
      observer.disconnect();
      node.removeEventListener('animationend', finish);
      media.removeEventListener('change', finish);
      finish();
    };
  }, []);
  return <div ref={ref} className="cinema-reveal" style={{ '--reveal-delay': `${index % 3 * 65}ms` } as React.CSSProperties}>{children}</div>;
};
