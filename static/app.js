/* matapp — app.js */

// ── Auth ──────────────────────────────────────────────────────────────────────

async function doLogout() {
  await fetch('/api/auth/logout', { method: 'POST' });
  window.location.href = '/login';
}

// ── Store setup wizard ────────────────────────────────────────────────────────

const CHAIN_LABELS = {
  willys: { label: 'Willys',  color: '#15803d', bg: '#dcfce7' },
  ica:    { label: 'ICA',     color: '#b91c1c', bg: '#fee2e2' },
  coop:   { label: 'Coop',    color: '#1d4ed8', bg: '#dbeafe' },
  lidl:   { label: 'Lidl',    color: '#92400e', bg: '#fef3c7' },
  hemkop: { label: 'Hemköp',  color: '#7c3aed', bg: '#ede9fe' },
  netto:  { label: 'Netto',   color: '#374151', bg: '#f3f4f6' },
  other:  { label: 'Butik',   color: '#374151', bg: '#f3f4f6' },
};

function _chainBadge(chain) {
  const c = CHAIN_LABELS[chain] || CHAIN_LABELS.other;
  return `<span style="font-size:11px;font-weight:700;padding:2px 7px;border-radius:999px;background:${c.bg};color:${c.color}">${c.label}</span>`;
}

function showChangeStore() {
  const modal = document.getElementById('setup-modal');
  if (modal) {
    modal.style.display = 'flex';
    document.getElementById('store-search-input')?.focus();
  }
}

async function searchStores() {
  const inputEl  = document.getElementById('store-search-input');
  const results  = document.getElementById('store-results');
  const err      = document.getElementById('setup-error');
  const searchBtn = document.querySelector('#setup-modal .setup-search-row button');

  // Guard against missing DOM elements (should never happen, but catches cached-JS bugs)
  if (!inputEl || !results || !err) {
    alert('Sidsfel: försök ladda om sidan (Ctrl+Shift+R)');
    return;
  }

  const zip = inputEl.value.trim().replace(/\s/g, '');
  if (!zip) {
    err.textContent = 'Ange ett postnummer, t.ex. 37100';
    return;
  }

  // Reset confirm button and previous selection
  const confirmBtn = document.getElementById('store-confirm-btn');
  if (confirmBtn) { confirmBtn.classList.remove('visible'); confirmBtn.disabled = false; }
  document.querySelectorAll('.store-option.selected').forEach(s => s.classList.remove('selected'));
  _pendingStore = null;

  // Immediate visual feedback
  err.textContent = '';
  results.innerHTML = '<p style="color:#15803d;font-size:15px;text-align:center;padding:16px 0">⏳ Söker butiker nära <strong>' + zip + '</strong>…</p>';
  if (searchBtn) { searchBtn.disabled = true; searchBtn.textContent = 'Söker…'; }

  try {
    const r    = await fetch('/api/stores/search?zip=' + encodeURIComponent(zip));
    const data = await r.json();

    if (!r.ok) {
      results.innerHTML = '';
      err.textContent = '❌ ' + (data.error || 'Sökning misslyckades — försök igen');
      return;
    }
    if (!Array.isArray(data) || !data.length) {
      results.innerHTML = '<p style="color:#6b7280;font-size:14px;padding:8px 0">Inga butiker hittades inom 15 km av ' + zip + '</p>';
      return;
    }
    results.innerHTML = data.map(s => {
      const distTxt = s.dist_m < 1000 ? s.dist_m + ' m' : (s.dist_m / 1000).toFixed(1) + ' km';
      const escAttr = v => String(v || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      return '<div class="store-option" onclick="_highlightStore(this)"'
        + ' data-id="' + escAttr(s.id) + '"'
        + ' data-name="' + escAttr(s.name) + '"'
        + ' data-addr="' + escAttr(s.address || '') + '"'
        + ' data-chain="' + escAttr(s.chain || 'other') + '"'
        + ' data-chain-store-id="' + escAttr(s.chain_store_id || '') + '">'
        + '<div style="display:flex;align-items:center;gap:8px">'
        + _chainBadge(s.chain || 'other')
        + '<span class="store-name">' + s.name + '</span>'
        + '<span style="margin-left:auto;font-size:12px;color:#6b7280">' + distTxt + '</span>'
        + '</div>'
        + '<div class="store-addr">' + (s.address || '') + '</div>'
        + '</div>';
    }).join('');
  } catch (e) {
    results.innerHTML = '';
    err.textContent = '❌ Nätverksfel: ' + e.message;
  } finally {
    if (searchBtn) { searchBtn.disabled = false; searchBtn.textContent = 'Sök'; }
  }
}

let _pendingStore = null;

function _highlightStore(el) {
  // Read store data from data attributes (avoids HTML quoting issues)
  const id           = el.dataset.id;
  const name         = el.dataset.name;
  const address      = el.dataset.addr;
  const chain        = el.dataset.chain;
  const chainStoreId = el.dataset.chainStoreId || '';

  document.querySelectorAll('.store-option.selected').forEach(s => s.classList.remove('selected'));
  el.classList.add('selected');
  _pendingStore = { id, name, address, chain, chainStoreId };

  const btn = document.getElementById('store-confirm-btn');
  if (btn) {
    btn.textContent = '✅ Välj ' + name;
    btn.classList.add('visible');
    btn.disabled = false;
    btn.onclick = () => confirmStore(id, name, address, chain, chainStoreId);
  }
  btn?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function confirmStore(id, name, address, chain, chainStoreId) {
  const btn = document.getElementById('store-confirm-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Sparar…'; }
  try {
    const r = await fetch('/api/setup/store', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ store_id: id, store_name: name, store_address: address, chain, chain_store_id: chainStoreId || null }),
    });
    if (r.ok) {
      showToast('Butik vald: ' + name, 'success');
      setTimeout(() => location.reload(), 800);
    } else {
      const d = await r.json();
      document.getElementById('setup-error').textContent = d.error || 'Fel vid sparande.';
      if (btn) { btn.disabled = false; btn.textContent = '✅ Välj ' + name; }
    }
  } catch(e) {
    document.getElementById('setup-error').textContent = 'Misslyckades: ' + e.message;
    if (btn) { btn.disabled = false; btn.textContent = '✅ Välj ' + name; }
  }
}

