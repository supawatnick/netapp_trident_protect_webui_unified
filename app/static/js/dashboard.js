// Dashboard
async function loadDashboard() {
  try {
    const [statsRes, appsRes] = await Promise.all([
      fetch('/api/dashboard'),
      fetch('/api/applications'),
    ]);
    const stats = await statsRes.json();
    const apps = (await appsRes.json()).items || [];

    document.getElementById('stat-apps').textContent = stats.totals.applications;
    document.getElementById('stat-protected').textContent = stats.health.applicationsProtected;
    document.getElementById('stat-partial').textContent = stats.health.applicationsPartial;
    document.getElementById('stat-unprotected').textContent = stats.health.applicationsUnprotected;
    document.getElementById('stat-backups').textContent = stats.totals.backups;
    document.getElementById('stat-snapshots').textContent = stats.totals.snapshots;
    document.getElementById('stat-schedules').textContent = stats.totals.schedules;
    document.getElementById('stat-failed').textContent = stats.health.backupsFailed;

    const tbody = document.querySelector('#apps-table tbody');
    if (apps.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No applications</td></tr>';
    } else {
      tbody.innerHTML = apps.map(a => `
        <tr>
          <td>${a.namespace}</td>
          <td><b>${a.name}</b></td>
          <td>${stateBadge(a.protectionState)}</td>
          <td>${a.resourceCount}</td>
          <td>${a.storageHuman}</td>
          <td>${a.age}</td>
        </tr>
      `).join('');
    }
  } catch (e) {
    toast('Failed to load dashboard: ' + e.message, 'error');
  }
}
loadDashboard();
setInterval(loadDashboard, 30000);
