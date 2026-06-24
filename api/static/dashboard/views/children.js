// views/children.js — Child profile CRUD view
// Mirrors legacy renderProfiles(), showCreateProfileModal(), showEditProfileModal().
// Endpoints:
//   GET    /api/profiles/parent/{parentId}
//   POST   /api/profiles/
//   PATCH  /api/profiles/{profileId}
//   DELETE /api/profiles/{profileId}
//   GET    /api/profiles/{profileId}/export

import { apiRequest } from '../core/api.js';
import { getParentId } from '../core/session.js';
import { el } from '../core/dom.js';
import { skeleton } from '../components/skeleton.js';
import { showToast } from '../components/toast.js';
import { confirmDialog } from '../components/confirm.js';

const GRADES = [
  { v: '', l: 'Select grade...' },
  { v: 'pre-k', l: 'Pre-K' },
  { v: 'kindergarten', l: 'Kindergarten' },
  { v: '1st', l: '1st Grade' }, { v: '2nd', l: '2nd Grade' }, { v: '3rd', l: '3rd Grade' },
  { v: '4th', l: '4th Grade' }, { v: '5th', l: '5th Grade' }, { v: '6th', l: '6th Grade' },
  { v: '7th', l: '7th Grade' }, { v: '8th', l: '8th Grade' }, { v: '9th', l: '9th Grade' },
  { v: '10th', l: '10th Grade' }, { v: '11th', l: '11th Grade' }, { v: '12th', l: '12th Grade' },
  { v: 'college', l: 'College' },
];

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

function formGroup(labelText, type, id, value, extraAttrs) {
  const group = el('div', { class: 'form-group' });
  const label = el('label', { for: id, text: labelText });
  const input = document.createElement('input');
  input.type = type;
  input.id = id;
  if (value !== '' && value != null) input.value = String(value);
  if (extraAttrs) Object.entries(extraAttrs).forEach(([k, v]) => input.setAttribute(k, v));
  group.appendChild(label);
  group.appendChild(input);
  return group;
}

function gradeSelectGroup(labelText, id, selected) {
  const group = el('div', { class: 'form-group' });
  const label = el('label', { for: id, text: labelText });
  const select = document.createElement('select');
  select.id = id;
  const selLower = (selected || '').toLowerCase();
  GRADES.forEach(({ v, l }) => {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = l;
    if (v === selLower) opt.selected = true;
    select.appendChild(opt);
  });
  group.appendChild(label);
  group.appendChild(select);
  return group;
}

function showFormError(container, message) {
  container.textContent = '';
  container.appendChild(el('div', { class: 'msg-error', text: message }));
}

function exportProfile(profileId, name) {
  // Use raw fetch for blob download — apiRequest parses JSON
  const token = sessionStorage.getItem('sf_token');
  fetch('/api/profiles/' + encodeURIComponent(profileId) + '/export', {
    headers: { 'Authorization': 'Bearer ' + (token || '') },
  })
    .then((r) => {
      if (!r.ok) throw new Error('Export failed');
      return r.blob();
    })
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'child_data_' + (name || 'export') + '.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    })
    .catch((err) => {
      showToast('Export failed: ' + err.message);
    });
}

