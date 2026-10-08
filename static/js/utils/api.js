// ============================================================================
// utils/api.js — Shared prefix-aware API client for matapp framtidsvision
// (Phase 8 T7 frontend, gate C7 — auth/profil/3-förslag vyer)
// ============================================================================
// Centralises the API base URL and endpoint paths so feature modules (auth.js,
// profile.js, suggestions.js) don't hardcode URLs. Loaded before all ui modules
// as a plain <script> (no ES modules) so its bindings are shared global scope.
//
// PREFIX-AWARE (the known phase-8 LEDGE — gate C7 "API-anrop mot /matapp/-
// prefixet INTE /"): the renderer's nginx serves the app under /matapp/ and
// STRIPS that prefix before proxying to the backend, so the browser MUST request
// /matapp/api/... while the backend still sees /api/... Derive the base from the
// page path so it also works mounted at root ("") and survives a move to another
// prefix — never hardcode "/matapp". This is the POC api.js (MC 1111.1 fix),
// preserved verbatim: the ruling is a LESSON HERE, not a file to rewrite.
// ============================================================================

function resolveApiBase(pathname) {
  const seg = (pathname || '').split('/')[1];
  return seg ? '/' + seg : '';
}
const API_BASE = resolveApiBase(window.location.pathname);

/**
 * Perform a GET request against the API.
 * @param {string} path - API path (from `Endpoints`)
 * @returns {Promise<Response>} The fetch Response (callers keep res.json())
 * @throws {Error} On 4xx/5xx HTTP status
 */
async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) {
    // MC 1355.18: attach the Response so callers can read status/detail
    // (e.g. auth.js distinguishing 409 from 422) without a second mechanism.
    const err = new Error(`HTTP ${res.status}`);
    err.response = res;
    throw err;
  }
  return res;
}

/**
 * Perform a JSON POST request against the API.
 * @param {string} path - API path (from `Endpoints`)
 * @param {Object} body - JSON body
 * @returns {Promise<Response>} The fetch Response
 * @throws {Error} On 4xx/5xx HTTP status
 */
async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.response = res;
    throw err;
  }
  return res;
}

/**
 * Perform a JSON PUT request against the API (profile upsert).
 * @param {string} path - API path (from `Endpoints`)
 * @param {Object} body - JSON body
 * @returns {Promise<Response>} The fetch Response
 * @throws {Error} On 4xx/5xx HTTP status
 */
async function apiPut(path, body) {
  const res = await fetch(API_BASE + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.response = res;
    throw err;
  }
  return res;
}

/**
 * Perform a DELETE request against the API (shopping-list removal, MC 10349).
 * Callers URL-encode path segments (encodeURIComponent) — item names carry
 * spaces and Swedish letters.
 * @param {string} path - API path (from `Endpoints` + encoded segment)
 * @returns {Promise<Response>} The fetch Response
 * @throws {Error} On 4xx/5xx HTTP status
 */
async function apiDelete(path) {
  const res = await fetch(API_BASE + path, { method: 'DELETE' });
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`);
    err.response = res;
    throw err;
  }
  return res;
}

// Central registry of API endpoints (Phase 8 frontend surface; MC 10349 adds
// the journey endpoints — accept/accepted/recipe/shopping — over APIs that
// already existed server-side but were never reachable from the UI).
const Endpoints = {
  login: '/api/auth/login',
  logout: '/api/auth/logout',
  me: '/api/auth/me',
  register: '/api/auth/register',
  profile: '/api/profile',
  menu: '/api/menu',
  menuAccept: '/api/menu/accept',
  menuAccepted: '/api/menu/accepted',
  shopping: '/api/shopping',
  shoppingToggle: '/api/shopping/toggle',
  shoppingBuild: '/api/shopping/build',
  recipe: '/api/recipe/',          // + encodeURIComponent(title)
};
