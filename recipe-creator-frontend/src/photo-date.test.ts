import { render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PhotoDate from './PhotoDate.svelte';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-10T12:00:00Z'));
});
afterEach(() => vi.useRealTimers());

it('shows a two-month relative age with original and absolute dates', () => {
  render(PhotoDate,{createdAt:'2026-07-12T12:00:00Z'});
  const time = screen.getByText('2 months ago');
  expect(time).toHaveAttribute('datetime','2026-07-12T12:00:00Z');
  expect(time.getAttribute('title')).toMatch(/^Uploaded .+2026/);
  expect(time).toHaveAccessibleName(/^Uploaded .+2026/);
});

it.each([
  ['2026-09-10T11:59:00.001Z','just now'],
  ['2026-09-10T11:59:00.000Z','1 minute ago'],
  ['2026-09-10T12:05:00.000Z','just now'],
])('formats the minute boundary and clock skew for %s', (createdAt, label) => {
  render(PhotoDate,{createdAt});
  expect(screen.getByText(label)).toBeInTheDocument();
});

it.each([undefined, null, '', 'not-a-date'])('does not fabricate an age for %s', createdAt => {
  const view = render(PhotoDate,{createdAt});
  expect(screen.getByText('Upload date unavailable')).toBeInTheDocument();
  expect(view.container.querySelector('time')).toBeNull();
  expect(view.container).not.toHaveTextContent('Invalid Date');
});