// ── Settings ─────────────────────────────────────────────────────────────────

function openSettings() {
  fetch('/api/settings').then(r => r.json()).then(s => {
    document.getElementById('s-adults').value        = s.adults ?? 2;
    document.getElementById('s-kids-count').value    = s.kids_count ?? 2;
    document.getElementById('s-recipes-week').value  = s.recipes_per_week ?? 5;
    document.getElementById('s-budget').value        = s.budget_sek ?? '';
    document.getElementById('s-fast-days').value     = s.fast_days ?? 2;
    document.getElementById('s-medium-days').value   = s.medium_days ?? 2;
    document.getElementById('s-long-days').value     = s.long_days ?? 1;
    document.getElementById('s-dessert').checked     = s.include_dessert ?? false;
    document.getElementById('s-starter').checked     = s.include_starter ?? false;
    document.getElementById('s-festive').checked     = s.festive_meals ?? false;
    document.getElementById('s-allergies').value     = (s.allergies || []).join(', ');
    document.getElementById('s-exclude').value       = (s.exclude_items || []).join(', ');
    const diet = s.diet || ['vegetarian', 'chicken', 'fish'];
    document.querySelectorAll('.diet-toggle').forEach(b => b.classList.toggle('active', diet.includes(b.dataset.diet)));
    document.getElementById('settings-modal').style.display = 'flex';
  }).catch(() => document.getElementById('settings-modal').style.display = 'flex');
}

function closeSettings() {
  document.getElementById('settings-modal').style.display = 'none';
}

function toggleDiet(btn) { btn.classList.toggle('active'); }

