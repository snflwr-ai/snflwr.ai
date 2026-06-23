function isUnresolved(item) {
  if (item.resolved === true || item.is_resolved === true) return false;
  if (item.resolved_at) return false; // non-empty timestamp => resolved
  return true;
}

function isCrisis(item) {
  if (String(item.severity || '').toLowerCase() === 'crisis') return true;
  const hay = `${item.category || ''} ${item.type || ''}`.toLowerCase();
  return hay.includes('crisis') || hay.includes('escalation');
}

export function deriveSafetyState(alerts = [], incidents = []) {
  const all = [...alerts, ...incidents];
  const attentionCount = all.filter(isUnresolved).length;
  const hasCrisis = all.some(isCrisis);
  let level = 'clear';
  if (hasCrisis) level = 'crisis';
  else if (attentionCount > 0) level = 'attention';
  return { level, attentionCount, hasCrisis };
}
