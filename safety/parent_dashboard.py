"""
Parent Dashboard API
Simple Flask API for parents/teachers to review safety logs
"""

from functools import wraps
from flask import Flask, jsonify, request, render_template_string
from safety.incident_logger import incident_logger
from datetime import datetime, timezone
import hmac
import json
import os

app = Flask(__name__)

# Authentication - MUST be set in production
ADMIN_PASSWORD = os.getenv('PARENT_DASHBOARD_PASSWORD')
if not ADMIN_PASSWORD:
    raise RuntimeError(
        "CRITICAL SECURITY ERROR: PARENT_DASHBOARD_PASSWORD environment variable must be set.\n"
        "This password protects access to child safety data and COPPA/FERPA sensitive information.\n"
        "Generate a secure password with: python -c 'import secrets; print(secrets.token_hex(32))'"
    )


def require_auth(f):
    """Require HTTP Basic authentication for dashboard routes"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not hmac.compare_digest(auth.password or '', ADMIN_PASSWORD):
            return jsonify({"error": "Authentication required"}), 401, {
                'WWW-Authenticate': 'Basic realm="Parent Dashboard"'
            }
        return f(*args, **kwargs)
    return decorated


# Simple HTML dashboard template
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>snflwr.ai - Parent Dashboard</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1, h2 { color: #333; }
        .card {
            background: white;
            padding: 20px;
            margin: 20px 0;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .stat {
            display: inline-block;
            margin: 10px 20px;
            padding: 15px;
            background: #f0f8ff;
            border-radius: 5px;
        }
        .stat-label { font-size: 12px; color: #666; }
        .stat-value { font-size: 24px; font-weight: bold; color: #2c3e50; }
        .incident {
            border-left: 4px solid #ddd;
            padding: 15px;
            margin: 10px 0;
            background: #fafafa;
        }
        .incident.high { border-left-color: #e74c3c; }
        .incident.medium { border-left-color: #f39c12; }
        .incident.low { border-left-color: #3498db; }
        .timestamp { color: #999; font-size: 12px; }
        .category {
            display: inline-block;
            padding: 3px 8px;
            background: #ecf0f1;
            border-radius: 3px;
            font-size: 12px;
            margin: 5px 5px 5px 0;
        }
        button {
            background: #3498db;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
        }
        button:hover { background: #2980b9; }
        .reviewed { opacity: 0.6; }
    </style>
</head>
<body>
    <h1>snflwr.ai - Parent Dashboard</h1>
    <p>Safety monitoring for K-12 conversations</p>

    <div class="card">
        <h2>Overview (Last 7 Days)</h2>
        <div id="stats">Loading...</div>
    </div>

    <div class="card">
        <h2>Unreviewed Incidents</h2>
        <div id="incidents">Loading...</div>
    </div>

    <script>
        // Fetch and display stats
        async function loadDashboard() {
            try {
                const statsRes = await fetch('/api/analytics?days=7');
                const stats = await statsRes.json();

                const statsEl = document.getElementById('stats');
                statsEl.textContent = '';

                function addStat(label, value, color) {
                    const div = document.createElement('div');
                    div.className = 'stat';
                    const labelEl = document.createElement('div');
                    labelEl.className = 'stat-label';
                    labelEl.textContent = label;
                    const valueEl = document.createElement('div');
                    valueEl.className = 'stat-value';
                    valueEl.textContent = value;
                    if (color) valueEl.style.color = color;
                    div.appendChild(labelEl);
                    div.appendChild(valueEl);
                    statsEl.appendChild(div);
                }

                addStat('Total Incidents', stats.total_incidents || 0);
                addStat('Unresolved', stats.unresolved || 0, '#e74c3c');
                addStat('Awaiting Notification', stats.awaiting_parent_notification || 0, '#f39c12');

                const incidentsRes = await fetch('/api/incidents/unreviewed');
                const incidents = await incidentsRes.json();
                const incEl = document.getElementById('incidents');
                incEl.textContent = '';

                if (incidents.length === 0) {
                    const p = document.createElement('p');
                    p.style.color = '#27ae60';
                    p.textContent = 'No unreviewed incidents';
                    incEl.appendChild(p);
                } else {
                    incidents.forEach(function(inc) {
                        const div = document.createElement('div');
                        div.className = 'incident ' + inc.severity;

                        const ts = document.createElement('div');
                        ts.className = 'timestamp';
                        ts.textContent = new Date(inc.timestamp).toLocaleString();
                        div.appendChild(ts);

                        const profile = document.createElement('div');
                        const profileStrong = document.createElement('strong');
                        profileStrong.textContent = 'Profile: ';
                        profile.appendChild(profileStrong);
                        profile.appendChild(document.createTextNode(inc.profile_id));
                        div.appendChild(profile);

                        const type = document.createElement('div');
                        const typeStrong = document.createElement('strong');
                        typeStrong.textContent = 'Type: ';
                        type.appendChild(typeStrong);
                        type.appendChild(document.createTextNode(inc.incident_type));
                        div.appendChild(type);

                        const cats = document.createElement('div');
                        const cat1 = document.createElement('span');
                        cat1.className = 'category';
                        cat1.textContent = inc.incident_type;
                        cats.appendChild(cat1);
                        const cat2 = document.createElement('span');
                        cat2.className = 'category';
                        cat2.textContent = inc.severity + ' severity';
                        cats.appendChild(cat2);
                        div.appendChild(cats);

                        const btn = document.createElement('button');
                        btn.textContent = 'Mark as Reviewed';
                        btn.addEventListener('click', function() { markReviewed(inc.incident_id); });
                        div.appendChild(btn);

                        incEl.appendChild(div);
                    });
                }
            } catch (err) {
                console.error('Error loading dashboard:', err);
            }
        }

        async function markReviewed(id) {
            try {
                await fetch('/api/incidents/' + id + '/review', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ notes: 'Reviewed by parent' })
                });
                loadDashboard();
            } catch (err) {
                console.error('Error marking reviewed:', err);
            }
        }

        // Load on page load
        loadDashboard();

        // Refresh every 30 seconds
        setInterval(loadDashboard, 30000);
    </script>
</body>
</html>
"""


