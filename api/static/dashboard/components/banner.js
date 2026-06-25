/**
 * banner.js — Safety-state banner component.
 *
 * @param {{ level: 'clear'|'attention'|'crisis', attentionCount: number, hasCrisis: boolean }} state
 * @returns {HTMLElement}
 */

import { el } from '../core/dom.js';

const VARIANTS = {
  clear: {
    mod: 'banner--clear',
    icon: '✅',
    title: 'All clear',
    desc: 'No safety concerns detected. Everything looks good.',
  },
  attention: {
    mod: 'banner--attention',
    icon: '⚠️',
    title: 'Needs your attention',
    desc: null, // built dynamically from count
  },
  crisis: {
    mod: 'banner--crisis',
    icon: '🚨',
    title: 'Immediate action required',
    desc: 'A potential crisis situation has been flagged. Please review now.',
  },
};

export function renderBanner(state) {
  const level = (state && state.level) || 'clear';
  const variant = VARIANTS[level] || VARIANTS.clear;

  const attentionCount =
    level === 'attention' && state.attentionCount != null
      ? state.attentionCount
      : null;

  const descText =
    level === 'attention' && attentionCount != null
      ? attentionCount === 1
        ? '1 safety flag needs your review.'
        : attentionCount + ' safety flags need your review.'
      : variant.desc;

  const attrs = { class: 'banner ' + variant.mod };
  if (level === 'crisis') {
    attrs.role = 'alert';
    attrs['aria-live'] = 'assertive';
  }

  return el('div', attrs, [
    el('span', { class: 'banner__icon', 'aria-hidden': 'true' }, [variant.icon]),
    el('div', { class: 'banner__body' }, [
      el('div', { class: 'banner__title' }, [variant.title]),
      el('div', { class: 'banner__desc' }, [descText || '']),
    ]),
  ]);
}