async function saveSettings() {
  const diet     = Array.from(document.querySelectorAll('.diet-toggle.active')).map(b => b.dataset.diet);
  const allergies = document.getElementById('s-allergies').value.split(',').map(s => s.trim()).filter(Boolean);
  const exclude   = document.getElementById('s-exclude').value.split(',').map(s => s.trim()).filter(Boolean);
  const settings  = {
    adults:          parseInt(document.getElementById('s-adults').value)        || 2,
    kids_count:      parseInt(document.getElementById('s-kids-count').value)    || 0,
    recipes_per_week:parseInt(document.getElementById('s-recipes-week').value)  || 5,
    budget_sek:      parseInt(document.getElementById('s-budget').value)        || null,
    fast_days:       parseInt(document.getElementById('s-fast-days').value)     || 2,
    medium_days:     parseInt(document.getElementById('s-medium-days').value)   || 2,
    long_days:       parseInt(document.getElementById('s-long-days').value)     || 1,
    include_dessert: document.getElementById('s-dessert').checked,
    include_starter: document.getElementById('s-starter').checked,
    festive_meals:   document.getElementById('s-festive').checked,
    diet, allergies, exclude_items: exclude,
  };
  const saveBtn = document.getElementById('settings-save-btn');
  saveBtn.disabled = true; saveBtn.textContent = '⏳ Sparar…';
  try {
    const r = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    if (r.ok) { closeSettings(); showToast('Inställningar sparade', 'success'); }
    else { const d = await r.json(); showToast('Fel: ' + (d.error || 'Okänt fel'), 'error'); }
  } finally { saveBtn.disabled = false; saveBtn.textContent = 'Spara'; }
}

// ── Weekly planning ───────────────────────────────────────────────────────────

async function planNextWeek() {
  const btn = document.getElementById('plan-next-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Planerar…'; }
  try {
    const r = await fetch('/api/plan/next-week', { method: 'POST' });
    const d = await r.json();
    if (r.ok) { showToast('Nästa vecka planerad!', 'success'); setTimeout(() => location.reload(), 1000); }
    else {
      showToast('Fel: ' + (d.error || 'Okänt fel'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = '📅 Planera nästa vecka'; }
    }
  } catch(e) {
    showToast('Nätverksfel', 'error');
    if (btn) { btn.disabled = false; btn.textContent = '📅 Planera nästa vecka'; }
  }
}

async function swapRecipe(recipeIdx) {
  const btn = document.getElementById('swap-btn-' + recipeIdx);
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await fetch('/api/plan/swap-recipe', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recipe_index: recipeIdx }),
    });
    const d = await r.json();
    if (r.ok) { showToast('Recept bytt!', 'success'); setTimeout(() => location.reload(), 800); }
    else {
      showToast('Fel: ' + (d.error || 'Okänt fel'), 'error');
      if (btn) { btn.disabled = false; btn.textContent = '🔄 Byt'; }
    }
  } catch(e) {
    showToast('Nätverksfel', 'error');
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Byt'; }
  }
}

function openOccasion() { document.getElementById('occasion-modal').style.display = 'flex'; }
function closeOccasion() { document.getElementById('occasion-modal').style.display = 'none'; }

async function saveOccasion() {
  const description = document.getElementById('occasion-desc').value.trim();
  const weeks_ahead = parseInt(document.getElementById('occasion-weeks').value) || 0;
  if (!description) { closeOccasion(); return; }
  const r = await fetch('/api/plan/set-occasion', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ description, weeks_ahead }),
  });
  if (r.ok) { closeOccasion(); showToast('Speciellt tillfälle sparat!', 'success'); }
  else { showToast('Fel vid sparande', 'error'); }
}

// ── Plan generation ────────────────────────────────────────────────────────────

async function generatePlan(btn, force) {
  btn.disabled = true;
  btn.textContent = '⏳ Genererar…';
  const status = document.getElementById('gen-status');
  if (status) status.textContent = 'Hämtar erbjudanden och väljer recept...';
  try {
    const r = await fetch('/api/plan/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: !!force }),
    });
    const d = await r.json();
    if (r.ok) {
      if (status) status.textContent = `✅ ${d.recipe_count} recept, ${d.item_count} varor. Laddar om...`;
      setTimeout(() => location.reload(), 1500);
    } else {
      if (status) status.textContent = '❌ ' + (d.error || 'Misslyckades');
      btn.disabled = false;
      btn.textContent = '🔄 Försök igen';
    }
  } catch(e) {
    if (status) status.textContent = '❌ Nätverksfel';
    btn.disabled = false;
    btn.textContent = '🔄 Försök igen';
  }
}

