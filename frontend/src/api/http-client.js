/**
 * HTTP helpers: debounce and in-flight GET deduplication.
 */
(function (global) {
  function debounce(fn, waitMs) {
    waitMs = waitMs || 300;
    var timer = null;
    return function () {
      var context = this;
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(context, args);
      }, waitMs);
    };
  }

  var inflight = new Map();

  function dedupeInFlight(key, fn) {
    if (inflight.has(key)) {
      return inflight.get(key);
    }
    var promise = Promise.resolve()
      .then(fn)
      .finally(function () {
        inflight.delete(key);
      });
    inflight.set(key, promise);
    return promise;
  }

  global.FTTH_HTTP = {
    debounce: debounce,
    dedupeInFlight: dedupeInFlight,
  };
})(window);
