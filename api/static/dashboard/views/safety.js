// views/safety.js — Safety alerts + incidents view

import { apiRequest } from '../core/api.js';
import { getParentId } from '../core/session.js';
import { deriveSafetyState } from '../core/safety.js';
import { el } from '../core/dom.js';
import { renderBanner } from '../components/banner.js';
import { skeleton } from '../components/skeleton.js';

function formatTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_) { return String(iso); }
}

// Render the alerts list sub-view
function renderAlerts(container, alerts, profiles) {
  container.textContent = '';

  if (alerts.length === 0) {
    container.appendChild(
      el('div', { class: 'empty-state' }, [
        el('div', { class: 'empty-icon', text: '✅' }),
        el('p', { text: 'No pending safety alerts. Everything looks good!' }),
      ])
    );
    return;
  }

  alerts.forEach((al) => {
    const profileName = profiles.find((p) => p.profile_id === al.profile_id);
    const pname = profileName ? profileName.name : 'Unknown';
    const alertId = al.alert_id || al.id || '';
    const severity = al.severity || 'medium';
    const sevClass = (severity === 'high' || severity === 'critical') ? ' severity-high' : '';

    const typeEl = el('div', { class: 'alert-type' }, [
      document.createTextNode(al.alert_type || al.incident_type || 'Alert'),
      el('span', { class: 'badge badge-severity-' + severity, text: severity }),
    ]);
    const detailEl = el('div', { class: 'alert-detail' }, [
      el('strong', { text: pname }),
      document.createTextNode(': ' + (al.message || al.content_snippet || 'No details')),
    ]);
    const timeEl = el('div', { class: 'alert-time', text: formatTime(al.created_at || al.timestamp) });

    const ackBtn = el('button', {
      class: 'btn btn-sm btn-outline',
      type: 'button',
      text: 'Acknowledge',
    });

    ackBtn.addEventListener('click', () => {
      ackBtn.disabled = true;
      ackBtn.textContent = '...';
      apiRequest('POST', '/api/safety/alerts/' + encodeURIComponent(alertId) + '/acknowledge')
        .then(() => {
          const alertCard = ackBtn.closest('.alert-card');
          if (alertCard) alertCard.remove();
          // Re-check if empty
          if (container.querySelectorAll('.alert-card').length === 0) {
            container.textContent = '';
            container.appendChild(
              el('div', { class: 'empty-state' }, [
                el('div', { class: 'empty-icon', text: '✅' }),
                el('p', { text: 'No pending safety alerts. Everything looks good!' }),
              ])
            );
          }
        })
        .catch(() => {
          ackBtn.disabled = false;
          ackBtn.textContent = 'Acknowledge';
        });
    });

    const alertContent = el('div', { class: 'alert-content' }, [typeEl, detailEl, timeEl]);
    const alertCard = el('div', { class: 'alert-card' + sevClass }, [alertContent, ackBtn]);
    container.appendChild(alertCard);
  });
}