function reloadShoppingList() {
  // Simple page reload for now
  location.reload();
}

// ── Toast ────────────────────────────────────────────────────────────────────

function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (type ? ' ' + type : '');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.className = 'toast hidden'; }, 2500);
}

// ── API helpers ───────────────────────────────────────────────────────────────

async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.status === 401) { window.location.href = '/login'; throw new Error('Ej inloggad'); }
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── Shopping list ─────────────────────────────────────────────────────────────

function toggleItem(id, checked) {
  post('/api/shopping/toggle', { id, checked })
    .then(() => {
      const el = document.getElementById('item-' + id);
      if (el) el.classList.toggle('checked', checked);
      updateProgress();
    })
    .catch(e => showToast('Fel: ' + e.message, 'error'));
}

function removeItem(id, btn) {
  post('/api/shopping/remove', { id })
    .then(() => {
      const el = document.getElementById('item-' + id);
      if (el) {
        el.style.opacity = '0';
        setTimeout(() => { el.remove(); updateProgress(); }, 200);
      }
    })
    .catch(e => showToast('Fel: ' + e.message, 'error'));
}

function addItem(prefillItem, prefillQty) {
  const nameEl = document.getElementById('new-item');
  const qtyEl = document.getElementById('new-qty');
  const item = (prefillItem !== undefined ? prefillItem : nameEl.value).trim();
  const quantity = (prefillQty !== undefined ? prefillQty : qtyEl.value).trim();

  if (!item) { nameEl.focus(); return; }

  post('/api/shopping/add', { item, quantity })
    .then(data => {
      appendShoppingItem({ id: data.id, item, quantity, on_sale: false, checked: false, added_manually: true });
      if (prefillItem === undefined) { nameEl.value = ''; qtyEl.value = ''; nameEl.focus(); }
      showToast(item + ' tillagd', 'success');
      updateProgress();
    })
    .catch(e => showToast('Fel: ' + e.message, 'error'));
}

function addFrequent(itemName) {
  document.getElementById('new-item').value = itemName;
  document.getElementById('new-qty').focus();
}

function appendShoppingItem(item) {
  // Find or create "övrigt" category
  let list = document.getElementById('shopping-list');
  let group = list.querySelector('.category-group[data-cat="övrigt"]');
  if (!group) {
    group = document.createElement('div');
    group.className = 'category-group';
    group.dataset.cat = 'övrigt';
    group.innerHTML = '<div class="category-header">ÖVRIGT</div>';
    list.appendChild(group);
  }

  const div = document.createElement('div');
  div.id = 'item-' + item.id;
  div.dataset.id = item.id;
  div.className = 'shopping-item';
  div.innerHTML = `
    <label class="item-label">
      <input type="checkbox" onchange="toggleItem(${item.id}, this.checked)">
      <span class="item-name">
        ${item.quantity ? `<span class="item-qty">${item.quantity}</span>` : ''}
        ${item.item}
        <span class="manual-badge">+</span>
      </span>
    </label>
    <button class="remove-btn" onclick="removeItem(${item.id}, this)" title="Ta bort">×</button>
  `;
  group.appendChild(div);
}

function updateProgress() {
  const items = document.querySelectorAll('.shopping-item');
  const checked = document.querySelectorAll('.shopping-item.checked').length;
  const total = items.length;

  const bar = document.querySelector('.progress-bar');
  const text = document.querySelector('.progress-text');
  if (bar && total > 0) {
    bar.style.width = Math.round(checked / total * 100) + '%';
  }
  if (text) {
    text.textContent = checked + ' / ' + total + ' bockat';
  }
}

// Enter key handlers
document.addEventListener('DOMContentLoaded', () => {
  const ni = document.getElementById('new-item');
  const nq = document.getElementById('new-qty');
  if (ni) ni.addEventListener('keydown', e => { if (e.key === 'Enter') addItem(); });
  if (nq) nq.addEventListener('keydown', e => { if (e.key === 'Enter') addItem(); });
  const si = document.getElementById('catalog-search-input');
  if (si) si.addEventListener('keydown', e => { if (e.key === 'Enter') searchRecipes(); });
});

