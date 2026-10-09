import { Spring, springSettled, stepSpring } from './spring.ts';

const simulate = (hz: number, target: number, seconds: number, initial: Spring = { position: 0, velocity: 0 }) => {
  let spring = initial;
  for (let frame = 0; frame < hz * seconds; frame++) spring = stepSpring(spring, target, 1 / hz);
  return spring;
};

test('30/60/120 Hz converge to the same position and velocity for equal elapsed time', () => {
  const slow = simulate(30, 1, .5);
  for (const hz of [60, 120]) {
    const fast = simulate(hz, 1, .5);
    expect(fast.position).toBeCloseTo(slow.position, 10);
    expect(fast.velocity).toBeCloseTo(slow.velocity, 10);
  }
  expect(springSettled(simulate(60, 1, 1), 1)).toBe(true);
});

test('reversing direction preserves velocity and returns to rest without oscillating forever', () => {
  const moving = simulate(60, 1, .1);
  const next = stepSpring(moving, -1, 1 / 120);
  expect(Math.abs(next.velocity)).toBeGreaterThan(0);
  const reversed = simulate(60, -1, 1, next);
  expect(springSettled(reversed, -1)).toBe(true);
  expect(springSettled(simulate(60, 0, 1, reversed), 0)).toBe(true);
});

test('a long frame is bounded; zero or negative time does not advance the spring', () => {
  const start = { position: -.5, velocity: 2 };
  expect(stepSpring(start, 1, 30)).toEqual(stepSpring(start, 1, .064));
  expect(stepSpring(start, 1, 0)).toEqual(start);
  expect(stepSpring(start, 1, -1)).toEqual(start);
  expect(Number.isFinite(stepSpring(start, 1, 30).velocity)).toBe(true);
});
