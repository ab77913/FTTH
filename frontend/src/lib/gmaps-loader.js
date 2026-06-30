/**
 * Lazy-load Google Maps JavaScript API (Street View, elevation, etc.).
 */
(function (global) {
  let loadPromise = null;
  let loadedKey = '';

  function loadGoogleMaps(apiKey) {
    const key = String(apiKey || '').trim();
    if (!key) return Promise.reject(new Error('Google Maps API key required'));
    if (global.google && global.google.maps && loadedKey === key) {
      return Promise.resolve(global.google.maps);
    }
    if (loadPromise && loadedKey === key) return loadPromise;

    loadedKey = key;
    loadPromise = new Promise(function (resolve, reject) {
      const existing = document.querySelector('script[data-ftth-gmaps="1"]');
      if (existing) existing.remove();
      if (global.google && global.google.maps) {
        delete global.google.maps;
      }

      const previousAuthFailure = global.gm_authFailure;
      const callbackName = '__ftthGmapsInit_' + Date.now();
      let settled = false;
      function cleanup() {
        delete global[callbackName];
        if (previousAuthFailure) global.gm_authFailure = previousAuthFailure;
        else delete global.gm_authFailure;
      }
      function fail(message) {
        if (settled) return;
        settled = true;
        cleanup();
        loadPromise = null;
        loadedKey = '';
        reject(new Error(message));
      }
      global.gm_authFailure = function () {
        fail('Google Maps rejected this API key. Check Maps JavaScript API, billing, and HTTP referrer restrictions.');
      };
      global[callbackName] = function () {
        if (settled) return;
        settled = true;
        cleanup();
        if (global.google && global.google.maps) resolve(global.google.maps);
        else reject(new Error('Google Maps failed to initialize'));
      };

      const script = document.createElement('script');
      script.dataset.ftthGmaps = '1';
      script.async = true;
      script.defer = true;
      script.src = 'https://maps.googleapis.com/maps/api/js?key='
        + encodeURIComponent(key)
        + '&v=weekly&libraries=geometry&callback=' + callbackName;
      script.onerror = function () {
        fail('Failed to load Google Maps JavaScript API');
      };
      document.head.appendChild(script);
    });

    return loadPromise;
  }

  global.FTTH_MAPS = Object.assign({}, global.FTTH_MAPS || {}, {
    loadGoogleMaps: loadGoogleMaps,
  });
})(window);
