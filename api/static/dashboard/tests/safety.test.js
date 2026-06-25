import { test } from 'node:test';
import assert from 'node:assert/strict';
import { deriveSafetyState } from '../core/safety.js';

test('empty inputs => clear', () => {
  const s = deriveSafetyState([], []);
  assert.equal(s.level, 'clear');
  assert.equal(s.attentionCount, 0);
  assert.equal(s.hasCrisis, false);
});

test('all resolved => clear (every resolution shape)', () => {
  const alerts = [{ resolved: true }, { is_resolved: true }, { resolved_at: '2026-06-01T00:00:00Z' }];
  assert.equal(deriveSafetyState(alerts, []).level, 'clear');
});

test('one unresolved alert => attention with count', () => {
  const s = deriveSafetyState([{ resolved: false }, { resolved: true }], []);
  assert.equal(s.level, 'attention');
  assert.equal(s.attentionCount, 1);
});

test('unresolved incident counts too', () => {
  const s = deriveSafetyState([], [{ resolved_at: null }]);
  assert.equal(s.level, 'attention');
  assert.equal(s.attentionCount, 1);
});

test("real 'critical' severity (unresolved) => crisis", () => {
  // Backend severities are critical|major|minor — NOT 'crisis'. The top
  // severity must drive the red banner.
  const s = deriveSafetyState([], [{ severity: 'critical', resolved: false }]);
  assert.equal(s.level, 'crisis');
  assert.equal(s.hasCrisis, true);
});

test('escalation incident_type (unresolved) => crisis', () => {
  // Real incidents flag escalation via incident_type, e.g. "escalating_requests".
  const s = deriveSafetyState([], [{ incident_type: 'escalating_requests', severity: 'major', resolved: false }]);
  assert.equal(s.hasCrisis, true);
  assert.equal(s.level, 'crisis');
});

test('RESOLVED critical does NOT pin a permanent red banner', () => {
  const s = deriveSafetyState([], [{ severity: 'critical', resolved: true }]);
  assert.equal(s.hasCrisis, false);
  assert.equal(s.level, 'clear');
});

test('unresolved major => attention (not crisis)', () => {
  const s = deriveSafetyState([], [{ severity: 'major', resolved: false }]);
  assert.equal(s.level, 'attention');
  assert.equal(s.hasCrisis, false);
});

test('crisis detected via category/type substring (case-insensitive)', () => {
  assert.equal(deriveSafetyState([{ category: 'Crisis_Escalation', resolved: false }], []).hasCrisis, true);
  assert.equal(deriveSafetyState([{ type: 'ESCALATION', resolved: false }], []).hasCrisis, true);
});