// ── Recipe ratings (1-7 scale) ────────────────────────────────────────────────

function rateRecipe(recipeId, rating, btn) {
  post('/api/recipe/rate', { recipe_id: recipeId, rating })
    .then(() => {
      const section = btn.closest('.rating-section');
      section.querySelectorAll('.rate-btn-7').forEach(b => b.classList.remove('rate-active'));
      btn.classList.add('rate-active');
      let label = section.querySelector('.current-rating');
      if (!label) {
        label = document.createElement('span');
        label.className = 'current-rating';
        section.appendChild(label);
      }
      label.textContent = `Betyg: ${rating}/7`;
      showToast(`Betyg ${rating}/7 sparat!`, 'success');
    })
    .catch(() => showToast('Kunde inte spara betyg', 'error'));
}

// ── Recipe catalog search ─────────────────────────────────────────────────────

const DIET_ICONS = { vegetarian: '🥦', chicken: '🍗', fish: '🐟' };

function searchRecipes() {
  const q = document.getElementById('catalog-search-input').value.trim();
  if (!q) return;
  const resultsEl = document.getElementById('catalog-search-results');
  resultsEl.innerHTML = '<p class="search-loading">Söker...</p>';

  fetch('/api/recipes/search?q=' + encodeURIComponent(q))
    .then(r => r.json())
    .then(results => {
      if (!results.length) {
        resultsEl.innerHTML = '<p class="search-empty">Inga recept hittades för dessa ingredienser.</p>';
        return;
      }
      resultsEl.innerHTML = results.map(r => {
        const icon = DIET_ICONS[r.diet_type] || '🍽️';
        const rating = r.site_rating ? `⭐ ${r.site_rating.toFixed(1)}` : '';
        const ki = (r.key_ingredients || []).join(', ');
        return `<div class="catalog-card">
          <a class="catalog-name" href="${r.url}" target="_blank" rel="noopener">${icon} ${r.name}</a>
          <div class="catalog-meta">
            ${rating} <span class="catalog-source">${r.source || ''}</span>
          </div>
          ${ki ? `<div class="catalog-ki">${ki}</div>` : ''}
        </div>`;
      }).join('');
    })
    .catch(() => {
      resultsEl.innerHTML = '<p class="search-error">Sökning misslyckades.</p>';
    });
}

// ── Smart suggestions ─────────────────────────────────────────────────────────

function addSuggestionToList(item, btn) {
  post('/api/shopping/add', { item, quantity: '' })
    .then(data => {
      const chip = btn.closest('.staple-chip');
      if (chip) chip.remove();
      showToast(item + ' tillagd i listan', 'success');
      reloadShoppingList();
    })
    .catch(e => showToast('Fel: ' + e.message, 'error'));
}

// ── Watchlist ─────────────────────────────────────────────────────────────────

function loadWatchlist() {
  const container = document.getElementById('watchlist-items');
  if (!container) return;
  fetch('/api/watchlist')
    .then(r => r.json())
    .then(items => {
      if (!items.length) {
        container.innerHTML = '<p class="search-empty">Inga bevakningar ännu.</p>';
        return;
      }
      container.innerHTML = items.map(item => `
        <div class="watchlist-item">
          <span>
            <span class="watchlist-item-name">${item.display_name}</span>
            <span class="watchlist-item-query">(${item.query})</span>
          </span>
          <button class="watchlist-remove-btn" onclick="removeFromWatchlist('${item.query.replace(/'/g, "\\'")}', this)" title="Ta bort">×</button>
        </div>
      `).join('');
    })
    .catch(() => {});
}

function addToWatchlist() {
  const queryEl = document.getElementById('watchlist-query');
  const nameEl = document.getElementById('watchlist-name');
  const query = queryEl.value.trim();
  const display_name = nameEl.value.trim() || query;
  if (!query) { queryEl.focus(); return; }

  post('/api/watchlist/add', { query, display_name })
    .then(() => {
      queryEl.value = '';
      nameEl.value = '';
      showToast(display_name + ' bevakad!', 'success');
      loadWatchlist();
    })
    .catch(e => showToast('Fel: ' + e.message, 'error'));
}

