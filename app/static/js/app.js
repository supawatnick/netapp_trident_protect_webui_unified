// Trident Protect Web UI — shared utilities

// --- Auth helpers ---
function isAdmin() {
  return window.TP_ROLE === 'admin';
}
function tpRequireAdmin() {
  if (isAdmin()) return true;
  toast('Admin role required for this action', 'error');
  return false;
}
// Wrap fetch: redirect to /login on 401, show error on 403
async function tpFetch(url, opts) {
  const res = await fetch(url, opts);
  if (res.status === 401) {
    window.location.href = '/login';
    throw new Error('auth required');
  }
  if (res.status === 403) {
    let msg = 'Forbidden';
    try { const d = await res.clone().json(); if (d && d.error) msg = d.error; } catch (e) {}
    toast(msg, 'error');
    throw new Error('forbidden');
  }
  return res;
}

function stateBadge(state) {
  const s = (state || 'Unknown').toLowerCase();
  if (s === 'protected') return '<span class="badge protected">PROTECTED</span>';
  if (s === 'partial') return '<span class="badge partial">PARTIAL</span>';
  if (s === 'none' || s === 'not protected' || s === 'unprotected') return '<span class="badge none">NONE</span>';
  if (s === 'completed' || s === 'success') return '<span class="badge success">COMPLETED</span>';
  if (s === 'failed' || s === 'error') return '<span class="badge error">FAILED</span>';
  if (s === 'running' || s === 'inprogress' || s === 'pending' || s === '') return '<span class="badge pending">RUNNING</span>';
  return `<span class="badge info">${state}</span>`;
}

function toast(message, kind) {
  const c = document.getElementById('toast-container');
  if (!c) return;
  const el = document.createElement('div');
  el.className = `toast ${kind || ''}`;
  el.textContent = message;
  c.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function populateNsDropdown(id) {
  const sel = document.getElementById(id);
  try {
    const res = await tpFetch('/api/namespaces');
    const data = await res.json();
    const ns = (data.items || data.namespaces || []).map(i => typeof i === 'string' ? i : i.name);
    sel.innerHTML = ns.map(n => `<option value="${n}">${n}</option>`).join('');
  } catch (e) {
    sel.innerHTML = '<option value="">Failed to load</option>';
  }
}

// --- Sidebar Trident version banner ---
// Renders the detected Trident version across all cluster profiles into
// #tp-trident-version. Re-fetches on a 10-min interval to keep the banner
// fresh on long-open pages.
let _tridentVersionTimer = null;

function _tridentRefreshSec() { return 600; }

function _renderTridentVersion(data) {
  const el = document.getElementById('tp-trident-version');
  if (!el) return;
  const total = data.total || 0;
  const checked = data.checked || 0;
  const versions = data.versions || [];
  const unreachable = data.unreachable || [];
  const refreshMin = Math.round((data.refresh_sec || _tridentRefreshSec()) / 60);
  const refreshNote = `auto-refresh every ${refreshMin} min`;

  // Per-cluster lines for tooltip
  const lines = versions.map(v => {
    const ver = v.version || (v.reachable ? '?' : 'unreachable');
    return `${v.name}: ${ver}`;
  });

  // 0 profiles OR 0 reachable → "Trident —"
  if (total === 0 || checked === 0) {
    el.innerHTML = `Trident&nbsp;—`;
    el.title = total === 0
      ? `No cluster profiles configured. ${refreshNote}`
      : `No cluster reachable. ${refreshNote}`;
    el.style.color = 'inherit';
    return;
  }

  const display = data.display_version || '';
  const verHTML = `Trident&nbsp;v${escapeHtml(display)}`;

  if (data.matched) {
    const checkNote = checked < total
      ? ` (checked ${checked}/${total})`
      : '';
    el.innerHTML = `${verHTML}&nbsp;<span class="badge success" style="opacity:1;font-size:9px;padding:1px 5px;vertical-align:middle;">matched</span>`;
    el.title = `${lines.join('\n')}\n${refreshNote}${checkNote}`;
    el.style.color = 'inherit';
  } else {
    // Mismatch: show the higher version + warning badge
    el.innerHTML = `${verHTML}&nbsp;<span class="badge warning" style="opacity:1;font-size:9px;padding:1px 5px;vertical-align:middle;background:rgba(255,220,0,0.15);">mismatch</span>`;
    el.title = `Versions differ across clusters (showing highest):\n${lines.join('\n')}\n${refreshNote}`;
    el.style.color = 'inherit';
  }
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function loadTridentVersion() {
  const el = document.getElementById('tp-trident-version');
  if (!el) return;
  try {
    const res = await tpFetch('/api/trident-version');
    const data = await res.json();
    _renderTridentVersion(data);
    const sec = data.refresh_sec || _tridentRefreshSec();
    if (_tridentVersionTimer) clearTimeout(_tridentVersionTimer);
    _tridentVersionTimer = setTimeout(() => { loadTridentVersion(); }, sec * 1000);
  } catch (e) {
    // Auth/403 → tpFetch already showed toast. Keep the placeholder.
    el.title = 'Failed to load Trident version';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadTridentVersion();
});
