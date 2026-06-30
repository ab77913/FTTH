/**
 * FTTH data API — jobs, records, uploads, settings.
 * Depends on window.FTTH from auth.js.
 */
(function (global) {
  const base = global.FTTH || {};
  const API = base.API || '/api';
  const authHeaders = base.authHeaders || function () { return {}; };
  const apiFetch = base.apiFetch || fetch;
  const getToken = base.getToken || function () { return null; };
  const dedupeInFlight = (global.FTTH_HTTP && global.FTTH_HTTP.dedupeInFlight) || function (_k, fn) { return fn(); };

  async function parseError(res, fallback) {
    try {
      const err = await res.json();
      return err.detail || fallback;
    } catch (_) {
      return fallback;
    }
  }

  async function apiCreateAccount(payload) {
    const res = await fetch(API + '/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await parseError(res, 'Account creation failed'));
    return res.json();
  }

  async function apiForgotPassword(username) {
    const res = await fetch(API + '/accounts/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username }),
    });
    if (!res.ok) throw new Error(await parseError(res, 'Reset code request failed'));
    return res.json();
  }

  async function apiResetPassword(username, resetToken, newPassword) {
    const res = await fetch(API + '/accounts/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: username,
        reset_token: resetToken,
        new_password: newPassword,
      }),
    });
    if (!res.ok) throw new Error(await parseError(res, 'Password reset failed'));
    return res.json();
  }

  async function fetchJobs() {
    return dedupeInFlight('GET:/jobs', async function () {
      const res = await apiFetch(API + '/jobs', { headers: authHeaders() });
      if (!res.ok) throw new Error('Failed to fetch jobs');
      return res.json();
    });
  }

  async function fetchJob(jobId) {
    const res = await apiFetch(API + '/jobs/' + jobId, { headers: authHeaders() });
    if (!res.ok) throw new Error('Failed to fetch job');
    return res.json();
  }

  async function uploadFile(file, customerId) {
    customerId = customerId || 'demo_customer';
    const formData = new FormData();
    const files = Array.isArray(file) ? file : [file];
    files.forEach(function (f) { formData.append('file', f); });
    const res = await apiFetch(
      API + '/upload?customer_id=' + encodeURIComponent(customerId),
      { method: 'POST', body: formData, headers: authHeaders() }
    );
    if (!res.ok) throw new Error(await parseError(res, 'Upload failed'));
    return res.json();
  }

  async function startProcessing(jobId) {
    const res = await apiFetch(API + '/jobs/' + jobId + '/process', {
      method: 'POST',
      headers: Object.assign({}, authHeaders(), { 'Content-Type': 'application/json' }),
      body: JSON.stringify({}),
    });
    if (!res.ok) throw new Error(await parseError(res, 'Failed to start processing'));
    return res.json();
  }

  async function fetchAgentStatus(jobId) {
    const res = await apiFetch(API + '/jobs/' + jobId + '/agents', {
      headers: authHeaders(),
      cache: 'no-store',
    });
    if (!res.ok) throw new Error('Failed to fetch agent status');
    return res.json();
  }

  async function deleteJob(jobId) {
    const res = await apiFetch(API + '/jobs/' + jobId, {
      method: 'DELETE',
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(await parseError(res, 'Failed to delete job'));
    return res.json();
  }

  async function deleteRecord(recordId) {
    const res = await apiFetch(API + '/records/' + recordId, {
      method: 'DELETE',
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(await parseError(res, 'Failed to delete record'));
    return res.json();
  }

  async function fetchRecords(jobId, page, pageSize, search) {
    page = page || 1;
    pageSize = pageSize || 25;
    const params = new URLSearchParams({ page: page, page_size: pageSize });
    if (jobId) params.append('job_id', jobId);
    if (search) params.append('search', search);
    const res = await apiFetch(API + '/records?' + params, { headers: authHeaders() });
    if (!res.ok) throw new Error('Failed to fetch records');
    return res.json();
  }

  async function fetchJobFlow(jobId) {
    const res = await apiFetch(API + '/jobs/' + jobId + '/flow', { headers: authHeaders() });
    if (!res.ok) throw new Error('Failed to fetch job flow');
    return res.json();
  }

  async function fetchAgentEstimates(jobId) {
    const res = await apiFetch(API + '/jobs/' + jobId + '/agent-estimates', {
      headers: authHeaders(),
      cache: 'no-store',
    });
    if (!res.ok) throw new Error('Failed to fetch agent estimates');
    return res.json();
  }

  async function fetchAppSettings() {
    return dedupeInFlight('GET:/app-settings', async function () {
      const res = await apiFetch(API + '/app-settings', { headers: authHeaders() });
      if (!res.ok) throw new Error('Failed to fetch app settings');
      return res.json();
    });
  }

  async function saveAppSettings(values) {
    const res = await apiFetch(API + '/app-settings', {
      method: 'PUT',
      headers: Object.assign({}, authHeaders(), { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ values: values }),
    });
    if (!res.ok) throw new Error(await parseError(res, 'Failed to save app settings'));
    return res.json();
  }

  async function fetchGeoRecords(jobId) {
    const params = new URLSearchParams({ limit: '50000' });
    if (jobId) params.append('job_id', jobId);
    const res = await apiFetch(API + '/records/geo?' + params, { headers: authHeaders() });
    if (!res.ok) throw new Error('Failed to fetch geo records');
    return res.json();
  }

  async function fetchAgentTables() {
    const res = await apiFetch(API + '/agent/tables', { headers: authHeaders() });
    if (!res.ok) return [];
    return res.json();
  }

  async function fetchMapOverlay(jobId) {
    return dedupeInFlight('GET:/map/overlay:' + jobId, async function () {
      const res = await apiFetch(API + '/map/overlay?job_id=' + jobId, { headers: authHeaders() });
      if (!res.ok) return { overlay: {}, legend: [] };
      return res.json();
    });
  }

  async function fetchStreetViewMeta(lat, lon, recordId) {
    const params = new URLSearchParams({
      lat: String(lat),
      lon: String(lon),
    });
    if (recordId != null) params.set('record_id', String(recordId));
    const res = await apiFetch(API + '/maps/streetview?' + params.toString(), { headers: authHeaders() });
    if (!res.ok) return { available: false, lat: lat, lon: lon, heading: 0, pitch: 0 };
    return res.json();
  }

  function subscribeProgress(jobId, onMessage, onError) {
    const evtSource = new EventSource(API + '/jobs/' + jobId + '/progress?token=' + getToken());
    evtSource.onmessage = function (event) {
      const data = JSON.parse(event.data);
      onMessage(data);
      if (data.done || data.error) evtSource.close();
    };
    evtSource.onerror = function () {
      evtSource.close();
      if (onError) onError();
    };
    return evtSource;
  }

  global.FTTH = Object.assign({}, base, {
    apiCreateAccount: apiCreateAccount,
    apiForgotPassword: apiForgotPassword,
    apiResetPassword: apiResetPassword,
    fetchJobs: fetchJobs,
    fetchJob: fetchJob,
    uploadFile: uploadFile,
    startProcessing: startProcessing,
    fetchAgentStatus: fetchAgentStatus,
    deleteJob: deleteJob,
    deleteRecord: deleteRecord,
    fetchRecords: fetchRecords,
    fetchJobFlow: fetchJobFlow,
    fetchAgentEstimates: fetchAgentEstimates,
    fetchAppSettings: fetchAppSettings,
    saveAppSettings: saveAppSettings,
    fetchGeoRecords: fetchGeoRecords,
    fetchStreetViewMeta: fetchStreetViewMeta,
    fetchAgentTables: fetchAgentTables,
    fetchMapOverlay: fetchMapOverlay,
    subscribeProgress: subscribeProgress,
  });
})(window);
