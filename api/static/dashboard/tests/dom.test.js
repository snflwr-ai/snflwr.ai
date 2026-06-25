import { test } from 'node:test';
import assert from 'node:assert/strict';
import { escHtml, escAttr } from '../core/dom.js';

test('escHtml escapes angle brackets and ampersands', () => {
  assert.equal(escHtml('<script>&"'), '&lt;script&gt;&amp;"');
});

test('escAttr escapes quotes for attribute context', () => {
  assert.equal(escAttr('"x" & <y>'), '&quot;x&quot; &amp; &lt;y&gt;');
});

test('escHtml coerces non-strings', () => {
  assert.equal(escHtml(42), '42');
  assert.equal(escHtml(null), 'null');
});
