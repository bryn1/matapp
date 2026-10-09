// ============================================================================
// ui/profile.js — Profile view (auth-skyddad /api/profile GET|PUT) — render-only
// (Phase 8 T7 frontend, gate C7)
// ============================================================================
// MC 10375 (P1-1 + P1-2): store checkboxes are rendered from the served catalog
// (GET /api/stores — the motor's list, no second UI-side list) PRE-CHECKED from
// the profile's selected_stores, and the PUT sends the REAL checked array. The
// old hardcoded `selected_stores: []` silently unpinned every store on each
// profile save (PUT replaces this field: absent/[] clears — live contract).
//
// Loads the authenticated user's profile (persons, meal_days, kron_budget,
// selected_stores) via GET /api/profile and renders an edit form; on submit PUTs
// it back. 404 (no profile yet) renders the same empty form. 401 is handled by
// the auth view (this view just reports "not logged in"). Render-only: draws
// the form from backend data, never computes household state itself.
// ============================================================================

const profileModule = (() => {
  // MC 1355.16: absent-means-unchanged PUT semantics — postal_code is only
  // included in the PUT body when the user actually edited the field.
  let postalEdited = false;
  // MC 1355.18 (T11): same absent-means-unchanged tracking for the new fields.
  let childrenEdited = false;
  let kidEdited = false;

  // ---- Data accessors (render-only) ----
  async function fetchProfile() {
    const res = await apiGet(Endpoints.profile);
    return await res.json(); // {persons, meal_days, kron_budget, selected_stores, postal_code, resolved_stores}
  }

  async function saveProfile(data) {
    const res = await apiPut(Endpoints.profile, data);
    return await res.json(); // {saved, profile_id, profile:{...}}
  }

  // ---- Store selection (MC 10375 P1-2) ----
  // The checkbox list is rendered from the motor-served catalog (GET /api/stores,
  // anonymous read) — the UI never keeps a second store list (O1). The server
  // rule is ONE (profile_service.validate_selected_stores: cap 3, no duplicates,
  // catalog-checked); the client cap below is UX only and the authoritative 422
  // is surfaced verbatim-ish in the status line, never swallowed.
  const MAX_STORES = 3;
  let storesFailed = false;   // GET /api/stores failed → honest caption, no fake boxes
  let selectedSnapshot = [];  // last server-known selection (pre-check + safe save)

  async function fetchStoreCatalog() {
    let catalog = [];
    try {
      const res = await apiGet(Endpoints.stores);
      catalog = await res.json(); // [{store_id, name, chain_type, enabled}]
    } catch (err) {
      console.error('Store catalog load failed:', err);
      storesFailed = true;
    }
    renderStoreCheckboxes(catalog);
  }

  function renderStoreCheckboxes(catalog) {
    const form = document.getElementById('profile-form');
    if (!form || document.getElementById('profile-stores')) return;
    // Dead catalog + saved selection: rebuild the boxes from what IS known (the
    // user's own ids) so saving can never drop it. Dead catalog + no selection:
    // zero boxes + honest failure caption — never fabricated checkboxes.
    const stores = (catalog && catalog.length) ? catalog
      : selectedSnapshot.map((sid) => ({ store_id: sid, name: sid }));

    const fieldset = document.createElement('fieldset');
    fieldset.id = 'profile-stores';
    fieldset.className = 'form-field';
    const legend = document.createElement('legend');
    legend.textContent = 'Butiker';
    fieldset.appendChild(legend);

    for (const store of stores) {
      const boxId = 'profile-store-' + store.store_id;
      const wrap = document.createElement('div');
      wrap.className = 'form-field';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.id = boxId;
      input.name = 'profile-store';
      input.value = store.store_id;
      const label = document.createElement('label');
      label.setAttribute('for', boxId); // real label association; native keyboard operation
      label.textContent = store.name || store.store_id;
      wrap.appendChild(input);
      wrap.appendChild(label);
      fieldset.appendChild(wrap);
    }

    const help = document.createElement('p');
    help.className = 'form-help';
    help.textContent = (storesFailed && !stores.length)
      ? 'Butikslistan kunde inte laddas — ladda om sidan. Nuvarande val bevaras.'
      : `Välj vilka butikers erbjudanden som räknas (max ${MAX_STORES}).`;
    fieldset.appendChild(help);

    const caption = document.createElement('p');
    caption.id = 'profile-stores-caption';
    caption.className = 'form-help';
    caption.setAttribute('role', 'status');
    fieldset.appendChild(caption);

    // One delegated change listener for all boxes: cap guard + honest caption.
    fieldset.addEventListener('change', (ev) => {
      if (!ev.target.matches('input[name="profile-store"]')) return;
      if (checkedStoreIds().length > MAX_STORES) {
        ev.target.checked = false;
        caption.textContent = `Max ${MAX_STORES} butiker kan väljas — avmarkera en först.`;
        return;
      }
      updateStoresCaption();
    });

    const anchor = document.getElementById('profile-resolved')
      || document.getElementById('profile-status');
    form.insertBefore(fieldset, anchor || null);
    precheckStores(selectedSnapshot);
  }

  function checkedStoreIds() {
    return Array.from(
      document.querySelectorAll('#profile-stores input[name="profile-store"]:checked'),
      (box) => box.value);
  }

  function precheckStores(selected) {
    const sel = new Set(selected || []);
    for (const box of document.querySelectorAll('#profile-stores input[name="profile-store"]')) {
      box.checked = sel.has(box.value);
    }
    updateStoresCaption();
  }

  // empty = all: the caption states the live backend semantics, nothing here
  // changes them.
  function updateStoresCaption() {
    const caption = document.getElementById('profile-stores-caption');
    if (!caption) return;
    const checked = checkedStoreIds();
    caption.textContent = checked.length === 0
      ? 'Inga butiker valda — erbjudanden från alla butiker räknas.'
      : `Valda butiker: ${checked.join(', ')} — erbjudandena från dessa räknas.`;
  }

  // 422 → honest Swedish copy, read from the real detail (the auth.js
  // registerErrorMessage idiom — never mislabel one failure as another).
  async function saveErrorMessage(err) {
    if (!err || !err.response || err.response.status !== 422) {
      return 'Kunde inte spara profilen.';
    }
    let detail = '';
    try {
      const body = await err.response.json();
      detail = body && typeof body.detail === 'string' ? body.detail : '';
    } catch (e) { /* body unreadable — fall through to the class default */ }
    if (/at most/i.test(detail)) {
      const cap = detail.match(/at most (\d+)/);
      return `Max ${cap ? cap[1] : MAX_STORES} butiker kan väljas — avmarkera en och spara igen.`;
    }
    if (/duplicate/i.test(detail)) return 'Samma butik kunde inte väljas två gånger.';
    if (/unknown store_id/i.test(detail)) return 'En vald butik finns inte i butikslistan — ladda om och försök igen.';
    return detail ? `Kunde inte spara profilen: ${detail}` : 'Kunde inte spara profilen.';
  }

  // ---- Fill the form from a profile object ----
  function fillForm(profile) {
    const personsEl = document.getElementById('profile-persons');
    const mealDaysEl = document.getElementById('profile-meal-days');
    const budgetEl = document.getElementById('profile-budget');
    const postalEl = document.getElementById('profile-postal');
    const statusEl = document.getElementById('profile-status');

    if (personsEl) personsEl.value = profile && profile.persons != null ? profile.persons : 2;
    if (mealDaysEl) mealDaysEl.value = profile && profile.meal_days != null ? profile.meal_days : 5;
    if (budgetEl) budgetEl.value = profile && profile.kron_budget != null ? profile.kron_budget : '';
    if (postalEl) postalEl.value = (profile && profile.postal_code) || '';
    postalEdited = false;
    const childrenEl = document.getElementById('profile-num-children');
    const kidEl = document.getElementById('profile-kid-friendly');
    if (childrenEl) {
      childrenEl.value = (profile && profile.num_children != null) ? profile.num_children : '';
    }
    if (kidEl) kidEl.checked = !!(profile && profile.prefer_kid_friendly);
    childrenEdited = false;
    kidEdited = false;
    renderResolved(profile && profile.resolved_stores);
    // MC 10375: pre-check the user's current selection (no-op if the boxes are
    // not rendered yet — renderStoreCheckboxes pre-checks on creation).
    selectedSnapshot = (profile && Array.isArray(profile.selected_stores))
      ? profile.selected_stores.slice() : [];
    precheckStores(selectedSnapshot);
    if (statusEl) {
      statusEl.textContent = profile ? 'Profil laddad.' : 'Ingen profil än — fyll i och spara för att skapa en.';
      statusEl.hidden = false;
    }
  }

  // ---- Render the persisted resolved stores (per-chain status + resolved_at) ----
  function renderResolved(resolved) {
    const el = document.getElementById('profile-resolved');
    if (!el) return;
    if (!resolved || !resolved.chains) { el.hidden = true; el.textContent = ''; return; }
    const lines = [];
    for (const [chain, entry] of Object.entries(resolved.chains)) {
      if (entry && entry.status === 'ok') {
        for (const s of entry.stores || []) {
          lines.push(`${s.store_name} (${chain}) · ${s.distance_km} km`);
        }
      } else {
        lines.push(`${chain}: kunde inte hämtas${entry && entry.error ? ` — ${entry.error}` : ''} — spara om profilen för att försöka igen.`);
      }
    }
    if (resolved.resolved_at) {
      lines.push(`Hämtat: ${resolved.resolved_at}`);
    }
    // T10f DA P3-1: store names and error strings come from third-party
    // responses — build the <p> nodes with textContent, never innerHTML.
    el.textContent = '';
    for (const line of lines) {
      const p = document.createElement('p');
      p.textContent = line;
      el.appendChild(p);
    }
    el.hidden = lines.length === 0;
  }

  // ---- Submit: collect + PUT ----
  async function submitProfile() {
    const persons = parseInt(document.getElementById('profile-persons')?.value, 10);
    const mealDays = parseInt(document.getElementById('profile-meal-days')?.value, 10);
    const budgetVal = document.getElementById('profile-budget')?.value;
    const kronBudget = budgetVal ? parseInt(budgetVal, 10) : 0;

    const data = {
      persons: Number.isNaN(persons) ? 2 : persons,
      meal_days: Number.isNaN(mealDays) ? 5 : mealDays,
      kron_budget: Number.isNaN(kronBudget) ? 0 : kronBudget,
      // MC 10375 P1-1: the REAL checked array — the old hardcoded [] silently
      // unpinned every store on profile save (PUT replaces this field;
      // absent/[] clears — the live contract, kept). If the checkbox block
      // never rendered, fall back to the last server-known selection: saving
      // the profile must never destroy it.
      selected_stores: document.getElementById('profile-stores')
        ? checkedStoreIds() : selectedSnapshot,
    };
    // Absent = unchanged (backend semantics); only an edit is sent. An edit
    // to empty sends null = explicit clear.
    if (postalEdited) {
      const postalVal = document.getElementById('profile-postal')?.value?.trim() || '';
      data.postal_code = postalVal === '' ? null : postalVal;
    }
    // MC 1355.18 (T11): antal barn — an edit to empty sends null = explicit
    // clear; barnvänligt — a checkbox has no empty state, so an edit always
    // sends 1 (checked) or 0 (unchecked) = an explicit preference (DA P3).
    if (childrenEdited) {
      const childrenVal = parseInt(document.getElementById('profile-num-children')?.value, 10);
      data.num_children = Number.isNaN(childrenVal) ? null : childrenVal;
    }
    if (kidEdited) {
      data.prefer_kid_friendly = document.getElementById('profile-kid-friendly')?.checked ? 1 : 0;
    }
    try {
      const res = await saveProfile(data);
      fillForm(res.profile || data);
      const statusEl = document.getElementById('profile-status');
      if (statusEl) { statusEl.textContent = 'Sparat!'; statusEl.hidden = false; }
    } catch (err) {
      console.error('Profile save failed:', err);
      const statusEl = document.getElementById('profile-status');
      if (statusEl) {
        statusEl.textContent = await saveErrorMessage(err); // 422 detail surfaced, never swallowed
        statusEl.hidden = false;
      }
    }
  }

  // ---- Load on enter ----
  async function load() {
    let profile = null;
    try {
      profile = await fetchProfile();
    } catch (err) {
      // 404 = none saved yet; 401 handled by auth view. Render empty form either way.
      console.error('Failed to load profile (404 ok):', err);
    }
    fillForm(profile);
  }

  function bindSubmit() {
    const form = document.getElementById('profile-form');
    if (form) form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      submitProfile();
    });
    // MC 1355.16: track edits so the PUT body carries postal_code only when
    // the user changed it (absent = unchanged backend semantics).
    const postalEl = document.getElementById('profile-postal');
    if (postalEl) postalEl.addEventListener('input', () => { postalEdited = true; });
    const childrenEl = document.getElementById('profile-num-children');
    if (childrenEl) childrenEl.addEventListener('input', () => { childrenEdited = true; });
    const kidEl = document.getElementById('profile-kid-friendly');
    if (kidEl) kidEl.addEventListener('change', () => { kidEdited = true; });
  }

  function init() {
    bindSubmit();
    fetchStoreCatalog();
    load();
  }

  return { init, load, submitProfile, fillForm };
})();

// Global alias (POC idiom): app.js bootstraps via window-scope module.
window.profileModule = profileModule;
