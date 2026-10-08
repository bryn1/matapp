// ============================================================================
// ui/shopping.js — Handelslista view (auth-skyddad /api/shopping*) — MC 10349
// ============================================================================
// The journey's end point (audit finding 2: the shopping APIs existed and
// worked; the UI never called them). Over the existing endpoints:
//   * GET  /api/shopping            rows grouped by category (server owns
//                                   normalization/categorization — render-only)
//   * checkbox                      -> POST /api/shopping/toggle {item}
//   * add form (item + qty text)    -> POST /api/shopping {item, quantity}
//   * "Ta bort"                     -> DELETE /api/shopping/{item}
//                                      (encodeURIComponent — names carry
//                                      spaces and Swedish letters)
//   * "Bygg från veckans meny"      -> POST /api/shopping/build; its 404 is
//     honest guidance, not an error banner: "Velj ett förslag under 3 förslag
//     först" (the accepted plan comes from the 3-förslag view).
//
// Rows render through DOM nodes + textContent, NEVER innerHTML: item names are
// user data (T10f DA P3-1 precedent). Source 'plan' rows expose the recipe
// dialog best-effort: build-from-plan aggregates INGREDIENT rows, so an
// ingredient that is not a recipe title honestly answers "Inget recept
// hittades" in the dialog rather than a fake recipe.
//
// Reuses app.css state-banner / empty-state / form / btn classes (no new CSS).
// ============================================================================

