/**
 * skeleton.js — Shimmer placeholder elements for loading states.
 *
 * @param {'text'|'card'|'block'} kind
 * @returns {HTMLElement}
 */

import { el } from '../core/dom.js';

const KIND_CLASS = {
  text: 'skeleton skeleton-text',
  card: 'skeleton skeleton-card',
  block: 'skeleton',
};

export function skeleton(kind) {
  const cls = KIND_CLASS[kind] || KIND_CLASS.block;
  return el('div', { class: cls, 'aria-hidden': 'true' }, []);
}
