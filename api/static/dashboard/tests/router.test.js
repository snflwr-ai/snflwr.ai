import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseRoute } from '../core/router.js';

test('empty hash => overview', () => {
  assert.deepEqual(parseRoute(''), { view: 'overview', params: {} });
  assert.deepEqual(parseRoute('#/'), { view: 'overview', params: {} });
});
test('view with query params', () => {
  assert.deepEqual(parseRoute('#/safety?child=p1'), { view: 'safety', params: { child: 'p1' } });
});
