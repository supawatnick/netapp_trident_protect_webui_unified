// AppVault management page

async function loadAppVaults() {
  try {
    const res = await tpFetch('/api/appvaults');
    const data = await res.json();
    const items = data.items || [];
    const tbody = document.getElementById('appvaults-tbody');
    if (items.length === 0) {
      const empty = isAdmin()
        ? 'No AppVaults found. Click "+ Add AppVault".'
        : 'No AppVaults found.';
      tbody.innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(empty)}</td></tr>`;
      return;
    }
    tbody.innerHTML = items.map(av => {
      const delBtn = isAdmin()
        ? `<button class="btn small danger" onclick="deleteAppVault('${escapeHtml(av.namespace)}','${escapeHtml(av.name)}')">Del</button>`
        : '';
      return `
      <tr>
        <td><b>${escapeHtml(av.name)}</b></td>
        <td>${escapeHtml(av.namespace)}</td>
        <td>${escapeHtml(av.provider)}</td>
        <td class="text-mono">${escapeHtml(av.bucket)}</td>
        <td>${stateBadge(av.state)}</td>
        <td class="text-muted" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(av.error || '')}">${escapeHtml(av.error || '—')}</td>
        <td>
          <button class="btn small secondary" onclick="viewAppVault('${escapeHtml(av.namespace)}','${escapeHtml(av.name)}')">View</button>
          ${delBtn}
        </td>
      </tr>
    `;
    }).join('');
  } catch (e) {
    toast('Failed to load AppVaults: ' + e.message, 'error');
  }
}

// ---- View AppVault modal ----
async function viewAppVault(ns, name) {
  try {
    const [jsonRes, yamlRes] = await Promise.all([
      tpFetch(`/api/appvaults/${encodeURIComponent(ns)}/${encodeURIComponent(name)}`),
      tpFetch(`/api/appvaults/${encodeURIComponent(ns)}/${encodeURIComponent(name)}?format=yaml`),
    ]);
    const av = await jsonRes.json();
    const yamlText = await yamlRes.text();
    renderViewAppVaultModal(av, yamlText);
    document.getElementById('view-appvault-modal').style.display = 'flex';
  } catch (e) {
    // tpFetch already toasted on 401/403
  }
}

function renderViewAppVaultModal(av, yamlText) {
  const md = av.metadata || {};
  const sp = av.spec || {};
  const st = av.status || {};
  const pc = sp.providerConfig || {};
  const s3 = pc.s3 || {};
  const creds = sp.providerCredentials || {};
  const secretRef = (creds.accessKeyID || {}).valueFromSecret || {};
  const cn = md.creationTimestamp || '';

  document.getElementById('vav-name').textContent = md.name || '—';
  document.getElementById('vav-namespace').textContent = md.namespace || '—';
  document.getElementById('vav-created').textContent = cn ? `${formatTime(cn)} (${formatAge(cn)})` : '—';
  document.getElementById('vav-uid').textContent = md.uid || '—';

  document.getElementById('vav-provider').textContent = sp.providerType || '—';
  document.getElementById('vav-endpoint').textContent = s3.endpoint || '—';
  document.getElementById('vav-bucket').textContent = s3.bucketName || '—';
  document.getElementById('vav-skipcert').textContent = s3.skipCertValidation ? 'Yes' : 'No';
  document.getElementById('vav-secret').textContent = secretRef.name || '—';

  document.getElementById('vav-state').innerHTML = stateBadge(st.state || 'Unknown');
  document.getElementById('vav-status-uid').textContent = st.uid || '—';

  const conds = st.conditions || [];
  document.getElementById('vav-conditions-body').innerHTML = conds.length === 0
    ? '<tr><td colspan="4" class="empty">No conditions</td></tr>'
    : conds.map(c => `
        <tr>
          <td>${escapeHtml(c.type || '—')}</td>
          <td>${escapeHtml(c.status || '—')}</td>
          <td>${escapeHtml(c.reason || '—')}</td>
          <td>${escapeHtml(c.message || '—')}</td>
        </tr>
      `).join('');

  document.getElementById('vav-yaml').textContent = yamlText;
}

function hideViewAppVaultModal() {
  document.getElementById('view-appvault-modal').style.display = 'none';
}

async function copyAppVaultYAML() {
  const text = document.getElementById('vav-yaml').textContent;
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied to clipboard', 'success');
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); toast('Copied (fallback)', 'success'); }
    catch (_) { toast('Copy failed', 'error'); }
    document.body.removeChild(ta);
  }
}

// ---- Helpers ----
function formatTime(ts) {
  try {
    const d = new Date(ts);
    return d.toISOString().replace('T', ' ').slice(0, 19);
  } catch (_) { return ts; }
}

function formatAge(ts) {
  try {
    const d = new Date(ts);
    const sec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec/60) + 'm ago';
    if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
    return Math.floor(sec/86400) + 'd ago';
  } catch (_) { return ''; }
}

// ---- Add AppVault form ----
function showAddAppVaultForm() {
  if (!tpRequireAdmin()) return;
  document.getElementById('av-form-title').textContent = 'Add AppVault';
  document.getElementById('av-name').value = '';
  document.getElementById('av-namespace').value = 'trident-protect';
  document.getElementById('av-endpoint').value = '';
  document.getElementById('av-bucket').value = '';
  document.getElementById('av-skip-cert').value = 'true';
  document.getElementById('av-access-key').value = '';
  document.getElementById('av-secret-key').value = '';
  document.getElementById('appvault-form').style.display = 'block';
}

function hideAppVaultForm() {
  document.getElementById('appvault-form').style.display = 'none';
}

async function saveAppVault() {
  if (!tpRequireAdmin()) return;
  const name = document.getElementById('av-name').value.trim();
  const body = {
    name: name,
    namespace: document.getElementById('av-namespace').value.trim() || 'trident-protect',
    provider: document.getElementById('av-provider').value,
    endpoint: document.getElementById('av-endpoint').value.trim(),
    bucket: document.getElementById('av-bucket').value.trim(),
    skip_cert: document.getElementById('av-skip-cert').value === 'true',
    secret_name: name ? name + '-secret' : '',
    access_key: document.getElementById('av-access-key').value.trim(),
    secret_key: document.getElementById('av-secret-key').value.trim(),
  };
  if (!body.name || !body.endpoint || !body.bucket) {
    toast('Name, Endpoint, Bucket are required', 'error');
    return;
  }
  if (!body.access_key || !body.secret_key) {
    toast('Access Key ID and Secret Access Key are required', 'error');
    return;
  }
  try {
    const res = await tpFetch('/api/appvaults', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    const r = await res.json();
    toast(r.message, r.ok ? 'success' : 'error');
    if (r.ok) {
      hideAppVaultForm();
      await loadAppVaults();
    }
  } catch (e) {
    // tpFetch already toasted on 401/403
  }
}

async function deleteAppVault(namespace, name) {
  if (!tpRequireAdmin()) return;
  if (!confirm(`Delete AppVault ${namespace}/${name}?\nThis will remove the AppVault CR (secret stays).`)) return;
  try {
    const res = await tpFetch(`/api/appvaults/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}`, {method:'DELETE'});
    const r = await res.json();
    toast(r.message, r.ok ? 'success' : 'error');
    if (r.ok) await loadAppVaults();
  } catch (e) {
    // tpFetch already toasted on 401/403
  }
}

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

loadAppVaults();
