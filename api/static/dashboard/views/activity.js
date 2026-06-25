// views/activity.js — Activity / Analytics view
// Mirrors legacy renderAnalytics() + loadAnalytics().
// Endpoints:
//   GET /api/profiles/parent/{parentId}
//   GET /api/analytics/usage/{profileId}?days={days}
//   GET /api/analytics/activity/{profileId}?limit=20

import { apiRequest } from '../core/api.js';
import { getParentId } from '../core/session.js';
import { formatDuration } from '../core/format.js';
import { el } from '../core/dom.js';
import { statCard } from '../components/card.js';
import { skeleton } from '../components/skeleton.js';
import { svgChart } from '../components/svgChart.js';

function formatTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_) { return String(iso); }
}

async function loadActivityData(mainEl, profileId, days) {
  mainEl.textContent = '';
  mainEl.appendChild(
    el('div', { class: 'loading' }, [
      el('span', { class: 'spinner' }),
      document.createTextNode(' Loading...'),
    ])
  );

  const ep = encodeURIComponent(profileId);
  let usage = {};
  let sessions = [];

  try {
    const [usageData, activityData] = await Promise.all([
      apiRequest('GET', '/api/analytics/usage/' + ep + '?days=' + days).catch(() => ({})),
      apiRequest('GET', '/api/analytics/activity/' + ep + '?limit=20').catch(() => ({ sessions: [] })),
    ]);
    usage = usageData || {};
    sessions = (activityData && activityData.sessions) || [];
  } catch (_) {
    usage = {};
    sessions = [];
  }

  mainEl.textContent = '';

  // Stat cards
  const totalSessions = usage.total_sessions || usage.session_count || 0;
  const totalMessages = usage.total_messages || usage.message_count || 0;
  const totalMinutes = usage.total_minutes || 0;

  const statRow = el('div', { class: 'stat-row' });
  statRow.appendChild(statCard({ label: 'Sessions (' + days + 'd)', value: totalSessions }));
  statRow.appendChild(statCard({ label: 'Messages', value: totalMessages }));
  statRow.appendChild(statCard({ label: 'Time', value: formatDuration(totalMinutes) }));
  mainEl.appendChild(statRow);

  // Build a simple per-session chart: each session is a bar showing its duration.
  // Use the last up to 14 sessions for readability.
  const chartSessions = sessions.slice(-14);
  const chartSeries = chartSessions.map((s) => Number(s.duration_minutes) || 0);
  const chartLabels = chartSessions.map((s) => {
    const d = s.started_at || s.created_at;
    if (!d) return '';
    try {
      const date = new Date(d);
      return (date.getMonth() + 1) + '/' + date.getDate();
    } catch (_) { return ''; }
  });

  if (chartSeries.length > 0) {
    const chartEl = svgChart({
      series: chartSeries,
      labels: chartLabels,
      title: 'Session Duration (minutes)',
    });
    mainEl.appendChild(chartEl);
  }

  // Recent sessions table
  if (sessions.length > 0) {
    const tableWrap = el('div', { class: 'table-wrap' });
    const table = document.createElement('table');
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    ['Started', 'Duration', 'Messages', 'Status'].forEach((h) => {
      const th = document.createElement('th');
      th.textContent = h;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    sessions.forEach((s) => {
      const tr = document.createElement('tr');
      const duration = s.duration_minutes ? formatDuration(s.duration_minutes) : '—';

      const tdStart = document.createElement('td');
      tdStart.textContent = formatTime(s.started_at || s.created_at);
      const tdDur = document.createElement('td');
      tdDur.textContent = duration;
      const tdMsg = document.createElement('td');
      tdMsg.textContent = String(s.message_count || s.total_messages || '—');
      const tdStatus = document.createElement('td');
      tdStatus.textContent = s.status || s.session_status || '—';

      tr.appendChild(tdStart);
      tr.appendChild(tdDur);
      tr.appendChild(tdMsg);
      tr.appendChild(tdStatus);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    tableWrap.appendChild(table);

    const sessionCard = document.createElement('div');
    sessionCard.className = 'card';
    const sessionCardHeader = el('div', { class: 'card-header' }, [el('h3', { text: 'Recent Sessions' })]);
    sessionCard.appendChild(sessionCardHeader);
    sessionCard.appendChild(tableWrap);
    mainEl.appendChild(sessionCard);
  } else {
    mainEl.appendChild(el('div', { class: 'msg-info', text: 'No session activity in this period.' }));
  }
}

export async function render(container, params) {
  container.textContent = '';

  const header = el('div', { class: 'page-header' }, [
    el('h2', { text: 'Activity' }),
    el('p', { text: 'Usage statistics and session activity' }),
  ]);
  container.appendChild(header);

  // Show skeleton while loading profiles
  for (let i = 0; i < 2; i++) container.appendChild(skeleton('card'));

  const parentId = getParentId();
  let profiles = [];

  try {
    const data = await apiRequest('GET', '/api/profiles/parent/' + encodeURIComponent(parentId));
    profiles = (data && data.profiles) || [];
  } catch (_) {
    profiles = [];
  }

  // Remove skeletons
  while (container.children.length > 1) container.removeChild(container.lastChild);

  if (profiles.length === 0) {
    container.appendChild(
      el('div', { class: 'empty-state' }, [
        el('div', { class: 'empty-icon', text: '📊' }),
        el('p', { text: 'No child profiles to show activity for.' }),
      ])
    );
    return;
  }

  // Determine initially-selected profile
  const selectedId = (params && params.profileId) || profiles[0].profile_id;
  let currentProfileId = selectedId;
  let currentDays = 7;

  // Profile selector
  const profileSelector = el('div', { class: 'profile-selector' });
  const profileLabel = el('label', { for: 'activity-profile', text: 'Child:' });
  const profileSelect = document.createElement('select');
  profileSelect.id = 'activity-profile';
  profiles.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.profile_id;
    opt.textContent = p.name;
    if (p.profile_id === selectedId) opt.selected = true;
    profileSelect.appendChild(opt);
  });
  profileSelector.appendChild(profileLabel);
  profileSelector.appendChild(profileSelect);
  container.appendChild(profileSelector);

  // Day selector
  const daySelector = el('div', { class: 'day-selector' });
  [7, 30, 90].forEach((d) => {
    const btn = el('button', { type: 'button', text: d + ' days' });
    if (d === 7) btn.className = 'active';
    btn.addEventListener('click', () => {
      container.querySelectorAll('.day-selector button').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currentDays = d;
      loadActivityData(activityContent, currentProfileId, currentDays);
    });
    daySelector.appendChild(btn);
  });
  container.appendChild(daySelector);

  // Activity content area
  const activityContent = el('div', { id: 'activity-content' });
  container.appendChild(activityContent);

  profileSelect.addEventListener('change', () => {
    currentProfileId = profileSelect.value;
    loadActivityData(activityContent, currentProfileId, currentDays);
  });

  // Initial load
  await loadActivityData(activityContent, currentProfileId, currentDays);
}