@app.route('/')
@require_auth
def dashboard():
    """Render parent dashboard"""
    return render_template_string(DASHBOARD_HTML)


@app.route('/api/analytics')
@require_auth
def get_analytics():
    """Get safety analytics"""
    days = request.args.get('days', 7, type=int)
    analytics = incident_logger.get_incident_statistics(days=days)
    return jsonify(analytics)


@app.route('/api/incidents/unreviewed')
@require_auth
def get_unreviewed_incidents():
    """Get unreviewed incidents across all profiles"""
    severity = request.args.get('severity')
    limit = request.args.get('limit', 50, type=int)

    # Fetch unresolved incidents for all profiles
    # incident_logger.get_profile_incidents requires a profile_id,
    # so we query the database directly for unresolved incidents
    from storage.database import db_manager
    from storage.db_adapters import DB_ERRORS

    try:
        query = """
            SELECT incident_id, profile_id, session_id, incident_type,
                   severity, content_snippet, timestamp, parent_notified,
                   resolved, metadata
            FROM safety_incidents
            WHERE resolved = 0
        """
        params = []

        if severity:
            query += " AND severity = ?"
            params.append(severity)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        results = db_manager.execute_query(query, tuple(params))
        incidents = []
        for row in results:
            inc = dict(row)
            # Decrypt encrypted fields before returning
            if inc.get('content_snippet'):
                try:
                    from storage.encryption import EncryptionManager
                    enc = EncryptionManager()
                    inc['content_snippet'] = enc.decrypt_string(inc['content_snippet'])
                except Exception:
                    inc['content_snippet'] = '[encrypted]'
            if inc.get('metadata'):
                try:
                    from storage.encryption import EncryptionManager
                    enc = EncryptionManager()
                    inc['metadata'] = enc.decrypt_dict(inc['metadata'])
                except Exception:
                    inc['metadata'] = {}
            incidents.append(inc)
        return jsonify(incidents)

    except DB_ERRORS as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/incidents/<int:incident_id>/review', methods=['POST'])
@require_auth
def mark_incident_reviewed(incident_id):
    """Mark incident as reviewed"""
    data = request.json or {}
    notes = data.get('notes', '')
    incident_logger.resolve_incident(incident_id, notes)
    return jsonify({'success': True})


@app.route('/api/user/<user_id>/report')
@require_auth
def get_user_report(user_id):
    """Get safety report for specific user"""
    days = request.args.get('days', 30, type=int)
    report = incident_logger.generate_parent_report(parent_id=user_id, days=days)
    return jsonify(report)


@app.route('/api/export')
@require_auth
def export_incidents():
    """Export incidents"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    fmt = request.args.get('format', 'json')

    from storage.database import db_manager
    from storage.db_adapters import DB_ERRORS

    try:
        query = "SELECT * FROM safety_incidents WHERE 1=1"
        params = []

        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)

        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date)

        query += " ORDER BY timestamp DESC"
        results = db_manager.execute_query(query, tuple(params))
        incidents = [dict(row) for row in results]

        if fmt == 'csv':
            import csv
            import io

            if not incidents:
                csv_data = ""
            else:
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=incidents[0].keys())
                writer.writeheader()
                writer.writerows(incidents)
                csv_data = output.getvalue()

            return csv_data, 200, {
                'Content-Type': 'text/csv',
                'Content-Disposition': f'attachment; filename=incidents_{datetime.now(timezone.utc).strftime("%Y%m%d")}.csv'
            }
        else:
            return json.dumps(incidents, default=str), 200, {'Content-Type': 'application/json'}

    except DB_ERRORS as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    print("\n" + "="*60)
    print("snflwr.ai - Parent Dashboard")
    print("="*60)
    print("\nDashboard starting on http://localhost:5000")
    print("\nThis dashboard allows parents/teachers to:")
    print("  - View safety analytics")
    print("  - Review blocked/flagged messages")
    print("  - Track student usage patterns")
    print("  - Export incident reports")
    print("\n" + "="*60 + "\n")

    # SECURITY: Never use debug=True in production - exposes code and allows RCE
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