function showCreateProfileModal(parentId, onSuccess) {
  const overlay = el('div', { class: 'modal-overlay' });
  const modal = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' });

  const header = el('div', { class: 'modal-header' });
  const h3 = el('h3', { text: 'Add Child Profile' });
  const closeBtn = el('button', { class: 'btn-icon', type: 'button', text: '×' });
  header.appendChild(h3);
  header.appendChild(closeBtn);

  const body = el('div', { class: 'modal-body' });
  const errBox = el('div', { id: 'modal-error' });
  body.appendChild(errBox);
  body.appendChild(formGroup("Child's Name", 'text', 'new-name', ''));

  const row = el('div', { class: 'form-row' });
  row.appendChild(formGroup('Age', 'number', 'new-age', '', { min: '3', max: '25' }));
  row.appendChild(gradeSelectGroup('Grade', 'new-grade', ''));
  body.appendChild(row);

  // COPPA's verifiable-parental-consent requirement applies only to children
  // under 13. The consent group is hidden by default and revealed (and required)
  // only when an under-13 age is entered — see the age listener below.
  const consentGroup = el('div', { class: 'form-group checkbox-group' });
  consentGroup.style.display = 'none';
  const consentInput = document.createElement('input');
  consentInput.type = 'checkbox';
  consentInput.id = 'new-consent';
  const consentLabel = el('label', {
    for: 'new-consent',
    text: 'I have obtained verifiable parental consent for this child (required for children under 13 — COPPA).',
  });
  consentGroup.appendChild(consentInput);
  consentGroup.appendChild(consentLabel);
  body.appendChild(consentGroup);

  const footer = el('div', { class: 'modal-footer' });
  const cancelBtn = el('button', { class: 'btn btn-outline', type: 'button', text: 'Cancel' });
  const createBtn = el('button', { class: 'btn btn-primary', type: 'button', text: 'Create Profile' });
  footer.appendChild(cancelBtn);
  footer.appendChild(createBtn);

  modal.appendChild(header);
  modal.appendChild(body);
  modal.appendChild(footer);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  function onKey(e) { if (e.key === 'Escape') close(); }
  function close() { overlay.remove(); document.removeEventListener('keydown', onKey); }
  closeBtn.addEventListener('click', close);
  cancelBtn.addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  document.getElementById('new-name').focus();

  // Show/require the COPPA consent box only for under-13 children.
  const ageInput = document.getElementById('new-age');
  function syncConsentVisibility() {
    const a = parseInt(ageInput.value, 10);
    const under13 = !isNaN(a) && a < 13;
    consentGroup.style.display = under13 ? '' : 'none';
    if (!under13) consentInput.checked = false;
  }
  ageInput.addEventListener('input', syncConsentVisibility);
  syncConsentVisibility();

  createBtn.addEventListener('click', () => {
    errBox.textContent = '';
    const name = document.getElementById('new-name').value.trim();
    const age = parseInt(document.getElementById('new-age').value, 10);
    const grade = document.getElementById('new-grade').value;
    const consent = document.getElementById('new-consent').checked;

    if (!name) { showFormError(errBox, 'Name is required'); return; }
    if (isNaN(age) || age < 3 || age > 25) { showFormError(errBox, 'Age must be between 3 and 25'); return; }
    if (!grade) { showFormError(errBox, 'Grade is required'); return; }
    if (age < 13 && !consent) {
      showFormError(errBox, 'Verifiable parental consent is required for children under 13 (COPPA).');
      return;
    }

    createBtn.disabled = true;
    createBtn.textContent = 'Creating...';

    apiRequest('POST', '/api/profiles/', {
      parent_id: parentId,
      name,
      age,
      grade_level: grade,
      model_role: 'student',
      parental_consent_verified: consent,
    })
      .then(() => { close(); onSuccess(); })
      .catch((err) => {
        showFormError(errBox, err.detail || err.message || 'Create failed');
        createBtn.disabled = false;
        createBtn.textContent = 'Create Profile';
      });
  });
}

function showEditProfileModal(profile, onSuccess) {
  const overlay = el('div', { class: 'modal-overlay' });
  const modal = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' });

  const header = el('div', { class: 'modal-header' });
  const h3 = el('h3', { text: 'Edit Profile: ' + profile.name });
  const closeBtn = el('button', { class: 'btn-icon modal-close', type: 'button', text: '×' });
  header.appendChild(h3);
  header.appendChild(closeBtn);

  const body = el('div', { class: 'modal-body' });
  const errBox = el('div', { id: 'modal-error' });
  body.appendChild(errBox);
  body.appendChild(formGroup('Name', 'text', 'edit-name', profile.name));

  const row = el('div', { class: 'form-row' });
  row.appendChild(formGroup('Age', 'number', 'edit-age', profile.age, { min: '3', max: '25' }));
  row.appendChild(gradeSelectGroup('Grade', 'edit-grade', profile.grade_level || profile.grade));
  body.appendChild(row);

  const timeLimitGroup = formGroup(
    'Daily Time Limit (minutes)',
    'number',
    'edit-time-limit',
    profile.daily_time_limit_minutes || 120,
    { min: '0', max: '1440' }
  );
  timeLimitGroup.appendChild(el('div', { class: 'hint', text: '0 = unlimited, max 1440 (24 hours)' }));
  body.appendChild(timeLimitGroup);

  const footer = el('div', { class: 'modal-footer' });
  const cancelBtn = el('button', { class: 'btn btn-outline', type: 'button', text: 'Cancel' });
  const saveBtn = el('button', { class: 'btn btn-primary', type: 'button', text: 'Save Changes' });
  footer.appendChild(cancelBtn);
  footer.appendChild(saveBtn);

  modal.appendChild(header);
  modal.appendChild(body);
  modal.appendChild(footer);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  function onKey(e) { if (e.key === 'Escape') close(); }
  function close() { overlay.remove(); document.removeEventListener('keydown', onKey); }
  closeBtn.addEventListener('click', close);
  cancelBtn.addEventListener('click', close);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  document.getElementById('edit-name').focus();

  saveBtn.addEventListener('click', () => {
    errBox.textContent = '';
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';

    const updates = {
      name: document.getElementById('edit-name').value.trim(),
      age: parseInt(document.getElementById('edit-age').value, 10),
      grade_level: document.getElementById('edit-grade').value,
    };
    const tl = parseInt(document.getElementById('edit-time-limit').value, 10);
    if (!isNaN(tl)) updates.daily_time_limit_minutes = tl;

    apiRequest('PATCH', '/api/profiles/' + encodeURIComponent(profile.profile_id), updates)
      .then(() => { close(); onSuccess(); })
      .catch((err) => {
        showFormError(errBox, err.detail || err.message || 'Update failed');
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save Changes';
      });
  });
}

