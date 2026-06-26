// components/emptyState.js
//
// A single, consistent empty-state treatment. Replaces the ad-hoc emoji icons
// (✅ / 👶) scattered across views with refined, monochrome inline SVGs that
// match the dashboard's iconography (and the safety-setup shield). Built with
// the el() helper + DOM SVG construction — no innerHTML (codebase XSS posture).

import { el } from '../core/dom.js';

const NS = 'http://www.w3.org/2000/svg';

function svg(build) {
  const node = document.createElementNS(NS, 'svg');
  node.setAttribute('viewBox', '0 0 24 24');
  node.setAttribute('fill', 'none');
  node.setAttribute('aria-hidden', 'true');
  node.setAttribute('class', 'empty-svg');
  build(node);
  return node;
}
function path(d, attrs = {}) {
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('d', d);
  p.setAttribute('stroke', 'currentColor');
  p.setAttribute('stroke-width', '1.6');
  p.setAttribute('stroke-linecap', 'round');
  p.setAttribute('stroke-linejoin', 'round');
  for (const [k, v] of Object.entries(attrs)) p.setAttribute(k, v);
  return p;
}

// Named icons (return fresh DOM nodes each call).
export const EMPTY_ICONS = {
  // shield + check — "all clear / protected"
  allClear: () => svg((s) => {
    s.appendChild(path('M12 3 5 5.5v5c0 4.2 2.9 8.1 7 9.2 4.1-1.1 7-5 7-9.2v-5L12 3Z'));
    s.appendChild(path('M9 11.6l2.1 2.1 4-4.2', { 'stroke-width': '1.9' }));
  }),
  // two people — "no children yet"
  children: () => svg((s) => {
    s.appendChild(path('M8.5 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'));
    s.appendChild(path('M3.5 19.5c0-2.8 2.2-5 5-5s5 2.2 5 5'));
    s.appendChild(path('M16 5.2a3 3 0 0 1 0 5.6M16.5 14.6c2.3.4 4 2.4 4 4.9', { 'stroke-opacity': '0.55' }));
  }),
  // bar chart — "no activity"
  activity: () => svg((s) => {
    s.appendChild(path('M4 20h16', { 'stroke-opacity': '0.4' }));
    s.appendChild(path('M7 20v-5M12 20V9M17 20v-8'));
  }),
};

/**
 * Build a consistent empty-state block.
 * @param {object} opts
 * @param {string} opts.icon  key in EMPTY_ICONS (default 'allClear')
 * @param {string} opts.text  the message
 */
export function emptyState({ icon = 'allClear', text = '' } = {}) {
  const node = el('div', { class: 'empty-state' });
  const iconWrap = el('div', { class: 'empty-icon' });
  iconWrap.appendChild((EMPTY_ICONS[icon] || EMPTY_ICONS.allClear)());
  node.appendChild(iconWrap);
  if (text) node.appendChild(el('p', { text }));
  return node;
}
