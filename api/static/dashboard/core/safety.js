function isUnresolved(item) {
  if (item.resolved === true || item.is_resolved === true) return false;
  if (item.resolved_at) return false; // non-empty timestamp => resolved
  return true;
}

function isCrisis(item) {
  // Real severities are 'critical' | 'major' | 'minor' (backend Severity enum);
  // 'critical' is the most urgent and drives the red "immediate action" banner.
  // ('crisis' kept as a defensive alias in case any caller uses it.)
  const sev = String(item.severity || '').toLowerCase();
  if (sev === 'critical' || sev === 'crisis') return true;
  // Incidents flag escalation via `incident_type` (e.g. "escalating_requests");
  // also check category/type for robustness against alert shapes.
  const hay = `${item.incident_type || ''} ${item.category || ''} ${item.type || ''}`.toLowerCase();
  return hay.includes('crisis') || hay.includes('escalat');
}

export function deriveSafetyState(alerts = [], incidents = []) {
  const all = [...alerts, ...incidents];
  // Crisis/attention are derived from UNRESOLVED items only: a resolved critical
  // incident from the past must not pin the dashboard to a permanent red state.
  // The "never show all-clear while something is unresolved" guarantee still
  // holds because attentionCount counts every unresolved item regardless.
  const unresolved = all.filter(isUnresolved);
  const attentionCount = unresolved.length;
  const hasCrisis = unresolved.some(isCrisis);
  let level = 'clear';
  if (hasCrisis) level = 'crisis';
  else if (attentionCount > 0) level = 'attention';
  return { level, attentionCount, hasCrisis };
}
