// Settings → Clusters page logic (Kubernetes edition, v1.4.3)

let currentProfiles = {};
let currentActive = '';
let editingProfile = null;
let loggingInProfile = null;
let fullToken = null;

async function loadSettings() {
  try {
    const res = await tpFetch('/api/settings');
    const data = await res.json();
    currentProfiles = data.profiles || {};
    currentActive = data.active || '';
    const maxProfiles = data.maxProfiles || 2;
    const profileCount = data.profileCount || Object.keys(currentProfiles).length;
    const canAdd = data.canAddProfile !== undefined ? data.canAddProfile : (profileCount < maxProfiles);

    // Update "+ Add Profile" button state
    const addBtn = document.querySelector('button[onclick="showAddForm()"]');
    if (addBtn) {
      addBtn.disabled = !canAdd || !tpRequireAdmin();
      addBtn.title = !tpRequireAdmin() ? 'Admin role required' :
        (canAdd ? '' : `Maximum ${maxProfiles} cluster profiles allowed (DR source + destination). Delete an existing profile first.`);
    }

    // Show/hide profile-limit banner
    const banner = document.getElementById('profile-limit-banner');
    if (banner) {
      banner.style.display = (profileCount >= maxProfiles) ? 'block' : 'none';
      banner.innerHTML = `<b>${profileCount}/${maxProfiles} cluster profiles configured.</b> DR requires exactly source + destination clusters. Delete an existing profile to add a new one.`;
    }

    document.getElementById('whoami-user').textContent = data.whoami?.user || '—';
    document.getElementById('whoami-server').textContent = data.whoami?.server || '—';
    document.getElementById('active-profile').textContent = currentActive || '(none)';

    renderProfiles();
    loadClusterInfo();
  } catch (e) {
    toast('Failed to load settings: ' + e.message, 'error');
  }
}

async function loadClusterInfo() {
  try {
    const res = await tpFetch('/api/cluster-info');
    const info = await res.json();
    const el = document.getElementById('ocp-cluster-name');
    if (!info || !info.name) {
      el.textContent = '—';
      return;
    }
    let txt = info.name;
    if (info.platform) txt += ` <span style="color:#999;font-size:12px">(${escapeHtml(info.platform)})</span>`;
    if (info.version) txt += ` <span style="color:#999;font-size:12px">· v${escapeHtml(info.version)}</span>`;
    el.innerHTML = txt;
  } catch (e) {
    document.getElementById('ocp-cluster-name').textContent = '—';
  }
}

function renderProfiles() {
  const tbody = document.getElementById('profiles-tbody');
  const names = Object.keys(currentProfiles).sort();
  if (names.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">No profiles yet. Click "+ Add Profile".</td></tr>';
    return;
  }
  tbody.innerHTML = names.map(name => {
    const p = currentProfiles[name];
    const isActive = name === currentActive;
    const isAdminUser = isAdmin();
    const hasToken = !!p.token;

    // Buttons (match 105):
    //  - Active: "In use" badge only.
    //  - Inactive + has stored token: "Switch" (one-click, auto-login)
    //    + "Login" (re-auth / replace token, in case saved one expired).
    //  - Inactive + no token: "Login & Switch" (paste token).
    let switchBtn;
    if (isActive) {
      switchBtn = '<span class="badge success">In use</span>';
    } else if (hasToken) {
      switchBtn = `<button class="btn small" onclick="switchToProfile('${escapeHtml(name)}')">Switch</button>` +
                  ` <button class="btn small secondary" onclick="showLoginForm('${escapeHtml(name)}')" title="Re-authenticate with a new bearer token (saved token may be expired)">Login</button>`;
    } else {
      switchBtn = `<button class="btn small" onclick="showLoginForm('${escapeHtml(name)}')">Login &amp; Switch</button>`;
    }
    const statusBadge = isActive
      ? '<span class="badge success">In use</span>'
      : (hasToken
          ? '<span class="badge pending">Standby (token saved)</span>'
          : '<span class="badge pending">Standby</span>');

    const actions = switchBtn
      + (isAdminUser
        ? ` <button class="btn small secondary" onclick="editProfile('${escapeHtml(name)}')">Edit</button>`
        : '')
      + (isAdminUser
        ? ` <button class="btn small danger" onclick="deleteProfile('${escapeHtml(name)}')"${isActive ? ' disabled' : ''}>Del</button>`
        : '');

    return `
      <tr${isActive ? ' style="background:#fffbea"' : ''}>
        <td><b>${escapeHtml(name)}</b>${isActive ? ' <span class="badge protected">ACTIVE</span>' : ''}</td>
        <td class="text-mono">${escapeHtml(p.api_url || '')}</td>
        <td class="text-muted">${escapeHtml(p.description || '—')}</td>
        <td>${statusBadge}</td>
        <td>${actions}</td>
      </tr>
    `;
  }).join('');
}

