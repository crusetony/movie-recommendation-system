import { useRef, useState } from 'react';
import { MovieLibraryAction, MovieLibraryItem, MovieSummary, SessionFeedback } from '../types/index.ts';
import { loadMovieLibrary, saveMovieLibrary, setMovieRating, updateMovieLibrary } from './movieLibraryStorage.ts';

export const useMovieLibrary = (feedback: SessionFeedback[]) => {
  const [initial] = useState(() => loadMovieLibrary(feedback));
  const [items, setItems] = useState(initial.items);
  const [warning, setWarning] = useState(initial.warning);
  const current = useRef(items);
  const commit = (next: MovieLibraryItem[], reset = false) => {
    current.current = next;
    setItems(next);
    setWarning(saveMovieLibrary(next, reset));
    return next;
  };
  const toggle = (movie: MovieSummary, action: MovieLibraryAction) => {
    try { return commit(updateMovieLibrary(current.current, movie, action)); }
    catch (error) { setWarning(error instanceof Error ? error.message : '片单修改失败'); return current.current; }
  };
  const remove = (movieId: number) => commit(current.current.filter(item => item.tmdb_id !== movieId));
  const clear = () => commit([], true);
  const rate = (movie: MovieSummary, rating: number | null) => {
    try { return commit(setMovieRating(current.current, movie, rating)); }
    catch (error) { setWarning(error instanceof Error ? error.message : '评分修改失败'); return current.current; }
  };
  return { items, warning, toggle, remove, clear, rate };
};
