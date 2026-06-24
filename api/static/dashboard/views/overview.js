// views/overview.js — Overview view
// Fetches profiles, per-child safety alerts/incidents, renders banner + activity cards.

import { apiRequest } from '../core/api.js';
import { getParentId } from '../core/session.js';
import { deriveSafetyState } from '../core/safety.js';
import { el } from '../core/dom.js';
import { renderBanner } from '../components/banner.js';
import { statCard, card } from '../components/card.js';
import { skeleton } from '../components/skeleton.js';

function timeAgo(iso) {
  if (!iso) return 'Never';
  try {
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (diff < 60) return 'Just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  } catch (_) { return String(iso); }
}

export async function render(container) {
  container.textContent = '';

  // Page header
  const header = el('div', { class: 'page-header' }, [
    el('h2', { text: 'Welcome Back' }),
    el('p', { text: "Here is an overview of your children's activity" }),
  ]);
  container.appendChild(header);

  // Show skeletons while loading
  const skeletonRow = el('div', { class: 'stat-row' });
  for (let i = 0; i < 3; i++) skeletonRow.appendChild(skeleton('card'));
  container.appendChild(skeletonRow);

  const parentId = getParentId();

  let profiles = [];
  let allAlerts = [];
  let allIncidents = [];

  try {
    const profileData = await apiRequest('GET', '/api/profiles/parent/' + encodeURIComponent(parentId));
    profiles = (profileData && profileData.profiles) || [];
  } catch (_) {
    profiles = [];
  }

  // Fetch alerts and incidents for safety state
  try {
    const alertData = await apiRequest('GET', '/api/safety/alerts/' + encodeURIComponent(parentId));
    allAlerts = (alertData && alertData.alerts) || [];
  } catch (_) {
    allAlerts = [];
  }

  // Fetch per-child incidents (best-effort, in parallel)
  if (profiles.length > 0) {
    const incidentResults = await Promise.allSettled(
      profiles.map((p) =>
        apiRequest('GET', '/api/safety/incidents/' + encodeURIComponent(p.profile_id) + '?days=30')
          .then((d) => (d && d.incidents) || [])
          .catch(() => [])
      )
    );
    incidentResults.forEach((r) => {
      if (r.status === 'fulfilled') allIncidents = allIncidents.concat(r.value);
    });
  }

  // Remove skeletons, rebuild
  container.textContent = '';
  container.appendChild(header);

  // Safety banner
  const safetyState = deriveSafetyState(allAlerts, allIncidents);
  const bannerEl = renderBanner(safetyState);
  container.appendChild(bannerEl);

  // Summary stat cards — each links to its detail tab.
  let totalSessions = 0;
  profiles.forEach((p) => {
    totalSessions += p.total_sessions || 0;
  });

  const statRow = el('div', { class: 'stat-row' });
  statRow.appendChild(statCard({ label: 'Children', value: profiles.length, href: '#/children' }));
  statRow.appendChild(statCard({ label: 'Total Sessions', value: totalSessions, href: '#/activity' }));
  statRow.appendChild(statCard({ label: 'Pending Alerts', value: allAlerts.length, href: '#/safety' }));
  container.appendChild(statRow);

  // Pending action cards (COPPA consent, billing/setup)
  const pendingActions = [];

  profiles.forEach((p) => {
    if (p.age < 13 && !p.parental_consent_verified) {
      pendingActions.push(
        el('div', { class: 'alert-card severity-high' }, [
          el('div', { class: 'alert-content' }, [
            el('div', { class: 'alert-type', text: 'COPPA Consent Required' }),
            el('div', { class: 'alert-detail', text: p.name + ' is under 13 and requires verified parental consent.' }),
          ]),
        ])
      );
    }
  });

  if (pendingActions.length > 0) {
    const actionsSection = el('div', { class: 'page-section' });
    actionsSection.appendChild(el('h3', { class: 'section-title', text: 'Action Required' }));
    pendingActions.forEach((a) => actionsSection.appendChild(a));
    container.appendChild(actionsSection);
  }

  // Per-child activity summary cards
  if (profiles.length === 0) {
    container.appendChild(
      el('div', { class: 'empty-state' }, [
        el('div', { class: 'empty-icon', text: '👶' }),
        el('p', { text: 'No child profiles yet. Go to Children to add one.' }),
      ])
    );
  } else {
    const cardGrid = el('div', { class: 'card-grid' });
    profiles.forEach((p) => {
      const initial = (p.name || '?')[0].toUpperCase();
      const lastActive = p.last_active ? timeAgo(p.last_active) : 'Never';

      // Use profile-level totals (no per-session API available in overview context)
      const sessions = p.total_sessions || 0;
      // Pending safety alerts for this child (from the parent's alert list).
      const childAlerts = allAlerts.filter((a) => a.profile_id === p.profile_id).length;

      const avatar = el('div', { class: 'profile-avatar', text: initial });
      const nameEl = el('div', { class: 'profile-name', text: p.name });
      const metaEl = el('div', { class: 'profile-meta', text: 'Age ' + p.age + ' · ' + (p.grade_level || p.grade || 'N/A') });
      const nameMeta = el('div', {}, [nameEl, metaEl]);
      const profileTop = el('div', { class: 'profile-top' }, [avatar, nameMeta]);

      const statsEl = el('div', { class: 'profile-stats' }, [
        el('div', { class: 'profile-stat' }, [
          el('div', { class: 'value', text: String(sessions) }),
          el('div', { class: 'label', text: 'Sessions' }),
        ]),
        el('div', { class: 'profile-stat' }, [
          el('div', { class: 'value', text: String(childAlerts) }),
          el('div', { class: 'label', text: 'Alerts' }),
        ]),
      ]);

      const lastActiveEl = el('div', { class: 'profile-last-active', text: 'Last active: ' + lastActive });

      // The whole card links to the Children tab (manage this child).
      const profileCard = el(
        'a',
        {
          class: 'profile-card profile-card-link' + (p.is_active ? '' : ' inactive'),
          href: '#/children',
        },
        [profileTop, statsEl, lastActiveEl]
      );

      cardGrid.appendChild(profileCard);
    });
    container.appendChild(cardGrid);
  }
}
