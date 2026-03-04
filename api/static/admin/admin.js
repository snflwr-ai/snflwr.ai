/* ================================================================
   snflwr.ai — Admin Dashboard SPA
   Vanilla JS, no framework, no build step.

   Security: All dynamic content goes through esc() / escA() which
   use document.createTextNode() for safe HTML entity encoding.
   Static markup is safe literal HTML. innerHTML is only set from
   the combination of escaped values + literal strings — never from
   raw user input.
   ================================================================ */

(function () {
    'use strict';

    /* ── State ───────────────────────────────────────────────── */
    var state = {
        token: sessionStorage.getItem('sf_admin_token') || null,
        adminId: sessionStorage.getItem('sf_admin_id') || null,
        email: sessionStorage.getItem('sf_admin_email') || '',
        view: 'overview',
        stats: {},
        accounts: [],
        profiles: [],
        alerts: [],
        activity: [],
        auditLog: [],
        search: ''
    };

    var app = document.getElementById('app');

    /* ── Sanitization ────────────────────────────────────────── */
    function esc(s) {
        if (s === null || s === undefined) return '';
        var d = document.createElement('div');
        d.appendChild(document.createTextNode(String(s)));
        return d.innerHTML;
    }
    function escA(s) { return esc(s).replace(/"/g, '&quot;'); }

    /* ── Toast notifications ─────────────────────────────────── */
    function toast(msg, type) {
        var c = document.querySelector('.toast-container');
        if (!c) {
            c = document.createElement('div');
            c.className = 'toast-container';
            document.body.appendChild(c);
        }
        var t = document.createElement('div');
        t.className = 'toast toast-' + (type || 'success');
        t.textContent = msg;
        c.appendChild(t);
        setTimeout(function () { t.remove(); }, 4000);
    }

    /* ── API helpers ─────────────────────────────────────────── */
    function csrfToken() {
        var m = document.cookie.match(/csrf_token=([^;]+)/);
        return m ? m[1] : '';
    }

    function api(method, path, body) {
        var h = {
            'Authorization': 'Bearer ' + state.token,
            'Content-Type': 'application/json'
        };
        if (['POST', 'PATCH', 'DELETE', 'PUT'].indexOf(method) !== -1) {
            h['X-CSRF-Token'] = csrfToken();
        }
        var opts = { method: method, headers: h };
        if (body) opts.body = JSON.stringify(body);
        return fetch(path, opts).then(function (r) {
            if (r.status === 401) { logout(); return Promise.reject(new Error('Session expired')); }
            if (r.status === 403) return Promise.reject(new Error('Admin access required'));
            return r;
        });
    }

    /* ── Batch-delete helpers ────────────────────────────────── */
    function getChecked(selector) {
        return Array.from(document.querySelectorAll(selector + ':checked'))
            .map(function (el) { return el.getAttribute('data-id'); });
    }

    function wireCheckboxes(selectAllId, rowSelector, deleteBtnId) {
        var selectAll = document.getElementById(selectAllId);
        if (selectAll) {
            selectAll.addEventListener('change', function () {
                document.querySelectorAll(rowSelector).forEach(function (cb) { cb.checked = selectAll.checked; });
                var btn = document.getElementById(deleteBtnId);
                if (btn) btn.style.display = selectAll.checked && document.querySelectorAll(rowSelector).length ? '' : 'none';
            });
        }
        document.querySelectorAll(rowSelector).forEach(function (cb) {
            cb.addEventListener('change', function () {
                var checked = document.querySelectorAll(rowSelector + ':checked').length;
                var total = document.querySelectorAll(rowSelector).length;
                var btn = document.getElementById(deleteBtnId);
                if (btn) btn.style.display = checked > 0 ? '' : 'none';
                if (selectAll) selectAll.indeterminate = checked > 0 && checked < total;
                if (selectAll) selectAll.checked = checked === total;
            });
        });
    }

    function confirmBatchDelete(label, onConfirm) {
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        var modal = document.createElement('div');
        modal.className = 'modal';
        modal.style.cssText = 'max-width:400px';
        modal.innerHTML = [
            '<div class="modal-header"><h3>Confirm Delete</h3></div>',
            '<div class="modal-body">',
            '<p>Permanently delete <strong>' + esc(label) + '</strong>? This cannot be undone.</p>',
            '</div>',
            '<div class="modal-footer" style="display:flex;gap:8px;justify-content:flex-end;padding:16px">',
            '<button class="btn btn-outline" id="confirm-cancel-btn">Cancel</button>',
            '<button class="btn btn-danger" id="confirm-ok-btn">Delete</button>',
            '</div>'
        ].join('');
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        document.getElementById('confirm-cancel-btn').addEventListener('click', function () { overlay.remove(); });
        document.getElementById('confirm-ok-btn').addEventListener('click', function () { overlay.remove(); onConfirm(); });
    }

    /* ── Navigation ──────────────────────────────────────────── */
    function nav(view) { state.view = view; state.search = ''; render(); }

    /* ── Render dispatcher ───────────────────────────────────── */
    function render() {
        if (!state.token) { renderLogin(); return; }
        renderShell(function () {
            var views = {
                overview: loadOverview,
                users: loadUsers,
                students: loadStudents,
                safety: loadSafety,
                activity: loadActivity,
                audit: loadAudit
            };
            (views[state.view] || loadOverview)();
        });
    }

    /* ── Login ────────────────────────────────────────────────── */
    function renderLogin() {
        app.textContent = '';

        var page = document.createElement('div');
        page.className = 'login-page';

        var card = document.createElement('div');
        card.className = 'login-card';

        var brand = document.createElement('div');
        brand.className = 'login-brand';
        var icon = document.createElement('img');
        icon.className = 'icon';
        icon.src = '/admin/static/icon.png';
        icon.alt = 'snflwr.ai';
        var h1 = document.createElement('h1');
        h1.textContent = 'snflwr.ai';
        var sub = document.createElement('div');
        sub.className = 'subtitle';
        sub.textContent = 'Admin Dashboard';
        brand.appendChild(icon);
        brand.appendChild(h1);
        brand.appendChild(sub);

        var errBox = document.createElement('div');
        errBox.id = 'login-error';

        var form = document.createElement('form');
        form.id = 'login-form';

        var eg = mkInput('Email', 'email', 'login-email', '', { autocomplete: 'email' });
        var pg = mkInput('Password', 'password', 'login-pass', '', { autocomplete: 'current-password' });

        var btn = document.createElement('button');
        btn.type = 'submit';
        btn.className = 'btn btn-primary btn-full';
        btn.id = 'login-btn';
        btn.textContent = 'Sign In';

        form.appendChild(eg);
        form.appendChild(pg);
        form.appendChild(btn);

        card.appendChild(brand);
        card.appendChild(errBox);
        card.appendChild(form);
        page.appendChild(card);
        app.appendChild(page);

        form.addEventListener('submit', handleLogin);
    }

    function handleLogin(e) {
        e.preventDefault();
        var email = document.getElementById('login-email').value.trim();
        var pass = document.getElementById('login-pass').value;
        var btn = document.getElementById('login-btn');
        var err = document.getElementById('login-error');
        err.textContent = '';
        btn.disabled = true;
        btn.textContent = 'Signing in\u2026';

        fetch('/api/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: email, password: pass })
        })
        .then(function (r) {
            if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || 'Login failed'); });
            return r.json();
        })
        .then(function (data) {
            state.token = data.token;
            state.adminId = data.session.parent_id;
            state.email = email;
            sessionStorage.setItem('sf_admin_token', data.token);
            sessionStorage.setItem('sf_admin_id', data.session.parent_id);
            sessionStorage.setItem('sf_admin_email', email);

            state.view = 'overview';
            render();
        })
        .catch(function (ex) {
            showErr(err, ex.message);
            btn.disabled = false;
            btn.textContent = 'Sign In';
        });
    }

    function logout() {
        if (state.token) api('POST', '/api/auth/logout').catch(function () {});
        state.token = null;
        state.adminId = null;
        state.email = '';
        sessionStorage.removeItem('sf_admin_token');
        sessionStorage.removeItem('sf_admin_id');
        sessionStorage.removeItem('sf_admin_email');
        render();
    }

    /* ── Shell (sidebar + main area) ─────────────────────────── */
    function renderShell(contentFn) {
        var alertBadge = state.stats.pending_alerts
            ? '<span class="nav-badge">' + esc(state.stats.pending_alerts) + '</span>'
            : '';

        var initial = (state.email || '?')[0].toUpperCase();

        // Safe: all dynamic values go through esc()/escA().
        // Static markup is literal HTML strings.
        setSafeHtml(app, [
            '<div class="layout">',
            '<aside class="sidebar">',
            '  <div class="sidebar-brand">',
            '    <div class="brand-row">',
            '      <img class="brand-icon" src="/admin/static/icon.png" alt="snflwr.ai">',
            '      <span class="brand-name">snflwr.ai</span>',
            '    </div>',
            '    <div class="brand-badge">Admin Panel</div>',
            '  </div>',
            '  <nav class="sidebar-nav">',
            '    <div class="nav-section">',
            '      <div class="nav-section-label">Main</div>',
            navItem('overview', '\u{1F3E0}', 'Dashboard'),
            navItem('users', '\u{1F465}', 'Parents'),
            navItem('students', '\u{1F393}', 'Students'),
            '    </div>',
            '    <div class="nav-section">',
            '      <div class="nav-section-label">Monitoring</div>',
            navItem('safety', '\u{1F6E1}\uFE0F', 'Safety Alerts', alertBadge),
            navItem('activity', '\u{1F4C8}', 'Activity Log'),
            navItem('audit', '\u{1F512}', 'System Log'),
            '    </div>',
            '  </nav>',
            '  <div class="sidebar-footer">',
            '    <div class="sidebar-user">',
            '      <div class="user-avatar">' + esc(initial) + '</div>',
            '      <span class="user-email">' + esc(state.email) + '</span>',
            '    </div>',
            '    <button class="btn-logout" id="btn-logout">Sign Out</button>',
            '  </div>',
            '</aside>',
            '<main class="main" id="main">',
            '  <div class="loading"><span class="spinner"></span> Loading\u2026</div>',
            '</main>',
            '</div>'
        ].join('\n'));

        // Bind nav
        document.querySelectorAll('.nav-item[data-v]').forEach(function (el) {
            el.addEventListener('click', function () { nav(el.getAttribute('data-v')); });
        });
        document.getElementById('btn-logout').addEventListener('click', logout);

        contentFn();
    }

    function navItem(view, icon, label, extra) {
        return '<button class="nav-item' + (state.view === view ? ' active' : '') +
            '" data-v="' + view + '">' +
            '<span class="nav-icon">' + icon + '</span>' +
            '<span class="nav-text">' + esc(label) + '</span>' +
            (extra || '') + '</button>';
    }

    function mainEl() { return document.getElementById('main'); }

    // Safe innerHTML setter — content is pre-escaped static markup + esc() values
    function setSafeHtml(el, html) {
        if (el) el.innerHTML = html;  // eslint-disable-line -- all dynamic values are escaped via esc()/escA()
    }
    function setMain(html) { setSafeHtml(mainEl(), html); }

    /* ── Overview ─────────────────────────────────────────────── */
    function loadOverview() {
        api('GET', '/api/admin/stats')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                state.stats = d;
                renderOverview(d);
            })
            .catch(function () { setMain('<div class="msg msg-error">Failed to load dashboard data.</div>'); });
    }

    function renderOverview(s) {
        var parts = [
            '<div class="page-header">',
            '  <h2>Dashboard</h2>',
            '  <p class="page-desc">Platform overview and quick actions</p>',
            '</div>',

            '<div class="stat-grid">',
            statCard('\u{1F465}', s.total_parents || 0, 'Parent Accounts', 'amber'),
            statCard('\u{1F393}', s.active_children || 0, 'Active Students', 'emerald'),
            statCard('\u{1F6E1}\uFE0F', s.pending_alerts || 0, 'Pending Alerts', (s.pending_alerts > 0 ? 'red' : 'blue')),
            statCard('\u{1F4AC}', s.recent_sessions || 0, 'Sessions (7 days)', 'violet'),
            statCard('\u26A0\uFE0F', s.total_incidents || 0, 'Total Incidents', 'red'),
            statCard('\u{1F4DA}', s.total_children || 0, 'Total Students', 'blue'),
            '</div>',

            '<div class="card">',
            '  <div class="card-header"><h3>Quick Actions</h3></div>',
            '  <div class="card-body">',
            '    <div class="quick-grid">',
            quickLink('users', '\u{1F465}', 'bg-amber', 'Manage Parents', 'View and edit parent accounts'),
            quickLink('students', '\u{1F393}', 'bg-emerald', 'Manage Students', 'Edit student profiles and settings'),
            quickLink('safety', '\u{1F6E1}\uFE0F', 'bg-red', 'Review Alerts', 'Check safety alerts and incidents'),
            quickLink('activity', '\u{1F4C8}', 'bg-blue', 'Activity Log', 'View recent student sessions'),
            '    </div>',
            '  </div>',
            '</div>'
        ];

        setMain(parts.join('\n'));

        document.querySelectorAll('.quick-link[data-v]').forEach(function (el) {
            el.addEventListener('click', function () { nav(el.getAttribute('data-v')); });
        });
    }

    function statCard(icon, value, label, accent) {
        return '<div class="stat-card accent-' + accent + '">' +
            '<div class="stat-icon">' + icon + '</div>' +
            '<div class="stat-value">' + esc(value) + '</div>' +
            '<div class="stat-label">' + esc(label) + '</div></div>';
    }

    function quickLink(view, icon, bg, title, desc) {
        return '<button class="quick-link" data-v="' + view + '">' +
            '<div class="ql-icon ' + bg + '">' + icon + '</div>' +
            '<div><div class="ql-text">' + esc(title) + '</div>' +
            '<div class="ql-sub">' + esc(desc) + '</div></div></button>';
    }

    /* ── Users (Parent Accounts) ─────────────────────────────── */
    function loadUsers() {
        setMain('<div class="loading"><span class="spinner"></span> Loading parent accounts\u2026</div>');
        api('GET', '/api/admin/accounts?limit=200')
            .then(function (r) { return r.json(); })
            .then(function (d) { state.accounts = d.accounts || []; renderUsers(); })
            .catch(function () { setMain('<div class="msg msg-error">Failed to load accounts.</div>'); });
    }

    function renderUsers(filter) {
        var search = (filter || state.search || '').toLowerCase();
        var list = state.accounts.filter(function (a) {
            if (!search) return true;
            return (a.name || '').toLowerCase().indexOf(search) !== -1 ||
                   (a.email || '').toLowerCase().indexOf(search) !== -1;
        });

        var parts = [
            '<div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start">',
            '  <div><h2>Parent Accounts</h2>',
            '  <p class="page-desc">' + esc(state.accounts.length) + ' registered parents</p></div>',
            '  <div style="display:flex;gap:8px">',
            '  <button class="btn btn-danger" id="delete-users-btn" style="display:none">Delete Selected</button>',
            '  <button class="btn btn-primary" id="add-parent-btn">+ Add Parent</button>',
            '  </div>',
            '</div>',
            '<div class="card">',
            '  <div class="search-bar">',
            '    <input type="text" id="user-search" placeholder="Search by name or email\u2026" value="' + escA(search) + '">',
            '  </div>',
            '  <div class="card-body compact"><div class="table-wrap"><table>',
            '    <thead><tr>',
            '      <th><input type="checkbox" id="select-all-users"></th>',
            '      <th>Parent</th><th>Email</th><th>Children</th><th>Status</th><th>Joined</th><th></th>',
            '    </tr></thead><tbody>'
        ];

        if (list.length === 0) {
            parts.push('<tr><td colspan="7"><div class="empty-state"><div class="empty-icon">\u{1F50D}</div><p>No parents found.</p></div></td></tr>');
        } else {
            list.forEach(function (a) {
                var statusBadge = a.is_active
                    ? '<span class="badge badge-active">Active</span>'
                    : '<span class="badge badge-inactive">Inactive</span>';
                var emailBadge = a.email_verified
                    ? '<span class="badge badge-verified">Verified</span>'
                    : '<span class="badge badge-unverified">Unverified</span>';

                parts.push(
                    '<tr>',
                    '<td><input type="checkbox" class="row-check-user" data-id="' + escA(a.parent_id) + '"></td>',
                    '<td><div class="cell-name">' + esc(a.name || 'Unnamed') + '</div>' +
                    '<div class="cell-mono">' + esc((a.parent_id || '').substring(0, 12)) + '\u2026</div></td>',
                    '<td>' + esc(a.email) + ' ' + emailBadge + '</td>',
                    '<td>' + esc(a.child_count) + '</td>',
                    '<td>' + statusBadge + '</td>',
                    '<td class="cell-sub">' + esc(fmtDate(a.created_at)) + '</td>',
                    '<td class="td-actions">',
                    '<button class="btn btn-sm btn-outline edit-user-btn" data-id="' + escA(a.parent_id) + '">Edit</button>',
                    '</td>',
                    '</tr>'
                );
            });
        }

        parts.push('</tbody></table></div></div></div>');
        setMain(parts.join('\n'));

        document.getElementById('user-search').addEventListener('input', function () {
            state.search = this.value;
            renderUsers(this.value);
            var el = document.getElementById('user-search');
            if (el) el.focus();
        });

        document.querySelectorAll('.edit-user-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                var acct = findAccount(el.getAttribute('data-id'));
                if (acct) showEditUserModal(acct);
            });
        });

        var addBtn = document.getElementById('add-parent-btn');
        if (addBtn) addBtn.addEventListener('click', showAddParentModal);

        wireCheckboxes('select-all-users', '.row-check-user', 'delete-users-btn');

        var delUsersBtn = document.getElementById('delete-users-btn');
        if (delUsersBtn) {
            delUsersBtn.addEventListener('click', function () {
                var ids = getChecked('.row-check-user');
                if (!ids.length) return;
                confirmBatchDelete(ids.length + ' parent account(s) and all their student profiles', function () {
                    api('DELETE', '/api/admin/accounts', ids)
                        .then(function (r) {
                            if (r.ok) {
                                toast('Deleted ' + ids.length + ' account(s)');
                                state.accounts = state.accounts.filter(function (a) { return ids.indexOf(a.parent_id) === -1; });
                                renderUsers();
                            } else { toast('Delete failed', true); }
                        });
                });
            });
        }
    }

    /* ── Edit User Modal ─────────────────────────────────────── */
    function showEditUserModal(acct) {
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';

        var modal = document.createElement('div');
        modal.className = 'modal';

        var header = document.createElement('div');
        header.className = 'modal-header';
        var h3 = document.createElement('h3');
        h3.textContent = 'Edit Parent: ' + (acct.name || 'Unnamed');
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

        body.appendChild(mkInput('Name', 'text', 'edit-user-name', acct.name || ''));
        body.appendChild(mkInput('Email Address', 'email', 'edit-user-email', acct.email || ''));

        var activeGroup = document.createElement('div');
        activeGroup.className = 'form-group checkbox-group';
        var activeCb = document.createElement('input');
        activeCb.type = 'checkbox';
        activeCb.id = 'edit-user-active';
        activeCb.checked = acct.is_active;
        var activeLbl = document.createElement('label');
        activeLbl.setAttribute('for', 'edit-user-active');
        activeLbl.textContent = 'Account is active';
        activeGroup.appendChild(activeCb);
        activeGroup.appendChild(activeLbl);
        body.appendChild(activeGroup);

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
            saveBtn.textContent = 'Saving\u2026';
            errBox.textContent = '';

            var updates = {};
            var newName = document.getElementById('edit-user-name').value.trim();
            var newEmail = document.getElementById('edit-user-email').value.trim();
            var newActive = document.getElementById('edit-user-active').checked;

            if (newName !== (acct.name || '')) updates.name = newName;
            if (newEmail !== (acct.email || '')) updates.email = newEmail;
            if (newActive !== acct.is_active) updates.is_active = newActive;

            if (Object.keys(updates).length === 0) { close(); return; }

            api('PATCH', '/api/admin/accounts/' + encodeURIComponent(acct.parent_id), updates)
                .then(function (r) {
                    if (r.ok) { close(); toast('Parent account updated'); loadUsers(); }
                    else return r.json().then(function (d) {
                        showErr(errBox, d.detail || 'Update failed');
                        saveBtn.disabled = false;
                        saveBtn.textContent = 'Save Changes';
                    });
                })
                .catch(function (ex) {
                    showErr(errBox, ex.message);
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save Changes';
                });
        });
    }

    /* ── Students (Child Profiles) ───────────────────────────── */
    function loadStudents() {
        setMain('<div class="loading"><span class="spinner"></span> Loading student profiles\u2026</div>');
        api('GET', '/api/admin/profiles/all?limit=200')
            .then(function (r) { return r.json(); })
            .then(function (d) { state.profiles = d.profiles || []; renderStudents(); })
            .catch(function () { setMain('<div class="msg msg-error">Failed to load students.</div>'); });
    }

    function renderStudents(filter) {
        var search = (filter || state.search || '').toLowerCase();
        var list = state.profiles.filter(function (p) {
            if (!search) return true;
            return (p.name || '').toLowerCase().indexOf(search) !== -1 ||
                   (p.parent_name || '').toLowerCase().indexOf(search) !== -1 ||
                   (p.parent_email || '').toLowerCase().indexOf(search) !== -1;
        });

        var parts = [
            '<div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start">',
            '  <div><h2>Student Profiles</h2>',
            '  <p class="page-desc">' + esc(state.profiles.length) + ' total students</p></div>',
            '  <div style="display:flex;gap:8px">',
            '  <button class="btn btn-danger" id="delete-students-btn" style="display:none">Delete Selected</button>',
            '  <button class="btn btn-primary" id="add-student-btn">+ Add Student</button>',
            '  </div>',
            '</div>',
            '<div class="card">',
            '  <div class="search-bar">',
            '    <input type="text" id="student-search" placeholder="Search by student name, parent name, or email\u2026" value="' + escA(search) + '">',
            '  </div>',
            '  <div class="card-body compact"><div class="table-wrap"><table>',
            '    <thead><tr>',
            '      <th><input type="checkbox" id="select-all-students"></th>',
            '      <th>Student</th><th>Age</th><th>Grade</th><th>Parent</th><th>Time Limit</th><th>Sessions</th><th>Status</th><th></th>',
            '    </tr></thead><tbody>'
        ];

        if (list.length === 0) {
            parts.push('<tr><td colspan="9"><div class="empty-state"><div class="empty-icon">\u{1F50D}</div><p>No students found.</p></div></td></tr>');
        } else {
            list.forEach(function (p) {
                var statusBadge = p.is_active
                    ? '<span class="badge badge-active">Active</span>'
                    : '<span class="badge badge-inactive">Inactive</span>';
                var timeLimit = p.daily_time_limit_minutes
                    ? esc(p.daily_time_limit_minutes) + ' min'
                    : 'Unlimited';
                var gradeDisplay = formatGrade(p.grade_level);

                parts.push(
                    '<tr>',
                    '<td><input type="checkbox" class="row-check-student" data-id="' + escA(p.profile_id) + '"></td>',
                    '<td><div class="cell-name">' + esc(p.name) + '</div>' +
                    '<div class="cell-sub">Last active: ' + esc(timeAgo(p.last_active)) + '</div></td>',
                    '<td>' + esc(p.age || '\u2014') + '</td>',
                    '<td>' + esc(gradeDisplay) + '</td>',
                    '<td><div class="cell-name">' + esc(p.parent_name || 'Unknown') + '</div>' +
                    '<div class="cell-sub">' + esc(p.parent_email || '') + '</div></td>',
                    '<td>' + esc(timeLimit) + '</td>',
                    '<td>' + esc(p.total_sessions || 0) + '</td>',
                    '<td>' + statusBadge + '</td>',
                    '<td class="td-actions">',
                    '<button class="btn btn-sm btn-outline edit-student-btn" data-id="' + escA(p.profile_id) + '">Edit</button>',
                    '<button class="btn btn-sm btn-ghost view-incidents-btn" data-id="' + escA(p.profile_id) + '" data-name="' + escA(p.name) + '">\u{1F6E1}\uFE0F</button>',
                    '</td>',
                    '</tr>'
                );
            });
        }

        parts.push('</tbody></table></div></div></div>');
        setMain(parts.join('\n'));

        document.getElementById('student-search').addEventListener('input', function () {
            state.search = this.value;
            renderStudents(this.value);
            var el = document.getElementById('student-search');
            if (el) el.focus();
        });

        document.querySelectorAll('.edit-student-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                var p = findProfile(el.getAttribute('data-id'));
                if (p) showEditStudentModal(p);
            });
        });

        document.querySelectorAll('.view-incidents-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                showIncidentsModal(el.getAttribute('data-id'), el.getAttribute('data-name'));
            });
        });

        var addStudentBtn = document.getElementById('add-student-btn');
        if (addStudentBtn) addStudentBtn.addEventListener('click', showAddStudentModal);

        wireCheckboxes('select-all-students', '.row-check-student', 'delete-students-btn');

        var delStudentsBtn = document.getElementById('delete-students-btn');
        if (delStudentsBtn) {
            delStudentsBtn.addEventListener('click', function () {
                var ids = getChecked('.row-check-student');
                if (!ids.length) return;
                confirmBatchDelete(ids.length + ' student profile(s) and all their data', function () {
                    api('DELETE', '/api/admin/profiles', ids)
                        .then(function (r) {
                            if (r.ok) {
                                toast('Deleted ' + ids.length + ' profile(s)');
                                state.profiles = state.profiles.filter(function (p) { return ids.indexOf(p.profile_id) === -1; });
                                renderStudents();
                            } else { toast('Delete failed', true); }
                        });
                });
            });
        }
    }

    /* ── Edit Student Modal ──────────────────────────────────── */
    function showEditStudentModal(profile) {
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        var modal = document.createElement('div');
        modal.className = 'modal';

        var header = document.createElement('div');
        header.className = 'modal-header';
        var h3 = document.createElement('h3');
        h3.textContent = 'Edit Student: ' + profile.name;
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

        body.appendChild(mkInput('Student Name', 'text', 'edit-st-name', profile.name || ''));

        var row1 = document.createElement('div');
        row1.className = 'form-row';
        row1.appendChild(mkInput('Age', 'number', 'edit-st-age', profile.age || '', { min: '3', max: '25' }));
        row1.appendChild(mkGradeSelect('Grade', 'edit-st-grade', profile.grade_level || ''));
        body.appendChild(row1);

        body.appendChild(mkInput('Daily Time Limit (minutes)', 'number', 'edit-st-limit',
            profile.daily_time_limit_minutes || '', { min: '0', max: '1440', placeholder: '0 = unlimited' }));
        var hint = document.createElement('div');
        hint.className = 'hint';
        hint.textContent = '0 or empty means unlimited. Maximum 1440 minutes (24 hours).';
        body.lastChild.appendChild(hint);

        var activeGroup = document.createElement('div');
        activeGroup.className = 'form-group checkbox-group';
        var activeCb = document.createElement('input');
        activeCb.type = 'checkbox';
        activeCb.id = 'edit-st-active';
        activeCb.checked = profile.is_active;
        var activeLbl = document.createElement('label');
        activeLbl.setAttribute('for', 'edit-st-active');
        activeLbl.textContent = 'Profile is active (student can log in)';
        activeGroup.appendChild(activeCb);
        activeGroup.appendChild(activeLbl);
        body.appendChild(activeGroup);

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
            saveBtn.textContent = 'Saving\u2026';
            errBox.textContent = '';

            var updates = {};
            var n = document.getElementById('edit-st-name').value.trim();
            var a = parseInt(document.getElementById('edit-st-age').value, 10);
            var g = document.getElementById('edit-st-grade').value;
            var tl = document.getElementById('edit-st-limit').value;
            var active = document.getElementById('edit-st-active').checked;

            if (n && n !== profile.name) updates.name = n;
            if (!isNaN(a) && a !== profile.age) updates.age = a;
            if (g && g !== (profile.grade_level || '')) updates.grade_level = g;
            if (tl !== '') {
                var tlNum = parseInt(tl, 10);
                if (!isNaN(tlNum) && tlNum !== profile.daily_time_limit_minutes) {
                    updates.daily_time_limit_minutes = tlNum;
                }
            }
            if (active !== profile.is_active) updates.is_active = active;

            if (Object.keys(updates).length === 0) { close(); return; }

            api('PATCH', '/api/admin/profiles/' + encodeURIComponent(profile.profile_id), updates)
                .then(function (r) {
                    if (r.ok) { close(); toast('Student profile updated'); loadStudents(); }
                    else return r.json().then(function (d) {
                        showErr(errBox, d.detail || 'Update failed');
                        saveBtn.disabled = false;
                        saveBtn.textContent = 'Save Changes';
                    });
                })
                .catch(function (ex) {
                    showErr(errBox, ex.message);
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Save Changes';
                });
        });
    }

    /* ── Safety Alerts ───────────────────────────────────────── */
    function loadSafety() {
        setMain('<div class="loading"><span class="spinner"></span> Loading safety alerts\u2026</div>');
        api('GET', '/api/admin/alerts/all?limit=100')
            .then(function (r) { return r.json(); })
            .then(function (d) { state.alerts = d.alerts || []; renderSafety(); })
            .catch(function () { setMain('<div class="msg msg-error">Failed to load alerts.</div>'); });
    }

    function renderSafety() {
        var parts = [
            '<div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start">',
            '  <div><h2>Safety Alerts</h2>',
            '  <p class="page-desc">Review flagged content and safety incidents across all students</p></div>',
            '  <button class="btn btn-danger" id="delete-alerts-btn" style="display:' + (state.alerts.length ? 'none' : 'none') + '">Delete Selected</button>',
            '</div>'
        ];

        if (state.alerts.length === 0) {
            parts.push(
                '<div class="card"><div class="card-body">',
                '<div class="empty-state"><div class="empty-icon">\u2705</div>',
                '<p>No pending safety alerts. All clear!</p></div>',
                '</div></div>'
            );
        } else {
            parts.push('<div class="card">');
            parts.push('<div class="card-header"><h3>' + esc(state.alerts.length) + ' Pending Alert' + (state.alerts.length !== 1 ? 's' : '') + '</h3></div>');

            state.alerts.forEach(function (al) {
                var sev = al.severity || 'medium';
                parts.push(
                    '<div class="alert-item">',
                    '<div class="alert-severity-mark sev-' + escA(sev) + '"></div>',
                    '<div class="alert-body">',
                    '  <div class="alert-title">' + esc(al.message || al.alert_type || 'Safety Alert') + '</div>',
                    '  <div class="alert-meta">',
                    '    <span><strong>Student:</strong> ' + esc(al.child_name || 'Unknown') + '</span>',
                    '    <span><strong>Parent:</strong> ' + esc(al.parent_name || 'Unknown') + '</span>',
                    '    <span><strong>Severity:</strong> <span class="badge badge-' + escA(sev) + '">' + esc(sev) + '</span></span>',
                    '    <span>' + esc(fmtTime(al.timestamp)) + '</span>',
                    '  </div>'
                );

                if (al.content_snippet) {
                    parts.push('<div class="alert-snippet">' + esc(al.content_snippet) + '</div>');
                }

                parts.push(
                    '</div>',
                    '<div class="alert-actions">',
                    '<input type="checkbox" class="row-check-alert" data-id="' + escA(al.alert_id) + '" style="margin-right:8px">',
                    '<button class="btn btn-sm btn-outline ack-btn" data-id="' + escA(al.alert_id) + '">Acknowledge</button>',
                    '<button class="btn btn-sm btn-ghost view-incidents-btn" data-id="' + escA(al.profile_id) + '" data-name="' + escA(al.child_name) + '">\u{1F4CB} Details</button>',
                    '</div>',
                    '</div>'
                );
            });

            parts.push('</div>');
        }

        setMain(parts.join('\n'));

        document.querySelectorAll('.ack-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                var aid = el.getAttribute('data-id');
                el.disabled = true;
                el.textContent = '\u2026';
                api('POST', '/api/safety/alerts/' + encodeURIComponent(aid) + '/acknowledge')
                    .then(function (r) {
                        if (r.ok) {
                            toast('Alert acknowledged');
                            state.alerts = state.alerts.filter(function (a) { return a.alert_id !== aid; });
                            if (state.stats.pending_alerts > 0) state.stats.pending_alerts--;
                            renderSafety();
                        } else {
                            el.disabled = false;
                            el.textContent = 'Acknowledge';
                        }
                    });
            });
        });

        document.querySelectorAll('.view-incidents-btn').forEach(function (el) {
            el.addEventListener('click', function () {
                showIncidentsModal(el.getAttribute('data-id'), el.getAttribute('data-name'));
            });
        });

        document.querySelectorAll('.row-check-alert').forEach(function (cb) {
            cb.addEventListener('change', function () {
                var anyChecked = document.querySelectorAll('.row-check-alert:checked').length > 0;
                var btn = document.getElementById('delete-alerts-btn');
                if (btn) btn.style.display = anyChecked ? '' : 'none';
            });
        });

        var delAlertsBtn = document.getElementById('delete-alerts-btn');
        if (delAlertsBtn) {
            delAlertsBtn.addEventListener('click', function () {
                var ids = getChecked('.row-check-alert').map(Number);
                if (!ids.length) return;
                confirmBatchDelete(ids.length + ' alert(s)', function () {
                    api('DELETE', '/api/admin/alerts', ids)
                        .then(function (r) {
                            if (r.ok) {
                                toast('Deleted ' + ids.length + ' alert(s)');
                                state.alerts = state.alerts.filter(function (a) { return ids.indexOf(a.alert_id) === -1; });
                                renderSafety();
                            } else { toast('Delete failed', true); }
                        });
                });
            });
        }
    }

    /* ── Incidents Modal ─────────────────────────────────────── */
    function showIncidentsModal(profileId, childName) {
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        var modal = document.createElement('div');
        modal.className = 'modal';
        modal.style.cssText = 'max-width:680px';

        var header = document.createElement('div');
        header.className = 'modal-header';
        var h3 = document.createElement('h3');
        h3.textContent = 'Safety Incidents: ' + (childName || 'Student');
        var closeBtn = document.createElement('button');
        closeBtn.className = 'btn-icon';
        closeBtn.textContent = '\u00D7';
        header.appendChild(h3);
        header.appendChild(closeBtn);

        var body = document.createElement('div');
        body.className = 'modal-body';
        var loadDiv = document.createElement('div');
        loadDiv.className = 'loading';
        var sp = document.createElement('span');
        sp.className = 'spinner';
        loadDiv.appendChild(sp);
        loadDiv.appendChild(document.createTextNode(' Loading\u2026'));
        body.appendChild(loadDiv);

        modal.appendChild(header);
        modal.appendChild(body);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        function close() { overlay.remove(); }
        closeBtn.addEventListener('click', close);
        overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });

        api('GET', '/api/safety/incidents/' + encodeURIComponent(profileId) + '?days=30')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var incidents = data.incidents || [];
                body.textContent = '';

                if (incidents.length === 0) {
                    var empty = document.createElement('div');
                    empty.className = 'empty-state';
                    var emIcon = document.createElement('div');
                    emIcon.className = 'empty-icon';
                    emIcon.textContent = '\u2705';
                    var emP = document.createElement('p');
                    emP.textContent = 'No safety incidents recorded.';
                    empty.appendChild(emIcon);
                    empty.appendChild(emP);
                    body.appendChild(empty);
                    return;
                }

                var table = document.createElement('table');
                var thead = document.createElement('thead');
                var headRow = document.createElement('tr');
                ['Time', 'Type', 'Severity', 'Content'].forEach(function (t) {
                    var th = document.createElement('th');
                    th.textContent = t;
                    headRow.appendChild(th);
                });
                thead.appendChild(headRow);
                table.appendChild(thead);

                var tbody = document.createElement('tbody');
                incidents.forEach(function (inc) {
                    var tr = document.createElement('tr');

                    var tdTime = document.createElement('td');
                    tdTime.className = 'cell-sub';
                    tdTime.textContent = fmtTime(inc.created_at || inc.timestamp);

                    var tdType = document.createElement('td');
                    tdType.textContent = inc.incident_type || inc.type || '\u2014';

                    var tdSev = document.createElement('td');
                    var sevBadge = document.createElement('span');
                    var sev = inc.severity || 'minor';
                    sevBadge.className = 'badge badge-' + sev;
                    sevBadge.textContent = sev;
                    tdSev.appendChild(sevBadge);

                    var tdContent = document.createElement('td');
                    tdContent.className = 'cell-sub';
                    tdContent.textContent = inc.content_snippet || inc.content || '\u2014';

                    tr.appendChild(tdTime);
                    tr.appendChild(tdType);
                    tr.appendChild(tdSev);
                    tr.appendChild(tdContent);
                    tbody.appendChild(tr);
                });
                table.appendChild(tbody);

                var wrap = document.createElement('div');
                wrap.className = 'table-wrap';
                wrap.appendChild(table);
                body.appendChild(wrap);
            })
            .catch(function () {
                body.textContent = '';
                var errDiv = document.createElement('div');
                errDiv.className = 'msg msg-error';
                errDiv.textContent = 'Failed to load incidents.';
                body.appendChild(errDiv);
            });
    }

    /* ── Activity Log ────────────────────────────────────────── */
    function loadActivity() {
        setMain('<div class="loading"><span class="spinner"></span> Loading activity log\u2026</div>');
        api('GET', '/api/admin/activity?limit=100')
            .then(function (r) { return r.json(); })
            .then(function (d) { state.activity = d.sessions || []; renderActivity(); })
            .catch(function () { setMain('<div class="msg msg-error">Failed to load activity.</div>'); });
    }

    function renderActivity() {
        var parts = [
            '<div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start">',
            '  <div><h2>Activity Log</h2>',
            '  <p class="page-desc">Recent student sessions and usage</p></div>',
            '  <button class="btn btn-danger" id="delete-activity-btn" style="display:none">Delete Selected</button>',
            '</div>',
            '<div class="card">',
            '<div class="card-body compact"><div class="table-wrap"><table>',
            '<thead><tr>',
            '<th><input type="checkbox" id="select-all-activity"></th>',
            '<th>Student</th><th>Started</th><th>Duration</th><th>Questions</th><th>Platform</th><th>Status</th>',
            '</tr></thead><tbody>'
        ];

        if (state.activity.length === 0) {
            parts.push('<tr><td colspan="7"><div class="empty-state"><div class="empty-icon">\u{1F4AC}</div><p>No recent sessions.</p></div></td></tr>');
        } else {
            state.activity.forEach(function (s) {
                var statusBadge = s.is_active
                    ? '<span class="badge badge-active">In Progress</span>'
                    : '<span class="badge badge-inactive">Ended</span>';
                var dur = s.duration_minutes ? s.duration_minutes + ' min' : '\u2014';
                parts.push(
                    '<tr>',
                    '<td><input type="checkbox" class="row-check-activity" data-id="' + escA(s.session_id) + '"></td>',
                    '<td class="cell-name">' + esc(s.child_name || 'Unknown') + '</td>',
                    '<td class="cell-sub">' + esc(fmtTime(s.started_at)) + '</td>',
                    '<td>' + esc(dur) + '</td>',
                    '<td>' + esc(s.questions_asked || 0) + '</td>',
                    '<td class="cell-sub">' + esc(s.platform || '\u2014') + '</td>',
                    '<td>' + statusBadge + '</td>',
                    '</tr>'
                );
            });
        }

        parts.push('</tbody></table></div></div></div>');
        setMain(parts.join('\n'));

        wireCheckboxes('select-all-activity', '.row-check-activity', 'delete-activity-btn');

        var delActivityBtn = document.getElementById('delete-activity-btn');
        if (delActivityBtn) {
            delActivityBtn.addEventListener('click', function () {
                var ids = getChecked('.row-check-activity');
                if (!ids.length) return;
                confirmBatchDelete(ids.length + ' session record(s)', function () {
                    api('DELETE', '/api/admin/activity', ids)
                        .then(function (r) {
                            if (r.ok) {
                                toast('Deleted ' + ids.length + ' session(s)');
                                state.activity = state.activity.filter(function (s) { return ids.indexOf(s.session_id) === -1; });
                                renderActivity();
                            } else { toast('Delete failed', true); }
                        });
                });
            });
        }
    }

    /* ── Audit / System Log ──────────────────────────────────── */
    function loadAudit() {
        setMain('<div class="loading"><span class="spinner"></span> Loading system log\u2026</div>');
        api('GET', '/api/admin/audit-log?limit=100')
            .then(function (r) { return r.json(); })
            .then(function (d) { state.auditLog = d.entries || []; renderAudit(); })
            .catch(function () { setMain('<div class="msg msg-error">Failed to load system log.</div>'); });
    }

    function renderAudit() {
        var parts = [
            '<div class="page-header">',
            '  <h2>System Log</h2>',
            '  <p class="page-desc">Audit trail of admin and system actions</p>',
            '</div>',
            '<div class="card">',
            '<div class="card-body compact"><div class="table-wrap"><table>',
            '<thead><tr>',
            '<th>Time</th><th>User</th><th>Role</th><th>Action</th><th>Details</th><th>Result</th>',
            '</tr></thead><tbody>'
        ];

        if (state.auditLog.length === 0) {
            parts.push('<tr><td colspan="6"><div class="empty-state"><div class="empty-icon">\u{1F512}</div><p>No log entries yet.</p></div></td></tr>');
        } else {
            state.auditLog.forEach(function (e) {
                var resultBadge = e.success
                    ? '<span class="badge badge-active">OK</span>'
                    : '<span class="badge badge-critical">Failed</span>';
                parts.push(
                    '<tr>',
                    '<td class="cell-sub">' + esc(fmtTime(e.timestamp)) + '</td>',
                    '<td class="cell-mono">' + esc((e.user_id || '').substring(0, 16)) + '</td>',
                    '<td class="cell-sub">' + esc(e.user_type || '\u2014') + '</td>',
                    '<td class="cell-name">' + esc(e.action || '\u2014') + '</td>',
                    '<td class="cell-sub">' + esc(e.details || '\u2014') + '</td>',
                    '<td>' + resultBadge + '</td>',
                    '</tr>'
                );
            });
        }

        parts.push('</tbody></table></div></div></div>');
        setMain(parts.join('\n'));
    }

    /* ── Form Helpers (safe DOM construction) ────────────────── */
    function mkInput(labelText, type, id, value, attrs) {
        var group = document.createElement('div');
        group.className = 'form-group';
        var label = document.createElement('label');
        label.setAttribute('for', id);
        label.textContent = labelText;
        var input = document.createElement('input');
        input.type = type;
        input.id = id;
        if (value !== '' && value !== null && value !== undefined) input.value = String(value);
        if (attrs) Object.keys(attrs).forEach(function (k) { input.setAttribute(k, attrs[k]); });
        group.appendChild(label);
        group.appendChild(input);
        return group;
    }

    function mkGradeSelect(labelText, id, selected) {
        var group = document.createElement('div');
        group.className = 'form-group';
        var label = document.createElement('label');
        label.setAttribute('for', id);
        label.textContent = labelText;
        var select = document.createElement('select');
        select.id = id;
        var grades = [
            { v: '', l: 'Select grade\u2026' },
            { v: 'pre-k', l: 'Pre-K' }, { v: 'kindergarten', l: 'Kindergarten' },
            { v: '1st', l: '1st Grade' }, { v: '2nd', l: '2nd Grade' }, { v: '3rd', l: '3rd Grade' },
            { v: '4th', l: '4th Grade' }, { v: '5th', l: '5th Grade' }, { v: '6th', l: '6th Grade' },
            { v: '7th', l: '7th Grade' }, { v: '8th', l: '8th Grade' }, { v: '9th', l: '9th Grade' },
            { v: '10th', l: '10th Grade' }, { v: '11th', l: '11th Grade' }, { v: '12th', l: '12th Grade' },
            { v: 'college', l: 'College' }
        ];
        var sel = (selected || '').toLowerCase();
        grades.forEach(function (g) {
            var opt = document.createElement('option');
            opt.value = g.v;
            opt.textContent = g.l;
            if (g.v === sel) opt.selected = true;
            select.appendChild(opt);
        });
        group.appendChild(label);
        group.appendChild(select);
        return group;
    }

    function showErr(container, message) {
        container.textContent = '';
        var div = document.createElement('div');
        div.className = 'msg msg-error';
        div.textContent = message;
        container.appendChild(div);
    }

    /* ── Add Parent Modal ────────────────────────────────────── */
    function showAddParentModal() {
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        var modal = document.createElement('div');
        modal.className = 'modal';

        var header = document.createElement('div');
        header.className = 'modal-header';
        var h3 = document.createElement('h3');
        h3.textContent = 'Add New Parent';
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

        body.appendChild(mkInput('Full Name', 'text', 'add-parent-name', ''));
        body.appendChild(mkInput('Email Address', 'email', 'add-parent-email', ''));
        body.appendChild(mkInput('Password', 'password', 'add-parent-pass', ''));

        var hint = document.createElement('div');
        hint.className = 'hint';
        hint.textContent = 'Parent will log in at the Snflwr Admin Dashboard only.';
        body.appendChild(hint);

        var footer = document.createElement('div');
        footer.className = 'modal-footer';
        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn btn-outline';
        cancelBtn.textContent = 'Cancel';
        var saveBtn = document.createElement('button');
        saveBtn.className = 'btn btn-primary';
        saveBtn.textContent = 'Create Parent';
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
            var name = document.getElementById('add-parent-name').value.trim();
            var email = document.getElementById('add-parent-email').value.trim();
            var pass = document.getElementById('add-parent-pass').value;

            if (!name || !email || !pass) {
                showErr(errBox, 'All fields are required');
                return;
            }
            if (pass.length < 8) {
                showErr(errBox, 'Password must be at least 8 characters');
                return;
            }

            saveBtn.disabled = true;
            saveBtn.textContent = 'Creating\u2026';
            errBox.textContent = '';

            api('POST', '/api/admin/accounts', { name: name, email: email, password: pass })
                .then(function (r) {
                    if (r.ok) { close(); toast('Parent account created'); loadUsers(); }
                    else return r.json().then(function (d) {
                        showErr(errBox, d.detail || 'Creation failed');
                        saveBtn.disabled = false;
                        saveBtn.textContent = 'Create Parent';
                    });
                })
                .catch(function (ex) {
                    showErr(errBox, ex.message);
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Create Parent';
                });
        });
    }

    /* ── Add Student Modal ───────────────────────────────────── */
    function showAddStudentModal() {
        _showAddStudentForm();
    }

    function _showAddStudentForm() {
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        var modal = document.createElement('div');
        modal.className = 'modal';

        var header = document.createElement('div');
        header.className = 'modal-header';
        var h3 = document.createElement('h3');
        h3.textContent = 'Add New Student';
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

        body.appendChild(mkInput('Student Name', 'text', 'add-st-name', ''));

        // Open WebUI login section
        var loginHeader = document.createElement('div');
        loginHeader.className = 'hint';
        loginHeader.style.cssText = 'margin:12px 0 4px;font-weight:600;color:var(--amber-600,#d97706)';
        loginHeader.textContent = 'Open WebUI Login (so student can chat at localhost:3000)';
        body.appendChild(loginHeader);
        body.appendChild(mkInput('Student Email', 'email', 'add-st-email', ''));
        body.appendChild(mkInput('Student Password', 'password', 'add-st-pass', ''));

        // Parent dropdown
        var parentGroup = document.createElement('div');
        parentGroup.className = 'form-group';
        var parentLabel = document.createElement('label');
        parentLabel.setAttribute('for', 'add-st-parent');
        parentLabel.textContent = 'Owner (Admin)';
        var parentInput = document.createElement('input');
        parentInput.id = 'add-st-parent';
        parentInput.type = 'text';
        parentInput.value = state.email || state.adminId || '';
        parentInput.disabled = true;
        parentInput.style.cssText = 'background:var(--gray-50,#f9fafb);color:var(--gray-500,#6b7280)';
        parentGroup.appendChild(parentLabel);
        parentGroup.appendChild(parentInput);
        body.appendChild(parentGroup);

        var row1 = document.createElement('div');
        row1.className = 'form-row';
        row1.appendChild(mkInput('Age', 'number', 'add-st-age', '', { min: '3', max: '18' }));
        row1.appendChild(mkGradeSelect('Grade', 'add-st-grade', ''));
        body.appendChild(row1);

        body.appendChild(mkInput('Daily Time Limit (minutes)', 'number', 'add-st-limit', '120',
            { min: '0', max: '1440' }));
        var hint = document.createElement('div');
        hint.className = 'hint';
        hint.textContent = '0 = unlimited. Default is 120 minutes.';
        body.lastChild.appendChild(hint);

        var footer = document.createElement('div');
        footer.className = 'modal-footer';
        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn btn-outline';
        cancelBtn.textContent = 'Cancel';
        var saveBtn = document.createElement('button');
        saveBtn.className = 'btn btn-primary';
        saveBtn.textContent = 'Create Student';
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
            var name = document.getElementById('add-st-name').value.trim();
            var email = document.getElementById('add-st-email').value.trim();
            var pass = document.getElementById('add-st-pass').value;
            var parentId = state.adminId;
            var age = parseInt(document.getElementById('add-st-age').value, 10);
            var grade = document.getElementById('add-st-grade').value;
            var limit = parseInt(document.getElementById('add-st-limit').value, 10);

            if (!name) { showErr(errBox, 'Student name is required'); return; }
            if (!email) { showErr(errBox, 'Student email is required for Open WebUI login'); return; }
            if (!pass || pass.length < 8) { showErr(errBox, 'Password must be at least 8 characters'); return; }
            if (!parentId) { showErr(errBox, 'Session expired — please sign in again'); return; }
            if (isNaN(age) || age < 3 || age > 18) { showErr(errBox, 'Age must be between 3 and 18'); return; }
            if (!grade) { showErr(errBox, 'Please select a grade'); return; }

            saveBtn.disabled = true;
            saveBtn.textContent = 'Creating\u2026';
            errBox.textContent = '';

            api('POST', '/api/admin/profiles', {
                parent_id: parentId,
                name: name,
                age: age,
                grade_level: grade,
                daily_time_limit_minutes: isNaN(limit) ? 120 : limit,
                email: email,
                password: pass
            })
                .then(function (r) {
                    if (r.ok) { close(); toast('Student profile created'); loadStudents(); }
                    else return r.json().then(function (d) {
                        showErr(errBox, d.detail || 'Creation failed');
                        saveBtn.disabled = false;
                        saveBtn.textContent = 'Create Student';
                    });
                })
                .catch(function (ex) {
                    showErr(errBox, ex.message);
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Create Student';
                });
        });
    }

    /* ── Utility Helpers ─────────────────────────────────────── */
    function findAccount(id) {
        for (var i = 0; i < state.accounts.length; i++) {
            if (state.accounts[i].parent_id === id) return state.accounts[i];
        }
        return null;
    }

    function findProfile(id) {
        for (var i = 0; i < state.profiles.length; i++) {
            if (state.profiles[i].profile_id === id) return state.profiles[i];
        }
        return null;
    }

    function fmtTime(iso) {
        if (!iso) return '\u2014';
        try {
            var d = new Date(iso);
            return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) { return String(iso); }
    }

    function fmtDate(iso) {
        if (!iso) return '\u2014';
        try { return new Date(iso).toLocaleDateString(); }
        catch (e) { return String(iso); }
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

    function formatGrade(g) {
        if (!g) return '\u2014';
        var map = {
            'pre-k': 'Pre-K', 'kindergarten': 'K',
            '1st': '1st', '2nd': '2nd', '3rd': '3rd', '4th': '4th',
            '5th': '5th', '6th': '6th', '7th': '7th', '8th': '8th',
            '9th': '9th', '10th': '10th', '11th': '11th', '12th': '12th',
            'college': 'College'
        };
        return map[g.toLowerCase()] || g;
    }

    /* ── Init ────────────────────────────────────────────────── */
    render();

})();
