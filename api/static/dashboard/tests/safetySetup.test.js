import { test } from 'node:test';
import assert from 'node:assert/strict';
import { maskEmail } from '../components/safetySetup.js';

test('maskEmail keeps the first two chars and the domain', () => {
  assert.equal(maskEmail('parent@example.com'), 'pa••••@example.com');
});

test('maskEmail handles a one-char local part', () => {
  assert.equal(maskEmail('a@b.com'), 'a•@b.com');
});

test('maskEmail returns input unchanged when there is no @', () => {
  assert.equal(maskEmail('notanemail'), 'notanemail');
  assert.equal(maskEmail(''), '');
});
