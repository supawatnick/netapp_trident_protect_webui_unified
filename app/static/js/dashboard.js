// Dashboard — resilient API parsing and session handling.
async function dashboardJson(url) {
  const response = await tpFetch(url, { headers: { 'Accept': 'application/json' } });
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new Error('Server returned a page instead of JSON. Please sign in again or check the preview server.');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
  return data;
}

async function loadDashboard() {
  try {
    const [stats, appData] = await Promise.all([
      dashboardJson('/api/dashboard'),
      dashboardJson('/api/applications')
    ]);
    const apps = appData.items || [];
    if (!stats.totals || !stats.health) throw new Error('Dashboard response is missing expected metrics');
    document.getElementById('stat-apps').textContent = stats.totals.applications ?? '—';
    document.getElementById('stat-protected').textContent = stats.health.applicationsProtected ?? '—';
    document.getElementById('stat-partial').textContent = stats.health.applicationsPartial ?? '—';
    document.getElementById('stat-unprotected').textContent = stats.health.applicationsUnprotected ?? '—';
    document.getElementById('stat-backups').textContent = stats.totals.backups ?? '—';
    document.getElementById('stat-snapshots').textContent = stats.totals.snapshots ?? '—';
    document.getElementById('stat-schedules').textContent = stats.totals.schedules ?? '—';
    document.getElementById('stat-failed').textContent = stats.health.backupsFailed ?? '—';
    const tbody = document.querySelector('#apps-table tbody');
    if (!apps.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No applications</td></tr>';
    } else {
      tbody.innerHTML = apps.map(a => `<tr>
        <td>${escapeHtml(a.namespace)}</td><td><b>${escapeHtml(a.name)}</b></td>
        <td>${stateBadge(a.protectionState)}</td><td>${escapeHtml(a.resourceCount)}</td>
        <td>${escapeHtml(a.storageHuman)}</td><td>${escapeHtml(a.age)}</td>
      </tr>`).join('');
    }
  } catch (e) {
    if (e.message !== 'auth required') {
      toast('Failed to load dashboard: ' + e.message, 'error');
    }
  }
}
loadDashboard();
setInterval(loadDashboard, 30000);
