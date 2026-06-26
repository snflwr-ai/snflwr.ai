// components/safetySetup.js
//
// The "your child is now protected" moment. Shown immediately after a child
// profile is created (views/children.js), this guided step closes the two
// real launch blockers it stands in for:
//   1. confirms WHERE crisis alerts reach the parent (the notification email), and
//   2. for under-13 children, kicks off verifiable parental consent (COPPA) via
//      POST /api/parental-consent/request — the real email flow, not a checkbox.
//
// Tone: calm, reassuring, transparent. It uses the dashboard's semantic safety
// palette on purpose — crisis-red and attention-amber to be honest about what
// triggers an alert, safe-green to confirm protection is in place.
//
// No innerHTML / raw html anywhere — everything is built with the el() helper
// and safe DOM construction, matching this codebase's strict XSS posture.

import { apiRequest } from '../core/api.js';
import { el } from '../core/dom.js';
import { getEmail } from '../core/session.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

// What a parent will actually be alerted about — shown verbatim so there are no
// surprises. Each maps to a severity in the safety pipeline.
const ALERT_TRIGGERS = [
  { sev: 'crisis', label: 'Self-harm or crisis', note: 'You are notified immediately, with 988 resources.' },
  { sev: 'attention', label: 'Bullying or violence', note: 'Flagged for your review on the dashboard.' },
  { sev: 'attention', label: 'Age-inappropriate content', note: 'Blocked, then surfaced to you.' },
];

export function maskEmail(email) {
  const [user, domain] = String(email || '').split('@');
  if (!user || !domain) return email || '';
  const head = user.length <= 2 ? user[0] : user.slice(0, 2);
  return `${head}${'•'.repeat(Math.max(1, user.length - 2))}@${domain}`;
}

