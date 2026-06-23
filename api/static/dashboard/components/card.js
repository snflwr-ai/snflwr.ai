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
 * @param {{ label: string, value: string|number, hint?: string }} opts
 * @returns {HTMLElement}
 */
export function statCard({ label, value, hint }) {
  const children = [
    el('div', { class: 'stat-value' }, [String(value)]),
    el('div', { class: 'stat-label' }, [String(label)]),
  ];

  if (hint != null) {
    children.push(el('div', { class: 'stat-hint' }, [String(hint)]));
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