const shoppingModule = (() => {
  const SOURCE_LABEL = { plan: 'från meny', staple: 'basvara', memory: 'vana', manual: 'manuell' };

  // ---- Banner + status helpers (same shape as the suggestions view) ----
  function setBanner(which) {
    const loading = document.getElementById('shopping-loading');
    const error = document.getElementById('shopping-error');
    if (loading) loading.hidden = which !== 'loading';
    if (error) error.hidden = which !== 'error';
  }

  function showError(message) {
    const text = document.getElementById('shopping-error-text');
    const el = document.getElementById('shopping-error');
    if (text) text.textContent = message;
    if (el) el.hidden = false;
  }

  function setStatus(message) {
    const el = document.getElementById('shopping-status');
    if (!el) return;
    el.textContent = message || '';
    el.hidden = !message;
  }

  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (className) el.className = className;
    return el;
  }

  // ---- Data access ----
  async function fetchRows() {
    const res = await apiGet(Endpoints.shopping);
    return await res.json(); // [{item, quantity, category, checked, source}]
  }

  // ---- Rendering (grouped by category, alphabetical sv) ----
  function grouped(rows) {
    const byCat = new Map();
    for (const row of rows) {
      const cat = row.category || 'övrigt';
      if (!byCat.has(cat)) byCat.set(cat, []);
      byCat.get(cat).push(row);
    }
    return [...byCat.entries()].sort((a, b) => a[0].localeCompare(b[0], 'sv'));
  }

  function renderRow(row) {
    const li = document.createElement('li');
    li.className = 'suggestion__day';

    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !!row.checked;
    box.dataset.item = row.item;
    box.setAttribute('aria-label', `Bocka i avklarad: ${row.item}`);
    li.appendChild(box);

    // Plan-sourced rows open the recipe dialog on their name (see header:
    // best-effort — ingredients honestly 404). Others stay plain text.
    let name;
    if (row.source === 'plan' && window.recipeModule) {
      name = document.createElement('button');
      name.type = 'button';
      name.className = 'dish-link';
      name.dataset.dish = row.item;
      name.setAttribute('aria-label', `Läs recept (om det finns): ${row.item}`);
    } else {
      name = document.createElement('span');
      name.className = 'suggestion__dish';
    }
    name.textContent = row.item;
    li.appendChild(name);

    const del = node('button', 'Ta bort', 'btn btn-ghost btn-sm');
    del.type = 'button';
    del.dataset.delItem = row.item;
    del.setAttribute('aria-label', `Ta bort ${row.item} från listan`);
    li.appendChild(del);

    const detail = [row.quantity, SOURCE_LABEL[row.source] || row.source]
      .filter(Boolean).join(' · ');
    if (detail) li.appendChild(node('span', detail, 'suggestion__extra'));
    return li;
  }

  function render(rows) {
    const list = document.getElementById('shopping-list');
    const countEl = document.getElementById('shopping-count');
    const emptyEl = document.getElementById('shopping-empty');
    if (!list) return;
    list.textContent = '';
    if (countEl) countEl.textContent = rows.length ? `${rows.length} varor` : '';
    if (emptyEl) emptyEl.hidden = rows.length > 0;
    for (const [category, group] of grouped(rows)) {
      const card = document.createElement('article');
      card.className = 'suggestion-card';
      card.appendChild(node('h3', category, 'suggestion-card__index'));
      const ul = document.createElement('ul');
      ul.className = 'suggestion-card__days';
      for (const row of group) ul.appendChild(renderRow(row));
      card.appendChild(ul);
      list.appendChild(card);
    }
  }

  // ---- Load / refresh ----
  async function load() {
    setBanner('loading');
    try {
      const rows = await fetchRows();
      setBanner('ready');
      render(rows);
    } catch (err) {
      console.error('Failed to load shopping list:', err);
      setBanner('error');
    }
  }

  async function refresh() {
    try {
      render(await fetchRows());
    } catch (err) {
      console.error('Shopping refresh failed:', err);
      showError('Kunde inte hämta handelslistan. Kontrollera att du är inloggad och försök igen.');
    }
  }

  function failMutation(err) {
    console.error('Shopping mutation failed:', err);
    showError('Kunde inte uppdatera listan. Försök igen.');
  }

  // ---- Mutations (every success re-reads the server's list — render-only) ----
  async function toggleItem(item) {
    try {
      await apiPost(Endpoints.shoppingToggle, { item });
      await refresh();
    } catch (err) {
      failMutation(err);
      await refresh(); // show the true server state after a failed flip
    }
  }

  async function removeItem(item) {
    try {
      await apiDelete(`${Endpoints.shopping}/${encodeURIComponent(item)}`);
      await refresh();
    } catch (err) {
      failMutation(err);
    }
  }

  async function submitAdd(ev) {
    ev.preventDefault();
    const itemEl = document.getElementById('shopping-add-item');
    const qtyEl = document.getElementById('shopping-add-qty');
    const item = (itemEl && itemEl.value || '').trim();
    if (!item) {
      showError('Skriv in en vara först.');
      return;
    }
    try {
      await apiPost(Endpoints.shopping, { item, quantity: (qtyEl && qtyEl.value || '').trim() });
      if (itemEl) itemEl.value = '';
      if (qtyEl) qtyEl.value = '';
      await refresh();
    } catch (err) {
      console.error('Shopping add failed:', err);
      showError('Kunde inte lägga till varan. Försök igen.');
    }
  }

  async function buildFromPlan() {
    setStatus('Bygger från veckans meny...');
    try {
      const res = await apiPost(Endpoints.shoppingBuild, {});
      const body = await res.json(); // {ok, week, dishes, added, merged}
      setStatus(`Klart: ${body.added} nya varor, ${body.merged} slagna samman.`);
      await refresh();
    } catch (err) {
      const status = err && err.response ? err.response.status : 0;
      console.error('Shopping build failed:', err);
      if (status === 404) {
        // No accepted plan for the week — guidance, not an error banner.
        setStatus('Velj ett förslag under 3 förslag först');
        return;
      }
      setStatus('');
      failMutation(err);
    }
  }

  // ---- Wiring (delegated: rows are rebuilt on every render) ----
  function bindList() {
    const list = document.getElementById('shopping-list');
    if (!list) return;
    list.addEventListener('change', (ev) => {
      const box = ev.target.closest('input[type="checkbox"][data-item]');
      if (box) toggleItem(box.dataset.item);
    });
    list.addEventListener('click', (ev) => {
      const del = ev.target.closest('[data-del-item]');
      if (del) {
        removeItem(del.dataset.delItem);
        return;
      }
      const dish = ev.target.closest('[data-dish]');
      if (dish && window.recipeModule) recipeModule.open(dish.dataset.dish);
    });
  }

  function init() {
    bindList();
    const form = document.getElementById('shopping-add-form');
    if (form) form.addEventListener('submit', submitAdd);
    const buildBtn = document.getElementById('shopping-build');
    if (buildBtn) buildBtn.addEventListener('click', buildFromPlan);
  }

  return { init, load, refresh, render, toggleItem, removeItem, buildFromPlan };
})();

// Global alias (POC idiom): app.js bootstraps via window-scope module.
window.shoppingModule = shoppingModule;