async function testConnection() {
  try {
    const res = await tpFetch('/api/settings/test', {method: 'POST'});
    const r = await res.json();
    toast(r.ok ? 'Connection OK: ' + r.message : 'Failed: ' + r.message, r.ok ? 'success' : 'error');
  } catch (e) {
    toast('Test failed: ' + e.message, 'error');
  }
}

async function showFullToken() {
  if (fullToken) {
    prompt('Bearer Token (copy this):', fullToken);
    return;
  }
  try {
    const res = await tpFetch('/api/settings/token');
    const r = await res.json();
    if (r.ok) {
      fullToken = r.full;
      prompt('Bearer Token (copy this):', fullToken);
    } else {
      toast(r.message, 'error');
    }
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

function showAddForm() {
  if (!tpRequireAdmin()) return;
  if (Object.keys(currentProfiles).length >= 2) {
    toast('Maximum 2 cluster profiles allowed (DR source + destination). Delete an existing profile first.', 'error');
    return;
  }
  editingProfile = null;
  document.getElementById('profile-form-title').textContent = 'Add Profile';
  document.getElementById('pf-name').value = '';
  document.getElementById('pf-name').disabled = false;
  document.getElementById('pf-description').value = '';
  document.getElementById('pf-api-url').value = 'https://';
  document.getElementById('pf-insecure').value = 'true';
  document.getElementById('profile-form').style.display = 'block';
}

function editProfile(name) {
  if (!tpRequireAdmin()) return;
  const p = currentProfiles[name];
  if (!p) return;
  editingProfile = name;
  document.getElementById('profile-form-title').textContent = 'Edit Profile: ' + name;
  document.getElementById('pf-name').value = name;
  document.getElementById('pf-name').disabled = true;
  document.getElementById('pf-description').value = p.description || '';
  document.getElementById('pf-api-url').value = p.api_url || '';
  document.getElementById('pf-insecure').value = p.insecure_skip_tls ? 'true' : 'false';
  document.getElementById('profile-form').style.display = 'block';
}

function hideProfileForm() {
  document.getElementById('profile-form').style.display = 'none';
  editingProfile = null;
}

async function saveProfile() {
  if (!tpRequireAdmin()) return;
  const body = {
    name: document.getElementById('pf-name').value.trim(),
    description: document.getElementById('pf-description').value.trim(),
    api_url: document.getElementById('pf-api-url').value.trim(),
    insecure_skip_tls: document.getElementById('pf-insecure').value === 'true',
  };
  if (!body.name || !body.api_url) {
    toast('Name and API URL are required', 'error');
    return;
  }
  try {
    const res = await tpFetch('/api/settings/profile', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const r = await res.json();
    toast(r.message, r.ok ? 'success' : 'error');
    if (r.ok) {
      hideProfileForm();
      await loadSettings();
    }
  } catch (e) {
    toast('Save failed: ' + e.message, 'error');
  }
}

async function deleteProfile(name) {
  if (!tpRequireAdmin()) return;
  if (!confirm(`Delete profile "${name}"?`)) return;
  try {
    const res = await tpFetch(`/api/settings/profile/${encodeURIComponent(name)}`, {method: 'DELETE'});
    const r = await res.json();
    toast(r.message, r.ok ? 'success' : 'error');
    if (r.ok) await loadSettings();
  } catch (e) {
    toast('Delete failed: ' + e.message, 'error');
  }
}

function showLoginForm(name) {
  loggingInProfile = name;
  document.getElementById('login-profile-name').textContent = name;
  document.getElementById('login-token').value = '';
  document.getElementById('login-form').style.display = 'block';
}

function hideLoginForm() {
  document.getElementById('login-form').style.display = 'none';
  loggingInProfile = null;
}

async function loginWithToken() {
  const token = document.getElementById('login-token').value.trim();
  if (!token) {
    toast('Bearer token required', 'error');
    return;
  }
  try {
    const res = await tpFetch('/api/settings/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: loggingInProfile, token}),
    });
    const r = await res.json();
    toast(r.message, r.ok ? 'success' : 'error');
    if (r.ok) {
      hideLoginForm();
      await loadSettings();
      await testConnection();
    }
  } catch (e) {
    toast('Login failed: ' + e.message, 'error');
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

function toggleHelp() {
  const content = document.getElementById('help-content');
  const toggle = document.getElementById('help-toggle');
  if (content.style.display === 'none') {
    content.style.display = 'block';
    toggle.textContent = '▲';
  } else {
    content.style.display = 'none';
    toggle.textContent = '▼';
  }
}

function toggleKubeconfig() {
  const content = document.getElementById('kubeconfig-content');
  const toggle = document.getElementById('kubeconfig-toggle');
  if (content.style.display === 'none') {
    content.style.display = 'block';
    toggle.textContent = '▲';
  } else {
    content.style.display = 'none';
    toggle.textContent = '▼';
  }
}

function loadKubeconfigFile() {
  const fileInput = document.getElementById('kubeconfig-file');
  const file = fileInput.files[0];
  if (!file) {
    showKubeconfigMsg('Choose a kubeconfig file first', 'error');
    return;
  }
  const reader = new FileReader();
  reader.onload = (e) => {
    document.getElementById('kubeconfig-text').value = e.target.result;
    showKubeconfigMsg(`Loaded ${file.name} (${file.size} bytes)`, 'success');
  };
  reader.onerror = () => {
    showKubeconfigMsg('Failed to read file', 'error');
  };
  reader.readAsText(file);
}

function showKubeconfigMsg(text, kind) {
  const el = document.getElementById('kubeconfig-msg');
  el.textContent = text;
  el.style.color = kind === 'error' ? 'var(--color-error)' :
                   kind === 'success' ? 'var(--color-success)' :
                   'var(--color-text-muted, #666)';
  el.style.fontSize = '13px';
}

function showKubeconfigPreview(preview) {
  const el = document.getElementById('kubeconfig-preview');
  const lines = [
    `<b>Server:</b> <code>${escapeHtml(preview.server)}</code>`,
    `<b>User:</b> <code>${escapeHtml(preview.user)}</code>`,
    `<b>Context:</b> <code>${escapeHtml(preview.context)}</code>`,
    `<b>Profile created:</b> <code>${escapeHtml(preview.profile)}</code>`,
    `<b>TLS skip:</b> ${preview.insecure_skip_tls ? 'yes' : 'no'}`,
  ];
  el.innerHTML = lines.join('<br/>');
  el.style.display = 'block';
}

async function importKubeconfig() {
  if (!tpRequireAdmin()) return;
  const yaml = document.getElementById('kubeconfig-text').value.trim();
  if (!yaml) {
    showKubeconfigMsg('Paste or load a kubeconfig first', 'error');
    return;
  }
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = 'Applying...';
  showKubeconfigMsg('Validating and writing kubeconfig...', 'info');
  try {
    const res = await tpFetch('/api/settings/kubeconfig', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kubeconfig: yaml}),
    });
    const r = await res.json();
    if (r.ok) {
      showKubeconfigMsg(r.message, 'success');
      if (r.preview) showKubeconfigPreview(r.preview);
      loadSettings();
    } else {
      showKubeconfigMsg(r.message, 'error');
      document.getElementById('kubeconfig-preview').style.display = 'none';
    }
  } catch (e) {
    showKubeconfigMsg('Request failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Apply Kubeconfig';
  }
}

loadSettings();

async function switchToProfile(name) {
  try {
    const res = await tpFetch('/api/settings/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name}),
    });
    const r = await res.json();
    if (r.ok && r.auto_login) {
      toast(`Switched to ${name} (auto-login)`, 'success');
    } else if (r.ok && r.needs_reauth) {
      toast(`Saved token for ${name} is expired or invalid. Please re-authenticate.`, 'error');
      showLoginForm(name);
    } else if (r.ok) {
      toast(`Active: ${name}.`, 'info');
    } else {
      toast(r.message, 'error');
    }
    if (r.ok) {
      await loadSettings();
    }
  } catch (e) {
    toast('Switch failed: ' + e.message, 'error');
  }
}