async function loadProfiles(container) {
  container.textContent = '';

  const header = el('div', { class: 'page-header' });
  const title = el('h2', { text: 'Children' });
  const desc = el('p', { text: "Manage your children's profiles and settings" });
  const headerActions = el('div', { class: 'header-actions' });
  const addBtn = el('button', { class: 'btn btn-primary', id: 'add-child-btn', type: 'button', text: '+ Add Child' });
  headerActions.appendChild(addBtn);
  header.appendChild(title);
  header.appendChild(desc);
  header.appendChild(headerActions);
  container.appendChild(header);

  // Skeletons while loading
  const skeletons = el('div', { class: 'card-grid' });
  for (let i = 0; i < 3; i++) skeletons.appendChild(skeleton('card'));
  container.appendChild(skeletons);

  const parentId = getParentId();
  let profiles = [];

  try {
    const data = await apiRequest('GET', '/api/profiles/parent/' + encodeURIComponent(parentId));
    profiles = (data && data.profiles) || [];
  } catch (_) {
    profiles = [];
  }

  // Remove skeletons
  skeletons.remove();

  addBtn.addEventListener('click', () => {
    showCreateProfileModal(parentId, () => loadProfiles(container));
  });

  if (profiles.length === 0) {
    container.appendChild(
      el('div', { class: 'empty-state' }, [
        el('div', { class: 'empty-icon', text: '👶' }),
        el('p', { text: 'No child profiles yet. Click "Add Child" to create one.' }),
      ])
    );
    return;
  }

  const cardGrid = el('div', { class: 'card-grid' });
  profiles.forEach((p) => {
    const initial = (p.name || '?')[0].toUpperCase();
    const lastActive = p.last_active ? timeAgo(p.last_active) : 'Never';

    const avatar = el('div', { class: 'profile-avatar', text: initial });
    const statusBadge = p.is_active
      ? el('span', { class: 'badge badge-active', text: 'Active' })
      : el('span', { class: 'badge badge-inactive', text: 'Inactive' });
    const nameEl = el('div', { class: 'profile-name' }, [document.createTextNode(p.name + ' '), statusBadge]);
    const metaEl = el('div', {
      class: 'profile-meta',
      text: 'Age ' + p.age + ' · Grade ' + (p.grade_level || p.grade || '?'),
    });
    const nameMeta = el('div', {}, [nameEl, metaEl]);
    const profileTop = el('div', { class: 'profile-top' }, [avatar, nameMeta]);

    const statsEl = el('div', { class: 'profile-stats' }, [
      el('div', { class: 'profile-stat' }, [
        el('div', { class: 'value', text: String(p.total_sessions || 0) }),
        el('div', { class: 'label', text: 'Sessions' }),
      ]),
      el('div', { class: 'profile-stat' }, [
        el('div', { class: 'value', text: String(p.total_questions || 0) }),
        el('div', { class: 'label', text: 'Questions' }),
      ]),
    ]);

    const lastActiveEl = el('div', {
      class: 'profile-last-active',
      text: 'Last active: ' + lastActive,
    });

    const actions = el('div', { class: 'profile-actions' });

    const editBtn = el('button', { class: 'btn btn-sm btn-primary', type: 'button', text: 'Edit' });
    editBtn.addEventListener('click', () => showEditProfileModal(p, () => loadProfiles(container)));
    actions.appendChild(editBtn);

    if (p.is_active) {
      const deactivateBtn = el('button', { class: 'btn btn-sm btn-danger', type: 'button', text: 'Deactivate' });
      deactivateBtn.addEventListener('click', () => {
        confirmDialog({
          title: 'Deactivate profile?',
          message:
            'Deactivate ' + p.name + "'s profile? They won't be able to use the " +
            'tutor until you reactivate it.',
          confirmText: 'Deactivate',
          danger: true,
        }).then((ok) => {
          if (!ok) return;
          apiRequest('DELETE', '/api/profiles/' + encodeURIComponent(p.profile_id))
            .then(() => loadProfiles(container))
            .catch((err) => showToast(err.detail || 'Deactivation failed'));
        });
      });
      actions.appendChild(deactivateBtn);
    }

    const exportBtn = el('button', { class: 'btn btn-sm btn-outline', type: 'button', text: 'Export Data' });
    exportBtn.addEventListener('click', () => exportProfile(p.profile_id, p.name));
    actions.appendChild(exportBtn);

    const incidentsBtn = el('button', { class: 'btn btn-sm btn-outline', type: 'button', text: 'Incidents' });
    incidentsBtn.addEventListener('click', () => {
      location.hash = '#/safety?profileId=' + encodeURIComponent(p.profile_id) +
        '&profileName=' + encodeURIComponent(p.name);
    });
    actions.appendChild(incidentsBtn);

    const profileCard = el('div', { class: 'profile-card' + (p.is_active ? '' : ' inactive') }, [
      profileTop,
      statsEl,
      lastActiveEl,
      actions,
    ]);
    cardGrid.appendChild(profileCard);
  });

  container.appendChild(cardGrid);
}

export async function render(container, params) {
  await loadProfiles(container);
}
