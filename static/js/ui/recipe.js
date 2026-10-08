// ============================================================================
// ui/recipe.js — recipe modal (native <dialog>) — shared by suggestions + shopping
// (MC 10349: acceptance item 3 "Reading recipes" — the feature was absent)
// ============================================================================
// One modal, two callers: suggestion-card dish buttons and shopping rows open
// GET /api/recipe/{title} in the <dialog id="recipe-dialog"> declared in
// index.html. Escape closes natively; the close button closes explicitly; the
// close event returns focus to the opener (keyboard users land back on the
// button they pressed). All content is built with DOM nodes + textContent —
// recipe strings are backend data, never innerHTML (T10f DA P3-1 precedent).
// ============================================================================

const recipeModule = (() => {
  let lastFocused = null; // the element that opened the dialog

  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (className) el.className = className;
    return el;
  }

  function dialogEls() {
    return {
      dialog: document.getElementById('recipe-dialog'),
      body: document.getElementById('recipe-dialog-body'),
      closeBtn: document.getElementById('recipe-dialog-close'),
    };
  }

  // ---- Render one recipe into the dialog body (textContent only) ----
  function renderRecipe(recipe, body) {
    body.textContent = '';
    const bits = [];
    if (recipe.category) bits.push(recipe.category);
    if (recipe.servings != null) bits.push(`${recipe.servings} porttioner`);
    if (recipe.vegetarian) bits.push('vegetarisk');
    if (recipe.kid_friendly) bits.push('barnvänlig');
    if (bits.length) body.appendChild(node('p', bits.join(' · '), 'form-help'));

    body.appendChild(node('h3', 'Ingredier'));
    const list = document.createElement('ul');
    for (const ing of (recipe.ingredients || [])) {
      const qty = ing.qty != null && ing.qty !== ''
        ? `${ing.qty} ${ing.unit || ''}`.trim() + ' ' : '';
      list.appendChild(node('li', `${qty}${ing.name || ''}`.trim()));
    }
    body.appendChild(list); // an empty list is the honest state, not fake text

    const allergens = (recipe.allergens || []).join(', ');
    body.appendChild(node('p', allergens ? `Allergener: ${allergens}` : 'Inga kända allergener.', 'form-help'));
  }

  // ---- Open with a title (called from suggestions + shopping grids) ----
  async function open(title) {
    const { dialog, body } = dialogEls();
    if (!dialog || !title) return;
    lastFocused = document.activeElement;
    body.textContent = 'Laddar recept...';
    if (!dialog.open) dialog.showModal();
    try {
      const res = await apiGet(Endpoints.recipe + encodeURIComponent(title));
      if (!dialog.open) return; // closed while fetching — render nowhere
      renderRecipe(await res.json(), body);
    } catch (err) {
      console.error('Recipe load failed:', err);
      const status = err && err.response ? err.response.status : 0;
      if (dialog.open) {
        body.textContent = status === 404
          ? `Inget recept hittades för “${title}”.`
          : 'Kunde inte hämta receptet. Kontrollera att du är inloggad och försök igen.';
      }
    }
  }

  function close() {
    const { dialog } = dialogEls();
    if (dialog && dialog.open) dialog.close();
  }

  function init() {
    const { dialog, closeBtn } = dialogEls();
    if (!dialog) return;
    if (closeBtn) closeBtn.addEventListener('click', close);
    // Escape fires cancel -> close natively; this covers BOTH paths with the
    // focus return (spec: close button + Escape + focus returns).
    dialog.addEventListener('close', () => {
      if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
      lastFocused = null;
    });
    // Click on the backdrop (the dialog element itself, outside the content)
    // closes it too — the native <dialog> has no backdrop click by default.
    dialog.addEventListener('click', (ev) => {
      if (ev.target === dialog) close();
    });
  }

  return { init, open, close };
})();

// Global alias (POC idiom): app.js bootstraps via window-scope module.
window.recipeModule = recipeModule;
