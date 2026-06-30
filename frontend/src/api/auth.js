/**
 * FTTH frontend API client — edit here for auth / fetch behavior.
 * Loaded before the React (Babel) bundle in frontend/public/index.html.
 */
(function (global) {
  const API = '/api';

  function getToken() {
    return sessionStorage.getItem('ftth_token');
  }
  function setToken(t) {
    sessionStorage.setItem('ftth_token', t);
  }
  function clearToken() {
    sessionStorage.removeItem('ftth_token');
    sessionStorage.removeItem('ftth_user');
  }
  function getUser() {
    return sessionStorage.getItem('ftth_user');
  }
  function authHeaders() {
    return { Authorization: 'Bearer ' + getToken() };
  }

  async function apiFetch(url, options) {
    options = options || {};
    const timeoutMs = options.timeoutMs || 120000;
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, timeoutMs);
    const fetchOptions = Object.assign({}, options, { signal: controller.signal });
    delete fetchOptions.timeoutMs;
    try {
      const res = await fetch(url, fetchOptions);
      if (res.status === 401) {
        clearToken();
        global.dispatchEvent(new CustomEvent('ftth-unauthorized'));
      }
      return res;
    } finally {
      clearTimeout(timer);
    }
  }

  async function apiLogin(username, password) {
    const res = await fetch(API + '/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username, password: password }),
    });
    if (!res.ok) {
      const e = await res.json();
      throw new Error(e.detail || 'Login failed');
    }
    return res.json();
  }

  async function fetchMapsKey() {
    const res = await apiFetch(API + '/maps-key', {
      headers: authHeaders(),
      cache: 'no-store',
    });
    if (!res.ok) throw new Error('Failed to fetch Google Maps key');
    return res.json();
  }

  global.FTTH = {
    API: API,
    OLLAMA_CHAT_URL: '/chat',
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    getUser: getUser,
    authHeaders: authHeaders,
    apiFetch: apiFetch,
    apiLogin: apiLogin,
    fetchMapsKey: fetchMapsKey,
  };
})(window);
