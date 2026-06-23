/**
 * nav.js — Responsive navigation component.
 *
 * Renders a semantic <nav> containing:
 *   - .sidebar > nav on ≥768 px (CSS shows/hides via media query)
 *   - .nav-bottom for mobile (CSS shows/hides)
 *
 * @param {string} currentView  - Active view key (e.g. 'overview')
 * @param {Function} onNavigate - Called with the view key when user activates an item
 * @returns {HTMLElement}       - The <nav> wrapper element
 */

import { el } from '../core/dom.js';

const NAV_ITEMS = [
  { view: 'overview',  icon: '🏠', label: 'Overview' },
  { view: 'safety',   icon: '🛡️', label: 'Safety' },
  { view: 'activity', icon: '📊', label: 'Activity' },
  { view: 'children', icon: '👦', label: 'Children' },
  { view: 'settings', icon: '⚙️', label: 'Settings' },
];

function buildItems(currentView, onNavigate, mobile) {
  return NAV_ITEMS.map(({ view, icon, label }) => {
    const isActive = view === currentView;

    const btnAttrs = {
      class: 'nav-item' + (isActive ? ' active' : ''),
      type: 'button',
      tabindex: '0',
    };
    if (isActive) {
      btnAttrs['aria-current'] = 'page';
    }

    const iconEl = el('span', { class: 'nav-icon', 'aria-hidden': 'true' }, [icon]);
    const labelEl = mobile
      ? el('span', { class: 'nav-text' }, [label])
      : el('span', {}, [label]);

    const btn = el('button', btnAttrs, [iconEl, labelEl]);

    btn.addEventListener('click', () => onNavigate(view));
    btn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onNavigate(view);
      }
    });

    return btn;
  });
}

export function renderNav(currentView, onNavigate) {
  // Sidebar (desktop)
  const sidebarNav = el('nav', { 'aria-label': 'Main navigation' }, [
    el('div', { class: 'sidebar-nav' }, buildItems(currentView, onNavigate, false)),
  ]);
  const sidebar = el('aside', { class: 'sidebar' }, [sidebarNav]);

  // Bottom bar (mobile)
  const bottomNav = el('nav', {
    class: 'nav-bottom',
    'aria-label': 'Main navigation',
  }, buildItems(currentView, onNavigate, true));

  // Wrap both in a fragment-like container so callers get one node.
  // Views that append this to .layout will get both pieces.
  const wrapper = el('div', { class: 'nav-wrapper' }, [sidebar, bottomNav]);
  return wrapper;
}
