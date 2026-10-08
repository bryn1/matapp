// ============================================================================
// ui/suggestions.js — 3-förslag view (auth-/profil-skyddad /api/menu)
// (Phase 8 T7 frontend, gate C7; MC 10349 adds the owner's journey)
// ============================================================================
// Fetches /api/menu (the api-lager's headline endpoint, Phase 7 gate C6) with the
// session cookie and renders the THREE suggestions (families) that come back.
// Each suggestion is {week_key, seed, days[]}, each day {date, dish_id,
// andel_extrapris}. It draws the DOM from backend data and never computes a
// menu itself (REV2 invariant #2).
//
// MC 10349 (audit findings 2 + 7) adds the journey ON TOP of the cards:
//   * "Välj detta förslag" -> POST /api/menu/accept {seed, dishes: dish ids in
//     RENDER order}. 200 marks the card "Vald ✓" + status line; 409 (the plan
//     moved server-side, DA P1-A) auto-refetches GET /api/menu, re-renders and
//     says "Menyn uppdaterad — välj igen"; 422/other shows an honest error
//     banner (never a silent failure);
//   * GET /api/menu/accepted on load (404 = nothing accepted yet, not an
//     error) so the Vald mark survives a reload;
//   * dish titles are buttons opening the recipe dialog (ui/recipe.js);
//   * render() now lifts #suggestions-loading itself — the render-only version
//     never hid it, the confirmed-live bug behind card 10064.1.3.
//
// Prefix-aware via utils/api.js — every /api/* call resolves to /matapp/api/*
// under nginx (gate C7's "API-anrop mot /matapp/-prefixet").
// ============================================================================