// --- safe SVG builders (return real DOM nodes, never strings) ---------------
function svg(viewBox, className) {
  const node = document.createElementNS(SVG_NS, 'svg');
  node.setAttribute('viewBox', viewBox);
  node.setAttribute('fill', 'none');
  node.setAttribute('aria-hidden', 'true');
  if (className) node.setAttribute('class', className);
  return node;
}
function path(d, attrs = {}) {
  const p = document.createElementNS(SVG_NS, 'path');
  p.setAttribute('d', d);
  for (const [k, v] of Object.entries(attrs)) p.setAttribute(k, v);
  return p;
}
function shieldSvg() {
  const s = svg('0 0 24 24', 'sfx-shield-svg');
  s.appendChild(path('M12 2.5 4.5 5.6v5.2c0 4.4 3 8.5 7.5 9.7 4.5-1.2 7.5-5.3 7.5-9.7V5.6L12 2.5Z', {
    fill: 'currentColor', 'fill-opacity': '0.12', stroke: 'currentColor', 'stroke-width': '1.5',
  }));
  s.appendChild(path('M8.6 12.2l2.3 2.3 4.5-4.7', {
    class: 'sfx-shield-check', stroke: 'currentColor', 'stroke-width': '2',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  }));
  return s;
}
function mailIcon() {
  const s = svg('0 0 24 24', 'sfx-ic');
  const r = document.createElementNS(SVG_NS, 'rect');
  Object.entries({ x: '3', y: '5', width: '18', height: '14', rx: '2.5',
    stroke: 'currentColor', 'stroke-width': '1.7' }).forEach(([k, v]) => r.setAttribute(k, v));
  s.appendChild(r);
  s.appendChild(path('m4 7 8 6 8-6', { stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round' }));
  return s;
}
function checkIcon() {
  const s = svg('0 0 24 24', 'sfx-mini-check');
  s.appendChild(path('m5 12.5 4 4 10-10', {
    stroke: 'currentColor', 'stroke-width': '2.2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  }));
  return s;
}

/**
 * Show the post-creation safety-notification setup step.
 * @param {object} opts
 * @param {object} opts.profile  the create-profile response (to_dict + age_verification)
 * @param {function} opts.onDone called when the parent finishes the step
 */
export function showSafetySetup({ profile, onDone }) {
  const av = (profile && profile.age_verification) || {};
  const childName = profile.name || 'your child';
  const profileId = profile.profile_id;
  const underThirteen = !!av.is_under_13 || !!av.requires_consent;
  const email = getEmail();

  const overlay = el('div', { class: 'modal-overlay sfx-overlay' });
  const modal = el('div', {
    class: 'modal sfx-modal',
    role: 'dialog',
    'aria-modal': 'true',
    'aria-labelledby': 'sfx-title',
  });

  const close = (completed) => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    if (completed && typeof onDone === 'function') onDone();
  };
  const onKey = (e) => { if (e.key === 'Escape') close(true); };
  document.addEventListener('keydown', onKey);

  // ---- Hero: the reassuring confirmation -----------------------------------
  const hero = el('div', { class: 'sfx-hero' });
  const badge = el('div', { class: 'sfx-shield' });
  badge.appendChild(shieldSvg());
  hero.appendChild(badge);
  hero.appendChild(el('p', { class: 'sfx-eyebrow', text: 'Profile created' }));
  hero.appendChild(el('h3', { id: 'sfx-title', class: 'sfx-title', text: `${childName} is protected` }));
  hero.appendChild(el('p', {
    class: 'sfx-sub',
    text: 'Every message is checked by the safety pipeline. Set up where we reach you if something needs your attention.',
  }));
  modal.appendChild(hero);

  const body = el('div', { class: 'sfx-body' });

  // ---- Row 1: notification email -------------------------------------------
  const emailRow = el('div', { class: 'sfx-row', 'data-reveal': '1' });
  const emailIcon = el('div', { class: 'sfx-row-icon sfx-ic-safe' });
  emailIcon.appendChild(mailIcon());
  emailRow.appendChild(emailIcon);
  const emailMain = el('div', { class: 'sfx-row-main' });
  emailMain.appendChild(el('div', { class: 'sfx-row-title', text: 'Alerts reach you here' }));
  emailMain.appendChild(el('div', {
    class: 'sfx-row-note',
    text: email ? maskEmail(email) : 'No email on file — add one in Settings.',
  }));
  emailRow.appendChild(emailMain);
  body.appendChild(emailRow);

  // ---- Row 2: transparency — what we alert about ---------------------------
  const triggers = el('div', { class: 'sfx-triggers', 'data-reveal': '2' });
  triggers.appendChild(el('div', { class: 'sfx-triggers-head', text: "What we'll tell you about" }));
  ALERT_TRIGGERS.forEach((t) => {
    const chip = el('div', { class: `sfx-trigger sfx-sev-${t.sev}` });
    chip.appendChild(el('span', { class: 'sfx-dot' }));
    const tx = el('div', { class: 'sfx-trigger-text' });
    tx.appendChild(el('span', { class: 'sfx-trigger-label', text: t.label }));
    tx.appendChild(el('span', { class: 'sfx-trigger-note', text: t.note }));
    chip.appendChild(tx);
    triggers.appendChild(chip);
  });
  body.appendChild(triggers);

  // ---- Row 3: COPPA consent (under-13 only) --------------------------------
  if (underThirteen) {
    const consent = el('div', { class: 'sfx-consent', 'data-reveal': '3' });
    consent.appendChild(el('div', { class: 'sfx-consent-head', text: 'One more step — parental consent' }));
    consent.appendChild(el('div', {
      class: 'sfx-consent-note',
      text: `Because ${childName} is under 13, COPPA requires your verifiable consent before they can chat. We'll email a one-tap confirmation link${email ? ' to ' + maskEmail(email) : ''}.`,
    }));
    // Informed consent: link the official FTC explanation of COPPA (we link the
    // authoritative source rather than hosting our own copy, which would go stale).
    const learn = el('div', { class: 'sfx-legal' });
    learn.appendChild(el('a', {
      class: 'sfx-legal-link',
      href: 'https://www.ftc.gov/legal-library/browse/rules/childrens-online-privacy-protection-rule-coppa',
      target: '_blank',
      rel: 'noopener noreferrer',
      text: 'What COPPA requires ↗',
    }));
    consent.appendChild(learn);
    const statusEl = el('div', { class: 'sfx-consent-status', role: 'status' });
    const sendBtn = el('button', { class: 'btn-primary sfx-consent-btn', type: 'button', text: 'Send consent request' });
    sendBtn.addEventListener('click', () => {
      sendBtn.disabled = true;
      sendBtn.textContent = 'Sending…';
      statusEl.className = 'sfx-consent-status';
      statusEl.replaceChildren();
      apiRequest('POST', '/api/parental-consent/request', {
        profile_id: profileId,
        parent_email: email,
        child_name: childName,
        child_age: av.age || profile.age,
      })
        .then(() => {
          consent.classList.add('is-sent');
          statusEl.className = 'sfx-consent-status is-ok';
          statusEl.replaceChildren(
            checkIcon(),
            el('span', { text: `Sent. Check your inbox to activate ${childName}.` }),
          );
          sendBtn.textContent = 'Resend';
          sendBtn.disabled = false;
        })
        .catch((err) => {
          statusEl.className = 'sfx-consent-status is-err';
          statusEl.replaceChildren(el('span', {
            text: (err && (err.detail || err.message)) ||
              'Could not send — check the email in Settings and try again.',
          }));
          sendBtn.textContent = 'Try again';
          sendBtn.disabled = false;
        });
    });
    consent.appendChild(sendBtn);
    consent.appendChild(statusEl);
    body.appendChild(consent);
  }

  modal.appendChild(body);

  // ---- Footer --------------------------------------------------------------
  const footer = el('div', { class: 'sfx-footer' });
  const doneBtn = el('button', {
    class: 'btn-primary sfx-done',
    type: 'button',
    text: underThirteen ? 'Done — I’ll confirm by email' : "Done — we're set",
  });
  doneBtn.addEventListener('click', () => close(true));
  footer.appendChild(doneBtn);
  modal.appendChild(footer);

  overlay.appendChild(modal);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(true); });
  document.body.appendChild(overlay);

  requestAnimationFrame(() => modal.classList.add('is-in'));
  doneBtn.focus();
}
