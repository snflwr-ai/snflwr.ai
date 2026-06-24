/**
 * card.js — Generic card and stat-card components.
 *
 * Exports:
 *   statCard({ label, value, hint? }): HTMLElement
 *   card({ title, body }): HTMLElement
 */

import { el } from '../core/dom.js';

/**
 * Stat card — numeric highlight with label and optional hint text.
 *
 * When `href` is given, the card renders as an accessible <a> (keyboard- and
 * screen-reader-navigable) pointing at the hash route, so the whole card is a
 * link to its detail tab.
 *
 * @param {{ label: string, value: string|number, hint?: string, href?: string }} opts
 * @returns {HTMLElement}
 */
export function statCard({ label, value, hint, href }) {
  const children = [
    el('div', { class: 'stat-value' }, [String(value)]),
    el('div', { class: 'stat-label' }, [String(label)]),
  ];

  if (hint != null) {
    children.push(el('div', { class: 'stat-hint' }, [String(hint)]));
  }

  if (href) {
    return el('a', { class: 'stat-card stat-card-link', href }, children);
  }
  return el('div', { class: 'stat-card' }, children);
}

/**
 * Generic card — titled container accepting an arbitrary body element.
 *
 * @param {{ title: string, body: HTMLElement|string }} opts
 * @returns {HTMLElement}
 */
export function card({ title, body }) {
  const headerEl = el('div', { class: 'card-header' }, [
    el('h3', {}, [String(title)]),
  ]);

  const bodyChildren =
    typeof body === 'string'
      ? [document.createTextNode(body)]
      : [body];

  return el('div', { class: 'card' }, [headerEl, ...bodyChildren]);
}