const suggestionsModule = (() => {
  let lastData = null;       // last rendered /api/menu payload (accept echo)
  let acceptedSeed = null;   // seed of the week's accepted plan (null = none)

  // ---- Data accessor ----
  async function fetchSuggestions() {
    const res = await apiGet(Endpoints.menu);
    return await res.json(); // {week_key, suggestions:[{week_key, seed, days:[...]}]}
  }

  async function fetchAccepted() {
    // 404 = no accepted plan for the week: an expected STATE, not an error
    // (silence here — the browser still logs the resource itself, but the
    // module must not add a false stack trace on every cold view enter).
    // Other failures skip the mark honestly (additive UI must never block the
    // cards) and are logged.
    try {
      const res = await apiGet(Endpoints.menuAccepted);
      return await res.json(); // {week_key, seed, dishes[]}
    } catch (err) {
      if (err && err.response && err.response.status === 404) return null;
      console.error('Accepted-plan read failed (mark skipped):', err);
      return null;
    }
  }

  // ---- HTML escaping (T11 DA P2b) ----
  // The card renders through ONE innerHTML template, so every backend-sourced
  // string interpolated into it must be escaped here (dish title, seed, dates).
  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ---- Rendering: a single suggestion family ----
  // MC 1355.16: when the menu response carries offer_sources, append the
  // store scope to each day's line (display only; no logic).
  function renderSuggestion(sug, index, sourceNames) {
    const week = sug && sug.week_key ? sug.week_key : '';
    const seed = sug && sug.seed != null ? sug.seed : '';
    const days = Array.isArray(sug && sug.days) ? sug.days : [];
    const dishes = days.map((d) => {
      const pct = Math.round((d.andel_extrapris != null ? d.andel_extrapris : 0) * 100);
      const storeScopes = (d.used_offer_ids || [])
        .map((id) => sourceNames && sourceNames.get(id))
        .filter(Boolean);
      const storeLabel = storeScopes.length
        ? ` · butik: ${escapeHtml([...new Set(storeScopes)].join(', '))}` : '';
      // MC 1355.18 (T11): plain-text "Barnvänligt" badge next to the dish when
      // the day's recipe is kid-friendly; nothing renders when absent/false.
      const kidBadge = d.kid_friendly
        ? ' <span class="form-help">(Barnvänligt)</span>' : '';
      // MC 10349: the dish title is a button opening the recipe dialog. A day
      // with no dish id stays plain text ("Recept saknas" — nothing to read).
      const title = d.dish_id
        ? `<button type="button" class="dish-link" data-dish="${escapeHtml(d.dish_id)}" `
          + `aria-label="Läs receptet: ${escapeHtml(d.dish_id)}">${escapeHtml(d.dish_id)}</button>`
        : 'Recept saknas';
      return `<li class="suggestion__day">
        <span class="suggestion__dish">${title}${kidBadge}</span>
        <span class="suggestion__extra">${escapeHtml(d.date || '')} · ${pct}% extrapris${storeLabel}</span>
      </li>`;
    }).join('');

    // MC 10349: the accept control. The accepted seed's card shows "Vald ✓"
    // (the server-side accepted plan, re-read on every load); the others get
    // the button. The payload dishes come from lastData at click time — the
    // ids in RENDER order, exactly what the accept contract requires.
    const seedChosen = acceptedSeed != null && sug && sug.seed === acceptedSeed;
    const action = seedChosen
      ? '<span class="btn btn-primary">Vald ✓</span>'
      : (seed === ''
        ? ''
        : `<button type="button" class="btn btn-primary" data-accept-seed="${escapeHtml(seed)}" `
          + `aria-label="Välj detta förslag — Förslag ${index + 1}">Välj detta förslag</button>`);
    const actionBlock = action
      ? `<div class="form-field form-field--actions">${action}</div>` : '';

    return `<article class="suggestion-card">
      <header class="suggestion-card__header">
        <span class="suggestion-card__index">Förslag ${index + 1}</span>
        <span class="suggestion-card__meta">seed ${seed} · vecka ${week}</span>
      </header>
      <ul class="suggestion-card__days">${dishes || '<li class="suggestion__day">Inga rätter</li>'}</ul>
      ${actionBlock}
    </article>`;
  }

  // ---- Rendering: all three ----
  function render(data) {
    lastData = data;
    const suggestions = (data && Array.isArray(data.suggestions)) ? data.suggestions : [];
    // offer_id -> store scope (display only; absent offer_sources = no label).
    // T10f DA P3-2: prefer the resolved store NAME; the raw id is the fallback.
    const sourceNames = new Map(
      ((data && Array.isArray(data.offer_sources)) ? data.offer_sources : [])
        .filter((s) => s && s.store_id)
        .map((s) => [s.offer_id, s.store_name || s.store_id]));
    const grid = document.getElementById('suggestions-grid');
    const countEl = document.getElementById('suggestions-count');
    const emptyEl = document.getElementById('suggestions-empty');
    const errorEl = document.getElementById('suggestions-error');

    if (countEl) countEl.textContent = suggestions.length
      ? `${suggestions.length} förslag`
      : '';
    if (grid) grid.innerHTML = suggestions.map((sug, i) => renderSuggestion(sug, i, sourceNames)).join('');
    if (emptyEl) emptyEl.hidden = suggestions.length > 0;
    if (errorEl) errorEl.hidden = true;
    // Card 10064.1.3 / finding 7: a SUCCESSFUL render must also lift the
    // loading banner (and bring the grid back after an error state) — the
    // render-only version never hid #suggestions-loading.
    setBanner('ready');
  }

  // ---- Banner switching ----
  function setBanner(which) {
    const loading = document.getElementById('suggestions-loading');
    const error = document.getElementById('suggestions-error');
    const grid = document.getElementById('suggestions-grid');
    if (loading) loading.hidden = which !== 'loading';
    if (error) error.hidden = which !== 'error';
    if (grid) grid.hidden = (which === 'loading' || which === 'error');
  }

  // ---- Status line + accept-error banner (MC 10349) ----
  function setStatus(message) {
    const el = document.getElementById('suggestions-status');
    if (!el) return;
    el.textContent = message || '';
    el.hidden = !message;
  }

  function setAcceptError(message) {
    const el = document.getElementById('suggestions-accept-error');
    const text = document.getElementById('suggestions-accept-error-text');
    if (text) text.textContent = message || '';
    if (el) el.hidden = !message;
  }

  // ---- Accept: the owner's one click (finding 2) ----
  async function acceptSuggestion(seed) {
    const sug = lastData && (lastData.suggestions || [])
      .find((s) => s.seed === seed);
    if (!sug) return;
    const dishes = (sug.days || []).map((d) => d.dish_id); // render order (contract)
    setAcceptError('');
    setStatus('Sparar valet...');
    try {
      const res = await apiPost(Endpoints.menuAccept, { seed, dishes });
      const body = await res.json(); // {ok, week_key, dishes}
      acceptedSeed = seed;
      render(lastData);
      setStatus(`Vald ✓ — sparat för ${body.week_key}. Bygg handelslistan under Handelslista.`);
    } catch (err) {
      const status = err && err.response ? err.response.status : 0;
      console.error('Accept failed:', err);
      if (status === 409) {
        // DA P1-A: the offered plan moved (boot-ingest, profile edit). Nothing
        // was recorded — re-fetch honestly and ask for a new choice.
        await load();
        setStatus('Menyn uppdaterad — välj igen');
      } else {
        setStatus('');
        setAcceptError(status
          ? `Kunde inte spara valet (fel ${status}). Kontrollera att du är inloggad och försök igen.`
          : 'Kunde inte spara valet. Försök igen.');
      }
    }
  }

  // ---- Load on view enter ----
  async function load() {
    setBanner('loading');
    setAcceptError('');
    try {
      const [data, accepted] = await Promise.all([
        fetchSuggestions(),
        fetchAccepted(),
      ]);
      acceptedSeed = accepted && accepted.seed != null ? accepted.seed : null;
      render(data);
      if (accepted) {
        setStatus(`Vald ✓ — sparat för ${accepted.week_key}.`);
      }
    } catch (err) {
      console.error('Failed to load suggestions:', err);
      acceptedSeed = null;
      setBanner('error');
    }
  }

  function bindRetry() {
    const btn = document.getElementById('suggestions-retry');
    if (btn) btn.addEventListener('click', load);
  }

  // One delegated listener for the re-rendered grid (buttons are replaced on
  // every render, so per-button binding would leak/stale).
  function bindGrid() {
    const grid = document.getElementById('suggestions-grid');
    if (!grid) return;
    grid.addEventListener('click', (ev) => {
      const acceptBtn = ev.target.closest('[data-accept-seed]');
      if (acceptBtn) {
        acceptSuggestion(parseInt(acceptBtn.dataset.acceptSeed, 10));
        return;
      }
      const dishBtn = ev.target.closest('[data-dish]');
      if (dishBtn && window.recipeModule) recipeModule.open(dishBtn.dataset.dish);
    });
  }

  function init() {
    bindRetry();
    bindGrid();
    load();
  }

  return { init, load, render, renderSuggestion, acceptSuggestion };
})();

// Global alias (POC idiom): app.js bootstraps via window-scope module.
window.suggestionsModule = suggestionsModule;
