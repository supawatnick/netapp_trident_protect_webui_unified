// Settings → Credentials page logic (admin-only)

let credsEditingUser = null;

function credsShowAddUserForm() {
  credsEditingUser = null;
  document.getElementById('creds-user-form-title').textContent = 'Add Local User';
  document.getElementById('creds-username').value = '';
  document.getElementById('creds-username').disabled = false;
  document.getElementById('creds-password').value = '';
  document.getElementById('creds-role').value = 'readonly';
  document.getElementById('creds-user-form').style.display = 'block';
}

function credsEditUser(username, role) {
  credsEditingUser = username;
  document.getElementById('creds-user-form-title').textContent = 'Edit User: ' + username;
  document.getElementById('creds-username').value = username;
  document.getElementById('creds-username').disabled = true;
  document.getElementById('creds-password').value = '';
  document.getElementById('creds-role').value = role || 'readonly';
  document.getElementById('creds-user-form').style.display = 'block';
}

function credsHideUserForm() {
  document.getElementById('creds-user-form').style.display = 'none';
  credsEditingUser = null;
}

async function credsSaveUser() {
  const username = document.getElementById('creds-username').value.trim().toLowerCase();
  const password = document.getElementById('creds-password').value;
  const role = document.getElementById('creds-role').value;
  if (!username) { toast('Username required', 'error'); return; }
  // Disallow \ and @ in local usernames — they are reserved for domain logins
  if (!credsEditingUser && (username.includes('\\') || username.includes('@'))) {
    toast("Username must not contain '\\' or '@' (reserved for domain users)", 'error');
    return;
  }
  if (!credsEditingUser && !password) { toast('Password required for new user', 'error'); return; }
  if (password && password.length < 4) { toast('Password must be at least 4 characters', 'error'); return; }

  try {
    let res;
    if (credsEditingUser) {
      const body = {role};
      if (password) body.password = password;
      res = await tpFetch(`/api/credentials/users/${encodeURIComponent(credsEditingUser)}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
    } else {
      res = await tpFetch('/api/credentials/users', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password, role}),
      });
    }
    const r = await res.json();
    toast(r.message, r.ok ? 'success' : 'error');
    if (r.ok) {
      credsHideUserForm();
      await credsLoadUsers();
    }
  } catch (e) {
    toast('Save failed: ' + e.message, 'error');
  }
}

async function credsDeleteUser(username) {
  if (!confirm(`Delete user "${username}"? This cannot be undone.`)) return;
  try {
    const res = await tpFetch(`/api/credentials/users/${encodeURIComponent(username)}`, {method: 'DELETE'});
    const r = await res.json();
    toast(r.message, r.ok ? 'success' : 'error');
    if (r.ok) await credsLoadUsers();
  } catch (e) {
    toast('Delete failed: ' + e.message, 'error');
  }
}

async function credsLoadUsers() {
  const tbody = document.getElementById('creds-users-tbody');
  try {
    const res = await tpFetch('/api/credentials/users?source=local');
    const data = await res.json();
    const items = data.items || [];
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No local users yet. Add one above.</td></tr>';
      return;
    }
    // Use data-attributes + event delegation (no inline JS) so usernames with
    // special characters (quotes, backslashes) cannot break out of the handler.
    tbody.innerHTML = items.map(u => `
      <tr>
        <td><b>${escapeHtml(u.username)}</b></td>
        <td><span class="badge" style="background:${u.role === 'admin' ? '#008000' : '#666'};color:#fff;border:none;">${escapeHtml(u.role)}</span></td>
        <td>${escapeHtml(u.source)}</td>
        <td class="text-muted">${escapeHtml(u.created_at || '—')}</td>
        <td class="text-muted">${escapeHtml(u.last_login || '—')}</td>
        <td>
          <button class="btn small secondary" data-action="edit" data-username="${encodeURIComponent(u.username)}" data-role="${encodeURIComponent(u.role || '')}">Edit</button>
          <button class="btn small danger" data-action="del" data-username="${encodeURIComponent(u.username)}">Del</button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">Failed to load users</td></tr>';
  }
}

async function credsLoadDomainUsers() {
  const tbody = document.getElementById('creds-domain-users-tbody');
  try {
    const res = await tpFetch('/api/credentials/users?source=ldap');
    const data = await res.json();
    const items = data.items || [];
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">No domain users have signed in yet.</td></tr>';
      return;
    }
    tbody.innerHTML = items.map(u => `
      <tr>
        <td><b>${escapeHtml(u.username)}</b></td>
        <td><span class="badge" style="background:${u.role === 'admin' ? '#008000' : '#666'};color:#fff;border:none;">${escapeHtml(u.role)}</span></td>
        <td class="text-muted">${escapeHtml(u.created_at || '—')}</td>
        <td class="text-muted">${escapeHtml(u.last_login || '—')}</td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">Failed to load domain users</td></tr>';
  }
}

async function credsLoadLdap() {
  try {
    const res = await tpFetch('/api/credentials/ldap');
    const data = await res.json();
    const ldap = data.ldap || {};
    document.getElementById('ldap-enabled').value = ldap.enabled ? 'true' : 'false';
    document.getElementById('ldap-server').value = ldap.server || '';
    document.getElementById('ldap-port').value = ldap.port || 389;
    document.getElementById('ldap-use-tls').value = ldap.use_tls ? 'true' : 'false';
    document.getElementById('ldap-skip-cert').value = ldap.skip_cert_verify === false ? 'false' : 'true';
    document.getElementById('ldap-bind-dn').value = ldap.bind_dn || '';
    document.getElementById('ldap-bind-password').value = '';
    document.getElementById('ldap-bind-password').placeholder =
      ldap.bind_password_set ? '(saved — leave blank to keep)' : '';
    document.getElementById('ldap-base-dn').value = ldap.base_dn || '';
    document.getElementById('ldap-username-attribute').value = ldap.username_attribute || 'sAMAccountName';
    document.getElementById('ldap-user-filter').value = ldap.user_filter || '';
    document.getElementById('ldap-netbios-domain').value = ldap.netbios_domain || '';
    const groups = (ldap.admin_group_dns || []).join('\n');
    document.getElementById('ldap-admin-group-dns').value = groups;
    if (data.default_role) {
      document.getElementById('ldap-default-role').value = data.default_role;
    }
  } catch (e) {
    toast('Failed to load LDAP: ' + e.message, 'error');
  }
}

async function credsSaveLdap() {
  const body = {
    auth_enabled: true,
    default_role: document.getElementById('ldap-default-role').value,
    ldap_enabled: document.getElementById('ldap-enabled').value === 'true',
    server: document.getElementById('ldap-server').value.trim(),
    port: parseInt(document.getElementById('ldap-port').value || '389', 10),
    use_tls: document.getElementById('ldap-use-tls').value === 'true',
    skip_cert_verify: document.getElementById('ldap-skip-cert').value === 'true',
    bind_dn: document.getElementById('ldap-bind-dn').value.trim(),
    base_dn: document.getElementById('ldap-base-dn').value.trim(),
    username_attribute: document.getElementById('ldap-username-attribute').value.trim(),
    user_filter: document.getElementById('ldap-user-filter').value.trim(),
    admin_group_dns: document.getElementById('ldap-admin-group-dns').value,
    netbios_domain: document.getElementById('ldap-netbios-domain').value.trim(),
  };
  const bp = document.getElementById('ldap-bind-password').value;
  if (bp) body.bind_password = bp;
  try {
    const res = await tpFetch('/api/credentials/ldap', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const r = await res.json();
    showLdapMsg(r.message, r.ok ? 'success' : 'error');
    if (r.ok) {
      document.getElementById('ldap-bind-password').value = '';
      document.getElementById('ldap-bind-password').placeholder = '(saved — leave blank to keep)';
    }
  } catch (e) {
    showLdapMsg('Save failed: ' + e.message, 'error');
  }
}

async function credsTestLdap() {
  await credsSaveLdap();
  const body = {};
  const u = document.getElementById('creds-test-username').value.trim();
  const p = document.getElementById('creds-test-password').value;
  if (u) body.username = u;
  if (p) body.password = p;
  showLdapMsg('Testing…', 'info');
  try {
    const res = await tpFetch('/api/credentials/ldap/test', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const r = await res.json();
    showLdapMsg(r.message, r.ok ? 'success' : 'error');
  } catch (e) {
    showLdapMsg('Test failed: ' + e.message, 'error');
  }
}

function showLdapMsg(text, kind) {
  const el = document.getElementById('creds-ldap-msg');
  el.textContent = text;
  el.style.color = kind === 'error' ? 'var(--color-error)' :
                   kind === 'success' ? 'var(--color-success)' : '#666';
  el.style.fontSize = '13px';
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

// Event delegation for Local Users table buttons (safer than inline onclick).
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('creds-users-tbody').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const username = decodeURIComponent(btn.dataset.username || '');
    const role = decodeURIComponent(btn.dataset.role || '');
    if (action === 'edit') credsEditUser(username, role);
    else if (action === 'del') credsDeleteUser(username);
  });
});

// Bootstrap (the page also loads credentials.js on /settings/credentials only,
// but DOMContentLoaded may already have fired by the time we reach this point
// in tests / hot reload. The handler is idempotent because it is attached to a
// specific tbody and uses event delegation.)
if (document.readyState !== 'loading') {
  const tbody = document.getElementById('creds-users-tbody');
  if (tbody && !tbody.dataset.bound) {
    tbody.dataset.bound = '1';
    tbody.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;
      const action = btn.dataset.action;
      const username = decodeURIComponent(btn.dataset.username || '');
      const role = decodeURIComponent(btn.dataset.role || '');
      if (action === 'edit') credsEditUser(username, role);
      else if (action === 'del') credsDeleteUser(username);
    });
  }
}

credsLoadUsers();
credsLoadDomainUsers();
credsLoadLdap();
