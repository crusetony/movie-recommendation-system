export type Spring = { position: number; velocity: number };

// Exact critically damped solution: the same elapsed time gives the same position
// at different refresh rates. Clamp long pauses; visibility changes reset instead.
export function stepSpring(state: Spring, target: number, seconds: number, frequency = 18): Spring {
  const dt = Math.max(0, Math.min(seconds, 0.064));
  const offset = state.position - target;
  const momentum = state.velocity + frequency * offset;
  const decay = Math.exp(-frequency * dt);
  return {
    position: target + (offset + momentum * dt) * decay,
    velocity: (state.velocity - frequency * momentum * dt) * decay,
  };
}

export const springSettled = (state: Spring, target: number) =>
  Math.abs(state.position - target) < 0.001 && Math.abs(state.velocity) < 0.01;
