/* ================================================================
   snflwr.ai - Parent Dashboard SPA
   Vanilla JS, no framework, no build step.

   Security: All dynamic content is escaped through escHtml() which
   uses document.createTextNode() for safe HTML entity encoding.
   Data comes only from the user's own authenticated API responses.
   ================================================================ */

(function () {
    'use strict';

    // ── State ────────────────────────────────────────────────────
    var state = {
        token: sessionStorage.getItem('sf_token') || null,
        parentId: sessionStorage.getItem('sf_parent_id') || null,
        email: sessionStorage.getItem('sf_email') || '',
        profiles: [],
        alerts: [],
        currentView: 'overview',
        selectedProfileId: null
    };

    var app = document.getElementById('app');

    // ── Sanitization ─────────────────────────────────────────────
    // All dynamic values MUST go through escHtml/escAttr before
    // being interpolated into markup strings.

    function escHtml(str) {
        if (str === null || str === undefined) return '';
        var d = document.createElement('div');
        d.appendChild(document.createTextNode(String(str)));
        return d.innerHTML;
    }

    function escAttr(str) {
        return escHtml(str).replace(/"/g, '&quot;');
    }

    // ── API helpers ──────────────────────────────────────────────
    function getCsrfToken() {
        var m = document.cookie.match(/csrf_token=([^;]+)/);
        return m ? m[1] : '';
    }

    function apiRequest(method, path, body) {
        var headers = {
            'Authorization': 'Bearer ' + state.token,
            'Content-Type': 'application/json'
        };
        if (['POST', 'PATCH', 'DELETE', 'PUT'].indexOf(method.toUpperCase()) !== -1) {
            headers['X-CSRF-Token'] = getCsrfToken();
        }
        var opts = { method: method, headers: headers };
        if (body) { opts.body = JSON.stringify(body); }
        return fetch(path, opts).then(function (resp) {
            if (resp.status === 401) {
                logout();
                return Promise.reject(new Error('Session expired'));
            }
            return resp;
        });
    }

    // ── Navigation ───────────────────────────────────────────────
    function navigate(view, params) {
        state.currentView = view;
        if (params && params.profileId) { state.selectedProfileId = params.profileId; }
        render();
    }

    // ── Render dispatcher ────────────────────────────────────────
    function render() {
        if (!state.token) {
            renderLogin();
            return;
        }
        switch (state.currentView) {
            case 'overview': renderDashboard(renderOverview); break;
            case 'profiles': renderDashboard(renderProfiles); break;
            case 'safety': renderDashboard(renderSafety); break;
            case 'incidents': renderDashboard(renderIncidents); break;
            case 'analytics': renderDashboard(renderAnalytics); break;
            default: renderDashboard(renderOverview);
        }
    }

    // ── Login ────────────────────────────────────────────────────
    function renderLogin() {
        // Build login form using safe DOM methods
        app.textContent = '';

        var page = document.createElement('div');
        page.className = 'login-page';

        var card = document.createElement('div');
        card.className = 'login-card';

        var logo = document.createElement('div');
        logo.className = 'login-logo';
        var logoIcon = document.createElement('span');
        logoIcon.className = 'logo-icon';
        logoIcon.textContent = '\u{1F33B}';
        var logoH1 = document.createElement('h1');
        logoH1.textContent = 'snflwr.ai';
        var logoP = document.createElement('p');
        logoP.textContent = 'Parent Dashboard';
        logo.appendChild(logoIcon);
        logo.appendChild(logoH1);
        logo.appendChild(logoP);

        var errBox = document.createElement('div');
        errBox.id = 'login-error';

        var form = document.createElement('form');
        form.id = 'login-form';

        var emailGroup = document.createElement('div');
        emailGroup.className = 'form-group';
        var emailLabel = document.createElement('label');
        emailLabel.setAttribute('for', 'email');
        emailLabel.textContent = 'Email';
        var emailInput = document.createElement('input');
        emailInput.type = 'email';
        emailInput.id = 'email';
        emailInput.required = true;
        emailInput.autocomplete = 'email';
        emailGroup.appendChild(emailLabel);
        emailGroup.appendChild(emailInput);

        var passGroup = document.createElement('div');
        passGroup.className = 'form-group';
        var passLabel = document.createElement('label');
        passLabel.setAttribute('for', 'password');
        passLabel.textContent = 'Password';
        var passInput = document.createElement('input');
        passInput.type = 'password';
        passInput.id = 'password';
        passInput.required = true;
        passInput.autocomplete = 'current-password';
        passGroup.appendChild(passLabel);
        passGroup.appendChild(passInput);

        var btn = document.createElement('button');
        btn.type = 'submit';
        btn.className = 'btn btn-primary btn-full';
        btn.id = 'login-btn';
        btn.textContent = 'Sign In';

        form.appendChild(emailGroup);
        form.appendChild(passGroup);
        form.appendChild(btn);

        card.appendChild(logo);
        card.appendChild(errBox);
        card.appendChild(form);
        page.appendChild(card);
        app.appendChild(page);

        form.addEventListener('submit', handleLogin);
    }

    function handleLogin(e) {
        e.preventDefault();
        var email = document.getElementById('email').value.trim();
        var password = document.getElementById('password').value;
        var btn = document.getElementById('login-btn');
        var errEl = document.getElementById('login-error');
        errEl.textContent = '';
        btn.disabled = true;
        btn.textContent = 'Signing in...';

        fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: email, password: password })
        })
        .then(function (resp) {
            if (!resp.ok) {
                return resp.json().then(function (d) {
                    throw new Error(d.detail || 'Login failed');
                });
            }
            return resp.json();
        })
        .then(function (data) {
            state.token = data.token;
            state.parentId = data.session.parent_id;
            state.email = email;
            sessionStorage.setItem('sf_token', data.token);
            sessionStorage.setItem('sf_parent_id', data.session.parent_id);
            sessionStorage.setItem('sf_email', email);
            state.currentView = 'overview';
            render();
        })
        .catch(function (err) {
            var errMsg = document.createElement('div');
            errMsg.className = 'msg-error';
            errMsg.textContent = err.message;
            errEl.textContent = '';
            errEl.appendChild(errMsg);
            btn.disabled = false;
            btn.textContent = 'Sign In';
        });
    }

    function logout() {
        if (state.token) {
            apiRequest('POST', '/api/auth/logout').catch(function () {});
        }
        state.token = null;
        state.parentId = null;
        state.email = '';
        state.profiles = [];
        state.alerts = [];
        sessionStorage.removeItem('sf_token');
        sessionStorage.removeItem('sf_parent_id');
        sessionStorage.removeItem('sf_email');
        render();
    }

    // ── Dashboard layout ─────────────────────────────────────────
    // Uses a template string approach where ALL dynamic data goes
    // through escHtml(). Static markup is safe literal HTML.

    function setMainContent(html) {
        // Safe: html is assembled from escaped dynamic data + literal markup
        var main = document.getElementById('main-content');
        if (main) { main.innerHTML = html; } // eslint-disable-line
    }

    function renderDashboard(contentFn) {
        var alertBadge = state.alerts.length > 0
            ? '<span class="nav-badge">' + escHtml(state.alerts.length) + '</span>'
            : '';

        // Static layout skeleton — only state.email is dynamic (escaped)
        app.innerHTML = [ // eslint-disable-line
            '<div class="layout">',
            '  <aside class="sidebar">',
            '    <div class="sidebar-header">',
            '      <div class="brand">',
            '        <span class="brand-icon">\u{1F33B}</span>',
            '        <span class="brand-name">snflwr.ai</span>',
            '      </div>',
            '      <div class="brand-sub">Parent Dashboard</div>',
            '    </div>',
            '    <nav class="sidebar-nav">',
            navBtn('overview', '\u{1F3E0}', 'Overview', ''),
            navBtn('profiles', '\u{1F464}', 'Profiles', ''),
            navBtn('safety', '\u{1F6E1}\uFE0F', 'Safety', alertBadge),
            navBtn('analytics', '\u{1F4CA}', 'Analytics', ''),
            '    </nav>',
            '    <div class="sidebar-footer">',
            '      <div class="user-info">' + escHtml(state.email) + '</div>',
            '      <button class="btn-logout" id="logout-btn">Sign Out</button>',
            '    </div>',
            '  </aside>',
            '  <main class="main" id="main-content">',
            '    <div class="loading"><span class="spinner"></span> Loading...</div>',
            '  </main>',
            '</div>'
        ].join('\n');

        // Nav click handlers
        document.querySelectorAll('.nav-item[data-view]').forEach(function (el) {
            el.addEventListener('click', function () {
                navigate(el.getAttribute('data-view'));
            });
        });
        document.getElementById('logout-btn').addEventListener('click', logout);

        // Load data then render content
        loadDashboardData().then(function () {
            // Update alert badge after data load
            var safetyNav = document.querySelector('.nav-item[data-view="safety"]');
            if (safetyNav && state.alerts.length > 0) {
                var existing = safetyNav.querySelector('.nav-badge');
                if (!existing) {
                    var badge = document.createElement('span');
                    badge.className = 'nav-badge';
                    badge.textContent = state.alerts.length;
                    safetyNav.appendChild(badge);
                }
            }
            contentFn();
        });
    }

    function navBtn(view, icon, label, extra) {
        return '<button class="nav-item' + (state.currentView === view ? ' active' : '') +
            '" data-view="' + view + '">' +
            '<span class="nav-icon">' + icon + '</span>' +
            '<span class="nav-text">' + escHtml(label) + '</span>' +
            extra + '</button>';
    }

    function loadDashboardData() {
        var p1 = apiRequest('GET', '/api/profiles/parent/' + encodeURIComponent(state.parentId))
            .then(function (r) { return r.json(); })
            .then(function (d) { state.profiles = d.profiles || []; })
            .catch(function () { state.profiles = []; });

        var p2 = apiRequest('GET', '/api/safety/alerts/' + encodeURIComponent(state.parentId))
            .then(function (r) { return r.json(); })
            .then(function (d) { state.alerts = d.alerts || []; })
            .catch(function () { state.alerts = []; });

        return Promise.all([p1, p2]);
    }

    // ── Overview ─────────────────────────────────────────────────
    function renderOverview() {
        var totalSessions = 0;
        var totalQuestions = 0;
        state.profiles.forEach(function (p) {
            totalSessions += p.total_sessions || 0;
            totalQuestions += p.total_questions || 0;
        });

        var parts = [
            '<div class="page-header">',
            '  <h2>Welcome Back</h2>',
            '  <p>Here is an overview of your children\'s activity</p>',
            '</div>',
            '<div class="stat-row">',
            statCard(state.profiles.length, 'Children'),
            statCard(totalSessions, 'Total Sessions'),
            statCard(totalQuestions, 'Total Questions'),
            statCard(state.alerts.length, 'Pending Alerts'),
            '</div>'
        ];

        if (state.profiles.length === 0) {
            parts.push('<div class="empty-state"><div class="empty-icon">\u{1F476}</div>',
                '<p>No child profiles yet. Go to Profiles to add one.</p></div>');
        } else {
            parts.push('<div class="card-grid">');
            state.profiles.forEach(function (p) { parts.push(profileCardHtml(p, true)); });
            parts.push('</div>');
        }

        setMainContent(parts.join('\n'));
        attachProfileCardListeners();
    }

    function statCard(value, label) {
        return '<div class="stat-card"><div class="stat-value">' + escHtml(value) +
            '</div><div class="stat-label">' + escHtml(label) + '</div></div>';
    }

    // ── Profiles ─────────────────────────────────────────────────
    function renderProfiles() {
        var parts = [
            '<div class="page-header">',
            '  <h2>Child Profiles</h2>',
            '  <p>Manage your children\'s profiles and settings</p>',
            '  <div class="header-actions">',
            '    <button class="btn btn-primary" id="add-child-btn">+ Add Child</button>',
            '  </div>',
            '</div>'
        ];

        if (state.profiles.length === 0) {
            parts.push('<div class="empty-state"><div class="empty-icon">\u{1F476}</div>',
                '<p>No child profiles yet. Click "Add Child" to create one.</p></div>');
        } else {
            parts.push('<div class="card-grid">');
            state.profiles.forEach(function (p) { parts.push(profileCardHtml(p, false)); });
            parts.push('</div>');
        }

        setMainContent(parts.join('\n'));
        document.getElementById('add-child-btn').addEventListener('click', showCreateProfileModal);
        attachProfileCardListeners();
    }

    function profileCardHtml(p, compact) {
        var initial = escHtml((p.name || '?')[0].toUpperCase());
        var statusBadge = p.is_active
            ? '<span class="badge badge-active">Active</span>'
            : '<span class="badge badge-inactive">Inactive</span>';
        var lastActive = p.last_active ? timeAgo(p.last_active) : 'Never';
        var pid = escAttr(p.profile_id);
        var pname = escAttr(p.name);

        var parts = [
            '<div class="profile-card' + (p.is_active ? '' : ' inactive') + '">',
            '  <div class="profile-top">',
            '    <div class="profile-avatar">' + initial + '</div>',
            '    <div>',
            '      <div class="profile-name">' + escHtml(p.name) + ' ' + statusBadge + '</div>',
            '      <div class="profile-meta">Age ' + escHtml(p.age) + ' &middot; Grade ' + escHtml(p.grade || '?') + '</div>',
            '    </div>',
            '  </div>',
            '  <div class="profile-stats">',
            '    <div class="profile-stat"><div class="value">' + escHtml(p.total_sessions || 0) + '</div><div class="label">Sessions</div></div>',
            '    <div class="profile-stat"><div class="value">' + escHtml(p.total_questions || 0) + '</div><div class="label">Questions</div></div>',
            '  </div>',
            '  <div style="font-size:0.75rem;color:#64748B;margin-bottom:0.75rem;">Last active: ' + escHtml(lastActive) + '</div>',
            '  <div class="profile-actions">',
            '    <button class="btn btn-sm btn-primary edit-profile-btn" data-id="' + pid + '">Edit</button>'
        ];

        if (!compact) {
            if (p.is_active) {
                parts.push('<button class="btn btn-sm btn-danger deactivate-btn" data-id="' + pid + '">Deactivate</button>');
            }
            parts.push('<button class="btn btn-sm btn-outline export-btn" data-id="' + pid + '" data-name="' + pname + '">Export Data</button>');
        }
        parts.push('<button class="btn btn-sm btn-outline incidents-btn" data-id="' + pid + '" data-name="' + pname + '">Incidents</button>');
        parts.push('  </div>', '</div>');
        return parts.join('\n');
    }

    function attachProfileCardListeners() {
        document.querySelectorAll('.edit-profile-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                var profile = findProfile(el.getAttribute('data-id'));
                if (profile) { showEditProfileModal(profile); }
            });
        });
        document.querySelectorAll('.deactivate-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                var id = el.getAttribute('data-id');
                if (confirm('Are you sure you want to deactivate this profile?')) {
                    apiRequest('DELETE', '/api/profiles/' + encodeURIComponent(id))
                        .then(function (r) {
                            if (r.ok) { navigate(state.currentView); }
                            else { return r.json().then(function (d) { alert(d.detail || 'Failed'); }); }
                        });
                }
            });
        });
        document.querySelectorAll('.export-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                exportProfile(el.getAttribute('data-id'), el.getAttribute('data-name'));
            });
        });
        document.querySelectorAll('.incidents-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                navigate('incidents', { profileId: el.getAttribute('data-id') });
            });
        });
    }

    // ── Edit Profile Modal ───────────────────────────────────────
    function showEditProfileModal(profile) {
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';

        var modal = document.createElement('div');
        modal.className = 'modal';

        // Header
        var header = document.createElement('div');
        header.className = 'modal-header';
        var h3 = document.createElement('h3');
        h3.textContent = 'Edit Profile: ' + profile.name;
        var closeBtn = document.createElement('button');
        closeBtn.className = 'btn-icon modal-close';
        closeBtn.textContent = '\u00D7';
        header.appendChild(h3);
        header.appendChild(closeBtn);

        // Body
        var body = document.createElement('div');
        body.className = 'modal-body';

        var errBox = document.createElement('div');
        errBox.id = 'modal-error';
        body.appendChild(errBox);

        body.appendChild(formGroup('Name', 'text', 'edit-name', profile.name));
        var row = document.createElement('div');
        row.className = 'form-row';
        row.appendChild(formGroup('Age', 'number', 'edit-age', profile.age, { min: '3', max: '25' }));
        row.appendChild(gradeSelectGroup('Grade', 'edit-grade', profile.grade));
        body.appendChild(row);
        body.appendChild(formGroup('Daily Time Limit (minutes)', 'number', 'edit-time-limit',
            profile.daily_time_limit_minutes || 120, { min: '0', max: '1440' }));
        var hint = document.createElement('div');
        hint.className = 'hint';
        hint.textContent = '0 = unlimited, max 1440 (24 hours)';
        body.lastChild.appendChild(hint);

        // Footer
        var footer = document.createElement('div');
        footer.className = 'modal-footer';
        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn btn-outline';
        cancelBtn.textContent = 'Cancel';
        var saveBtn = document.createElement('button');
        saveBtn.className = 'btn btn-primary';
        saveBtn.textContent = 'Save Changes';
        footer.appendChild(cancelBtn);
        footer.appendChild(saveBtn);

        modal.appendChild(header);
        modal.appendChild(body);
        modal.appendChild(footer);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        function close() { overlay.remove(); }
        closeBtn.addEventListener('click', close);
        cancelBtn.addEventListener('click', close);
        overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });

        saveBtn.addEventListener('click', function () {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
            errBox.textContent = '';

            var updates = {
                name: document.getElementById('edit-name').value.trim(),
                age: parseInt(document.getElementById('edit-age').value, 10),
                grade_level: document.getElementById('edit-grade').value
            };
            var tl = parseInt(document.getElementById('edit-time-limit').value, 10);
            if (!isNaN(tl)) { updates.daily_time_limit_minutes = tl; }

            apiRequest('PATCH', '/api/profiles/' + encodeURIComponent(profile.profile_id), updates)
                .then(function (r) {
                    if (r.ok) { close(); navigate(state.currentView); }
                    else {
                        return r.json().then(function (d) {
                            showFormError(errBox, d.detail || 'Update failed');
                            saveBtn.disabled = false;
                            saveBtn.textContent = 'Save Changes';
                        });
                    }
                })
                .catch(function (err) {
                    showFormError(errBox, err.message);
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save Changes';
                });
        });
    }

    // ── Create Profile Modal ─────────────────────────────────────
    function showCreateProfileModal() {
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';

        var modal = document.createElement('div');
        modal.className = 'modal';

        var header = document.createElement('div');
        header.className = 'modal-header';
        var h3 = document.createElement('h3');
        h3.textContent = 'Add Child Profile';
        var closeBtn = document.createElement('button');
        closeBtn.className = 'btn-icon';
        closeBtn.textContent = '\u00D7';
        header.appendChild(h3);
        header.appendChild(closeBtn);

        var body = document.createElement('div');
        body.className = 'modal-body';
        var errBox = document.createElement('div');
        errBox.id = 'modal-error';
        body.appendChild(errBox);

        body.appendChild(formGroup('Child\'s Name', 'text', 'new-name', ''));
        var row = document.createElement('div');
        row.className = 'form-row';
        row.appendChild(formGroup('Age', 'number', 'new-age', '', { min: '3', max: '25' }));
        row.appendChild(gradeSelectGroup('Grade', 'new-grade', ''));
        body.appendChild(row);

        var consentGroup = document.createElement('div');
        consentGroup.className = 'form-group checkbox-group';
        var consentInput = document.createElement('input');
        consentInput.type = 'checkbox';
        consentInput.id = 'new-consent';
        var consentLabel = document.createElement('label');
        consentLabel.setAttribute('for', 'new-consent');
        consentLabel.textContent = 'I verify parental consent for this child (required for COPPA compliance)';
        consentGroup.appendChild(consentInput);
        consentGroup.appendChild(consentLabel);
        body.appendChild(consentGroup);

        var footer = document.createElement('div');
        footer.className = 'modal-footer';
        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn btn-outline';
        cancelBtn.textContent = 'Cancel';
        var createBtn = document.createElement('button');
        createBtn.className = 'btn btn-primary';
        createBtn.textContent = 'Create Profile';
        footer.appendChild(cancelBtn);
        footer.appendChild(createBtn);

        modal.appendChild(header);
        modal.appendChild(body);
        modal.appendChild(footer);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        function close() { overlay.remove(); }
        closeBtn.addEventListener('click', close);
        cancelBtn.addEventListener('click', close);
        overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });

        createBtn.addEventListener('click', function () {
            errBox.textContent = '';
            var name = document.getElementById('new-name').value.trim();
            var age = parseInt(document.getElementById('new-age').value, 10);
            var grade = document.getElementById('new-grade').value;
            var consent = document.getElementById('new-consent').checked;

            if (!name) { showFormError(errBox, 'Name is required'); return; }
            if (isNaN(age) || age < 3 || age > 25) { showFormError(errBox, 'Age must be between 3 and 25'); return; }
            if (!grade) { showFormError(errBox, 'Grade is required'); return; }
            if (!consent) { showFormError(errBox, 'Parental consent is required for COPPA compliance'); return; }

            createBtn.disabled = true;
            createBtn.textContent = 'Creating...';

            apiRequest('POST', '/api/profiles/', {
                parent_id: state.parentId,
                name: name,
                age: age,
                grade_level: grade,
                model_role: 'student',
                parental_consent_verified: consent
            })
            .then(function (r) {
                if (r.ok) { close(); navigate('profiles'); }
                else {
                    return r.json().then(function (d) {
                        showFormError(errBox, d.detail || 'Create failed');
                        createBtn.disabled = false;
                        createBtn.textContent = 'Create Profile';
                    });
                }
            })
            .catch(function (err) {
                showFormError(errBox, err.message);
                createBtn.disabled = false;
                createBtn.textContent = 'Create Profile';
            });
        });
    }

    // ── Safety Alerts ────────────────────────────────────────────
    function renderSafety() {
        var parts = [
            '<div class="page-header">',
            '  <h2>Safety Alerts</h2>',
            '  <p>Review and acknowledge safety alerts for your children</p>',
            '</div>'
        ];

        if (state.alerts.length === 0) {
            parts.push('<div class="empty-state"><div class="empty-icon">\u2705</div>',
                '<p>No pending safety alerts. Everything looks good!</p></div>');
        } else {
            state.alerts.forEach(function (al) {
                var sevClass = (al.severity === 'high' || al.severity === 'critical') ? ' severity-high' : '';
                var pname = findProfileName(al.profile_id);
                var alertId = escAttr(al.alert_id || al.id || '');
                parts.push(
                    '<div class="alert-card' + sevClass + '">',
                    '  <div class="alert-content">',
                    '    <div class="alert-type">' + escHtml(al.alert_type || al.incident_type || 'Alert') +
                    '      <span class="badge badge-severity-' + escAttr(al.severity || 'medium') + '">' + escHtml(al.severity || 'medium') + '</span>',
                    '    </div>',
                    '    <div class="alert-detail"><strong>' + escHtml(pname) + '</strong>: ' + escHtml(al.message || al.content_snippet || 'No details') + '</div>',
                    '    <div class="alert-time">' + escHtml(formatTime(al.created_at || al.timestamp)) + '</div>',
                    '  </div>',
                    '  <button class="btn btn-sm btn-outline ack-btn" data-id="' + alertId + '">Acknowledge</button>',
                    '</div>'
                );
            });
        }

        // Per-child incident links
        if (state.profiles.length > 0) {
            parts.push('<div class="card" style="margin-top:1.5rem;">',
                '<div class="card-header"><h3>View Incidents by Child</h3></div>',
                '<div style="display:flex;flex-wrap:wrap;gap:0.5rem;padding:0 1.25rem 1.25rem;">');
            state.profiles.forEach(function (p) {
                parts.push('<button class="btn btn-outline incidents-btn" data-id="' + escAttr(p.profile_id) +
                    '" data-name="' + escAttr(p.name) + '">' + escHtml(p.name) + '</button>');
            });
            parts.push('</div></div>');
        }

        setMainContent(parts.join('\n'));

        document.querySelectorAll('.ack-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                var aid = el.getAttribute('data-id');
                el.disabled = true;
                el.textContent = '...';
                apiRequest('POST', '/api/safety/alerts/' + encodeURIComponent(aid) + '/acknowledge')
                    .then(function (r) {
                        if (r.ok) {
                            state.alerts = state.alerts.filter(function (a) { return (a.alert_id || a.id) !== aid; });
                            navigate('safety');
                        } else { el.disabled = false; el.textContent = 'Acknowledge'; }
                    });
            });
        });

        document.querySelectorAll('.incidents-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                navigate('incidents', { profileId: el.getAttribute('data-id') });
            });
        });
    }

    // ── Incidents ────────────────────────────────────────────────
    function renderIncidents() {
        var profileId = state.selectedProfileId;
        var profile = findProfile(profileId);
        var profileName = profile ? profile.name : 'Unknown';

        setMainContent([
            '<div class="page-header">',
            '  <h2>Safety Incidents: ' + escHtml(profileName) + '</h2>',
            '  <p>History of flagged content for this child</p>',
            '  <div class="header-actions">',
            '    <button class="btn btn-outline" id="back-safety">\u2190 Back to Safety</button>',
            '  </div>',
            '</div>',
            '<div class="loading"><span class="spinner"></span> Loading incidents...</div>'
        ].join('\n'));

        document.getElementById('back-safety').addEventListener('click', function () { navigate('safety'); });

        apiRequest('GET', '/api/safety/incidents/' + encodeURIComponent(profileId) + '?days=30')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var incidents = data.incidents || [];
                var parts = [
                    '<div class="page-header">',
                    '  <h2>Safety Incidents: ' + escHtml(profileName) + '</h2>',
                    '  <p>' + escHtml(incidents.length) + ' incident(s) found</p>',
                    '  <div class="header-actions">',
                    '    <button class="btn btn-outline" id="back-safety2">\u2190 Back to Safety</button>',
                    '  </div>',
                    '</div>'
                ];

                if (incidents.length === 0) {
                    parts.push('<div class="empty-state"><div class="empty-icon">\u2705</div>',
                        '<p>No safety incidents recorded for this child.</p></div>');
                } else {
                    parts.push('<div class="table-wrap"><table>',
                        '<thead><tr><th>Time</th><th>Type</th><th>Severity</th><th>Content</th></tr></thead><tbody>');
                    incidents.forEach(function (inc) {
                        parts.push(
                            '<tr>',
                            '<td>' + escHtml(formatTime(inc.created_at || inc.timestamp)) + '</td>',
                            '<td>' + escHtml(inc.incident_type || inc.type || '\u2014') + '</td>',
                            '<td><span class="badge badge-severity-' + escAttr(inc.severity || 'medium') + '">' + escHtml(inc.severity || '\u2014') + '</span></td>',
                            '<td>' + escHtml(inc.content_snippet || inc.content || '\u2014') + '</td>',
                            '</tr>'
                        );
                    });
                    parts.push('</tbody></table></div>');
                }

                setMainContent(parts.join('\n'));
                document.getElementById('back-safety2').addEventListener('click', function () { navigate('safety'); });
            })
            .catch(function (err) {
                setMainContent('<div class="msg-error">Failed to load incidents: ' + escHtml(err.message) + '</div>');
            });
    }

    // ── Analytics ────────────────────────────────────────────────
    function renderAnalytics() {
        if (state.profiles.length === 0) {
            setMainContent([
                '<div class="page-header"><h2>Analytics</h2></div>',
                '<div class="empty-state"><div class="empty-icon">\u{1F4CA}</div>',
                '<p>No child profiles to show analytics for.</p></div>'
            ].join('\n'));
            return;
        }

        var selectedId = state.selectedProfileId || state.profiles[0].profile_id;
        var optionsHtml = '';
        state.profiles.forEach(function (p) {
            optionsHtml += '<option value="' + escAttr(p.profile_id) + '"' +
                (p.profile_id === selectedId ? ' selected' : '') + '>' + escHtml(p.name) + '</option>';
        });

        setMainContent([
            '<div class="page-header">',
            '  <h2>Analytics</h2>',
            '  <p>Usage statistics and session activity</p>',
            '</div>',
            '<div class="profile-selector">',
            '  <label>Child:</label>',
            '  <select id="analytics-profile">' + optionsHtml + '</select>',
            '</div>',
            '<div class="day-selector">',
            '  <button class="active" data-days="7">7 days</button>',
            '  <button data-days="30">30 days</button>',
            '  <button data-days="90">90 days</button>',
            '</div>',
            '<div id="analytics-content"><div class="loading"><span class="spinner"></span> Loading...</div></div>'
        ].join('\n'));

        document.getElementById('analytics-profile').addEventListener('change', function () {
            state.selectedProfileId = this.value;
            loadAnalytics(this.value, 7);
        });

        document.querySelectorAll('.day-selector button').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('.day-selector button').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                loadAnalytics(document.getElementById('analytics-profile').value, parseInt(btn.getAttribute('data-days'), 10));
            });
        });

        loadAnalytics(selectedId, 7);
    }

    function loadAnalytics(profileId, days) {
        var container = document.getElementById('analytics-content');
        if (!container) return;
        container.innerHTML = '<div class="loading"><span class="spinner"></span> Loading...</div>'; // eslint-disable-line

        var ep = encodeURIComponent(profileId);
        var p1 = apiRequest('GET', '/api/analytics/usage/' + ep + '?days=' + days)
            .then(function (r) { return r.json(); }).catch(function () { return {}; });

        var p2 = apiRequest('GET', '/api/analytics/activity/' + ep + '?limit=20')
            .then(function (r) { return r.json(); }).catch(function () { return { sessions: [] }; });

        Promise.all([p1, p2]).then(function (results) {
            var usage = results[0];
            var activity = results[1];
            var sessions = activity.sessions || [];

            var parts = [
                '<div class="stat-row">',
                statCard(usage.total_sessions || usage.session_count || 0, 'Sessions (' + escHtml(days) + 'd)'),
                statCard(usage.total_messages || usage.message_count || 0, 'Messages'),
                statCard(usage.total_minutes || 0, 'Minutes'),
                '</div>'
            ];

            if (sessions.length > 0) {
                parts.push('<div class="card"><div class="card-header"><h3>Recent Sessions</h3></div>',
                    '<div class="table-wrap"><table>',
                    '<thead><tr><th>Started</th><th>Duration</th><th>Messages</th><th>Status</th></tr></thead><tbody>');
                sessions.forEach(function (s) {
                    parts.push(
                        '<tr>',
                        '<td>' + escHtml(formatTime(s.started_at || s.created_at)) + '</td>',
                        '<td>' + escHtml(s.duration_minutes ? s.duration_minutes + ' min' : '\u2014') + '</td>',
                        '<td>' + escHtml(s.message_count || s.total_messages || '\u2014') + '</td>',
                        '<td>' + escHtml(s.status || s.session_status || '\u2014') + '</td>',
                        '</tr>'
                    );
                });
                parts.push('</tbody></table></div></div>');
            } else {
                parts.push('<div class="msg-info">No session activity in this period.</div>');
            }

            if (container) { container.innerHTML = parts.join('\n'); } // eslint-disable-line
        });
    }

    // ── COPPA Data Export ────────────────────────────────────────
    function exportProfile(profileId, name) {
        apiRequest('GET', '/api/profiles/' + encodeURIComponent(profileId) + '/export')
            .then(function (r) {
                if (!r.ok) { throw new Error('Export failed'); }
                return r.blob();
            })
            .then(function (blob) {
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = 'child_data_' + (name || 'export') + '.json';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            })
            .catch(function (err) {
                alert('Export failed: ' + err.message);
            });
    }

    // ── Form helpers (safe DOM construction) ─────────────────────
    function formGroup(labelText, type, id, value, attrs) {
        var group = document.createElement('div');
        group.className = 'form-group';
        var label = document.createElement('label');
        label.setAttribute('for', id);
        label.textContent = labelText;
        var input = document.createElement('input');
        input.type = type;
        input.id = id;
        if (value !== '' && value !== null && value !== undefined) { input.value = String(value); }
        if (attrs) {
            Object.keys(attrs).forEach(function (k) { input.setAttribute(k, attrs[k]); });
        }
        group.appendChild(label);
        group.appendChild(input);
        return group;
    }

    function gradeSelectGroup(labelText, id, selected) {
        var group = document.createElement('div');
        group.className = 'form-group';
        var label = document.createElement('label');
        label.setAttribute('for', id);
        label.textContent = labelText;
        var select = document.createElement('select');
        select.id = id;

        var grades = [
            { v: '', l: 'Select grade...' },
            { v: 'pre-k', l: 'Pre-K' }, { v: 'kindergarten', l: 'Kindergarten' },
            { v: '1st', l: '1st Grade' }, { v: '2nd', l: '2nd Grade' }, { v: '3rd', l: '3rd Grade' },
            { v: '4th', l: '4th Grade' }, { v: '5th', l: '5th Grade' }, { v: '6th', l: '6th Grade' },
            { v: '7th', l: '7th Grade' }, { v: '8th', l: '8th Grade' }, { v: '9th', l: '9th Grade' },
            { v: '10th', l: '10th Grade' }, { v: '11th', l: '11th Grade' }, { v: '12th', l: '12th Grade' },
            { v: 'college', l: 'College' }
        ];
        var selLower = (selected || '').toLowerCase();
        grades.forEach(function (g) {
            var opt = document.createElement('option');
            opt.value = g.v;
            opt.textContent = g.l;
            if (g.v === selLower) { opt.selected = true; }
            select.appendChild(opt);
        });

        group.appendChild(label);
        group.appendChild(select);
        return group;
    }

    function showFormError(container, message) {
        container.textContent = '';
        var div = document.createElement('div');
        div.className = 'msg-error';
        div.textContent = message;
        container.appendChild(div);
    }

    // ── Utility helpers ──────────────────────────────────────────
    function findProfile(id) {
        for (var i = 0; i < state.profiles.length; i++) {
            if (state.profiles[i].profile_id === id) return state.profiles[i];
        }
        return null;
    }

    function findProfileName(profileId) {
        var p = findProfile(profileId);
        return p ? p.name : 'Unknown';
    }

    function formatTime(iso) {
        if (!iso) return '\u2014';
        try {
            var d = new Date(iso);
            return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) { return String(iso); }
    }

    function timeAgo(iso) {
        if (!iso) return 'Never';
        try {
            var diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
            if (diff < 60) return 'Just now';
            if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
            if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
            return Math.floor(diff / 86400) + 'd ago';
        } catch (e) { return String(iso); }
    }

    // ── Init ─────────────────────────────────────────────────────
    render();

})();