function removeFromWatchlist(query, btn) {
  post('/api/watchlist/remove', { query })
    .then(() => {
      const item = btn.closest('.watchlist-item');
      if (item) item.remove();
      showToast('Bevakning borttagen', 'success');
    })
    .catch(e => showToast('Fel: ' + e.message, 'error'));
}

document.addEventListener('DOMContentLoaded', () => {
  loadWatchlist();
  const wq = document.getElementById('watchlist-query');
  const wn = document.getElementById('watchlist-name');
  if (wq) wq.addEventListener('keydown', e => { if (e.key === 'Enter') addToWatchlist(); });
  if (wn) wn.addEventListener('keydown', e => { if (e.key === 'Enter') addToWatchlist(); });
});

// ── Tab navigation ─────────────────────────────────────────────────────────────

function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  sessionStorage.setItem('activeTab', name);
}

// Restore last tab on page load
(function() {
  const saved = sessionStorage.getItem('activeTab');
  if (saved) {
    const btn = document.querySelector('.tab-btn[onclick*="\'' + saved + '\'"]');
    if (btn) {
      switchTab(saved, btn);
      if (saved === 'history') loadHistory();
    }
  }
})();

// ── History tab ──────────────────────────────────────────────────────────────

let _historyDebounce = null;

function loadHistory() {
  clearTimeout(_historyDebounce);
  _historyDebounce = setTimeout(_doLoadHistory, 250);
}

async function _doLoadHistory() {
  const name    = document.getElementById('history-name-filter').value.trim();
  const minR    = document.getElementById('history-min-rating').value;
  const maxR    = document.getElementById('history-max-rating').value;
  const params  = new URLSearchParams();
  if (name) params.set('name', name);
  if (minR) params.set('min_rating', minR);
  if (maxR) params.set('max_rating', maxR);

  const container = document.getElementById('history-results');
  container.innerHTML = '<p class="history-empty">Laddar…</p>';

  try {
    const resp = await fetch('/api/recipes/history?' + params.toString());
    const rows = await resp.json();
    if (!rows.length) {
      container.innerHTML = '<p class="history-empty">Inga betygsatta recept hittades.</p>';
      return;
    }
    container.innerHTML = rows.map(r => {
      const rNum  = r.rating || 0;
      const cls   = rNum <= 2 ? 'rating-low' : rNum <= 4 ? 'rating-mid' : rNum <= 6 ? 'rating-high' : 'rating-top';
      const stars = '★'.repeat(Math.round(rNum / 7 * 5));
      const week  = r.week_num ? `V${r.week_num} ${r.year}` : '';
      const cooked = r.last_cooked ? new Date(r.last_cooked).toLocaleDateString('sv-SE') : '';
      const meta  = [week, cooked ? `Lagad ${cooked}` : '', r.times_cooked > 1 ? `${r.times_cooked}× lagad` : '']
                      .filter(Boolean).join(' · ');
      const ki    = Array.isArray(r.key_ingredients) && r.key_ingredients.length
                      ? `<div class="history-ingredients">🥬 ${r.key_ingredients.slice(0,6).join(', ')}</div>` : '';
      const link  = r.source_url ? `<a href="${r.source_url}" target="_blank" rel="noopener" style="font-size:12px;color:#0369a1;margin-top:4px;display:block">Receptlänk →</a>` : '';
      return `
        <div class="history-card">
          <div class="history-card-top">
            <span class="history-card-name">${r.name}</span>
            <span class="history-rating-badge ${cls}">${rNum}/7 ${stars}</span>
          </div>
          <div class="history-card-meta">${meta}</div>
          ${ki}${link}
        </div>`;
    }).join('');
  } catch(e) {
    container.innerHTML = '<p class="history-empty">Kunde inte ladda historik.</p>';
  }
}

// ── Order Agent settings (Phase 6) ────────────────────────────────────────────

