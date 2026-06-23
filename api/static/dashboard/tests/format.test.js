import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatDuration, aggregateActivity } from '../core/format.js';

test('formatDuration', () => {
  assert.equal(formatDuration(0), '0m');
  assert.equal(formatDuration(90), '1h 30m');
  assert.equal(formatDuration(60), '1h 0m');
});
test('aggregateActivity sums fields', () => {
  const got = aggregateActivity([
    { questions_asked: 3, duration_minutes: 10 },
    { questions_asked: 2, duration_minutes: 5 },
  ]);
  assert.deepEqual(got, { totalSessions: 2, totalQuestions: 5, totalMinutes: 15 });
});