// Render per-child incidents sub-view
function renderIncidents(container, profileId, profileName) {
  container.textContent = '';

  const header = el('div', { class: 'page-header' }, [
    el('h2', { text: 'Safety Incidents: ' + profileName }),
    el('p', { text: 'History of flagged content for this child' }),
  ]);

  const backBtn = el('button', { class: 'btn btn-outline', type: 'button', text: '← Back to Alerts' });
  header.appendChild(backBtn);
  container.appendChild(header);

  const loadingEl = el('div', { class: 'loading' }, [
    el('span', { class: 'spinner' }),
    document.createTextNode(' Loading incidents...'),
  ]);
  container.appendChild(loadingEl);

  backBtn.addEventListener('click', () => {
    // Re-render the full safety view
    render(container.parentElement || document.getElementById('main-content'));
  });

  apiRequest('GET', '/api/safety/incidents/' + encodeURIComponent(profileId) + '?days=30')
    .then((data) => {
      loadingEl.remove();
      const incidents = (data && data.incidents) || [];
      const count = el('p', { text: incidents.length + ' incident(s) found' });
      header.querySelector('p').replaceWith(count);

      if (incidents.length === 0) {
        container.appendChild(
          el('div', { class: 'empty-state' }, [
            el('div', { class: 'empty-icon', text: '✅' }),
            el('p', { text: 'No safety incidents recorded for this child.' }),
          ])
        );
        return;
      }

      const tableWrap = el('div', { class: 'table-wrap' });
      const table = document.createElement('table');
      const thead = document.createElement('thead');
      const headerRow = document.createElement('tr');
      ['Time', 'Type', 'Severity', 'Content'].forEach((h) => {
        const th = document.createElement('th');
        th.textContent = h;
        headerRow.appendChild(th);
      });
      thead.appendChild(headerRow);
      table.appendChild(thead);

      const tbody = document.createElement('tbody');
      incidents.forEach((inc) => {
        const tr = document.createElement('tr');
        const severity = inc.severity || 'medium';

        const tdTime = document.createElement('td');
        tdTime.textContent = formatTime(inc.created_at || inc.timestamp);
        const tdType = document.createElement('td');
        tdType.textContent = inc.incident_type || inc.type || '—';
        const tdSev = document.createElement('td');
        tdSev.appendChild(el('span', { class: 'badge badge-severity-' + severity, text: severity }));
        const tdContent = document.createElement('td');
        tdContent.textContent = inc.content_snippet || inc.content || '—';

        tr.appendChild(tdTime);
        tr.appendChild(tdType);
        tr.appendChild(tdSev);
        tr.appendChild(tdContent);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      tableWrap.appendChild(table);
      container.appendChild(tableWrap);
    })
    .catch((err) => {
      loadingEl.remove();
      container.appendChild(
        el('div', { class: 'msg-error', text: 'Failed to load incidents: ' + (err.detail || err.message || String(err)) })
      );
    });
}

export async function render(container, params) {
  container.textContent = '';

  // If a profileId param is provided, show incidents sub-view
  if (params && params.profileId) {
    const profileId = params.profileId;
    const profileName = params.profileName || 'Child';
    renderIncidents(container, profileId, profileName);
    return;
  }

  const header = el('div', { class: 'page-header' }, [
    el('h2', { text: 'Safety' }),
    el('p', { text: 'Review and acknowledge safety alerts for your children' }),
  ]);
  container.appendChild(header);

  // Skeletons while loading
  for (let i = 0; i < 3; i++) container.appendChild(skeleton('card'));

  const parentId = getParentId();
  let alerts = [];
  let profiles = [];
  let allIncidents = [];

  try {
    const profileData = await apiRequest('GET', '/api/profiles/parent/' + encodeURIComponent(parentId));
    profiles = (profileData && profileData.profiles) || [];
  } catch (_) {
    profiles = [];
  }

  try {
    const alertData = await apiRequest('GET', '/api/safety/alerts/' + encodeURIComponent(parentId));
    alerts = (alertData && alertData.alerts) || [];
  } catch (_) {
    alerts = [];
  }

  // Fetch incidents per child for banner state
  if (profiles.length > 0) {
    const results = await Promise.allSettled(
      profiles.map((p) =>
        apiRequest('GET', '/api/safety/incidents/' + encodeURIComponent(p.profile_id) + '?days=30')
          .then((d) => (d && d.incidents) || [])
          .catch(() => [])
      )
    );
    results.forEach((r) => {
      if (r.status === 'fulfilled') allIncidents = allIncidents.concat(r.value);
    });
  }

  // Remove skeletons
  container.textContent = '';
  container.appendChild(header);

  // Banner
  const safetyState = deriveSafetyState(alerts, allIncidents);
  container.appendChild(renderBanner(safetyState));

  // Alerts list
  const alertsSection = el('div', { class: 'alerts-list' });
  renderAlerts(alertsSection, alerts, profiles);
  container.appendChild(alertsSection);

  // Per-child incident links
  if (profiles.length > 0) {
    const linksBody = el('div', { class: 'profile-incident-links' });
    profiles.forEach((p) => {
      const btn = el('button', { class: 'btn btn-outline', type: 'button', text: p.name });
      btn.addEventListener('click', () => {
        // Navigate to incidents sub-view for this child
        location.hash = '#/safety?profileId=' + encodeURIComponent(p.profile_id) +
          '&profileName=' + encodeURIComponent(p.name);
      });
      linksBody.appendChild(btn);
    });

    const incidentCard = document.createElement('div');
    incidentCard.className = 'card';
    const cardHeader = el('div', { class: 'card-header' }, [el('h3', { text: 'View Incidents by Child' })]);
    incidentCard.appendChild(cardHeader);
    incidentCard.appendChild(linksBody);
    container.appendChild(incidentCard);
  }
}
