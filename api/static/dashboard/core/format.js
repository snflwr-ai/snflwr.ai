export function formatDuration(minutes) {
  const m = Math.max(0, Math.round(Number(minutes) || 0));
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function aggregateActivity(sessions = []) {
  return sessions.reduce(
    (acc, s) => ({
      totalSessions: acc.totalSessions + 1,
      totalQuestions: acc.totalQuestions + (Number(s.questions_asked) || 0),
      totalMinutes: acc.totalMinutes + (Number(s.duration_minutes) || 0),
    }),
    { totalSessions: 0, totalQuestions: 0, totalMinutes: 0 }
  );
}