async function loadOrderSettings() {
  try {
    const r = await fetch('/api/order/settings');
    if (!r.ok) return;
    const s = await r.json();
    document.getElementById('order-chain').value = s.chain || 'willys';
    document.getElementById('order-store-id').value = s.store_id || '';
    document.getElementById('order-ntfy-topic').value = s.ntfy_topic || '';
    document.getElementById('order-slot-weekday').value = s.preferred_slot_weekday || 6;
    document.getElementById('order-slot-hour').value = s.preferred_slot_hour || 11;
    // Never populate password fields
    loadOrderHistory();
  } catch(e) { /* ignore */ }
}

async function saveOrderSettings() {
  const body = {
    chain: document.getElementById('order-chain').value,
    store_id: document.getElementById('order-store-id').value.trim(),
    ntfy_topic: document.getElementById('order-ntfy-topic').value.trim(),
    preferred_slot_weekday: parseInt(document.getElementById('order-slot-weekday').value),
    preferred_slot_hour: parseInt(document.getElementById('order-slot-hour').value),
    username: document.getElementById('order-username').value.trim(),
    password: document.getElementById('order-password').value,
  };
  try {
    const r = await fetch('/api/order/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await r.json();
    showToast(data.message || (r.ok ? 'Sparat' : 'Fel'));
    if (r.ok) {
      document.getElementById('order-password').value = '';
    }
  } catch(e) { showToast('Kunde inte spara'); }
}

async function triggerOrderAgent(liveRun) {
  const statusDiv = document.getElementById('order-job-status');
  const msgDiv = document.getElementById('order-job-msg');
  statusDiv.style.display = 'block';
  msgDiv.textContent = liveRun ? '🛒 Startar beställning…' : '🧪 Kör dry-run…';

  try {
    const r = await fetch('/api/order/fill-carts', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ dry_run: !liveRun }),
    });
    const data = await r.json();
    if (!r.ok) {
      msgDiv.textContent = `❌ ${data.message || 'Fel'}`;
      return;
    }
    const jobId = data.job_id;
    msgDiv.textContent = `⏳ Jobbet körs (ID: ${jobId})…`;
    // Poll for completion
    const poll = setInterval(async () => {
      try {
        const pr = await fetch(`/api/order/status/${jobId}`);
        const pdata = await pr.json();
        if (pdata.status === 'done') {
          clearInterval(poll);
          const res = pdata.result || {};
          if (res.success) {
            msgDiv.textContent = `✅ Klar! ${res.item_count} varor · ${res.total_sek?.toFixed(0)} kr${res.slot_time ? ' · ' + res.slot_time : ''}`;
          } else {
            msgDiv.textContent = `❌ Fel: ${res.error || 'okänt'}`;
          }
          loadOrderHistory();
        }
      } catch(e) { clearInterval(poll); }
    }, 3000);
  } catch(e) { msgDiv.textContent = '❌ Kunde inte starta'; }
}

async function loadOrderHistory() {
  const container = document.getElementById('order-history-list');
  if (!container) return;
  try {
    const r = await fetch('/api/order/history');
    if (!r.ok) { container.innerHTML = ''; return; }
    const rows = await r.json();
    if (!rows.length) {
      container.innerHTML = '<p style="color:#999;font-size:14px">Ingen historik ännu.</p>';
      return;
    }
    container.innerHTML = rows.map(row => {
      const statusIcon = row.status === 'pending' ? '✅' : row.status === 'failed' ? '❌' : '⏳';
      const total = row.total_sek ? `${row.total_sek.toFixed(0)} kr` : '—';
      const slot = row.pickup_slot || '';
      const date = row.created_at ? row.created_at.slice(0, 16) : '';
      const link = row.checkout_url
        ? `<a href="${row.checkout_url}" target="_blank" rel="noopener" style="font-size:12px;color:#0369a1">Betala →</a>`
        : '';
      return `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #e5e7eb;font-size:14px">
        <span>${statusIcon} V${row.week_num}/${row.year} · ${row.chain} · ${total}</span>
        <span style="color:#666">${slot} ${link}</span>
        <span style="color:#999;font-size:12px">${date}</span>
      </div>`;
    }).join('');
  } catch(e) { container.innerHTML = ''; }
}
