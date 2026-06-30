/** @jsx React.createElement */

const { useState, useEffect, useMemo } = React;

const { Icon, API, fetchAppSettings, saveAppSettings } = window.FTTH_APP;

function AppSettingsModal({ onClose }) {
  const [items, setItems] = useState([]);
  const [draft, setDraft] = useState({});
  const [envFile, setEnvFile] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchAppSettings()
      .then(data => {
        if (!active) return;
        setItems(data.items || []);
        setEnvFile(data.env_file || '');
      })
      .catch(err => active && setError(err.message || 'Failed to load settings'))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  const groups = useMemo(() => {
    const grouped = {};
    items.forEach(item => {
      const group = item.group || 'Other';
      if (!grouped[group]) grouped[group] = [];
      grouped[group].push(item);
    });
    return grouped;
  }, [items]);

  function updateDraft(key, value) {
    setDraft(prev => ({ ...prev, [key]: value }));
    setSaved('');
  }

  async function handleSave() {
    const values = {};
    Object.entries(draft).forEach(([key, value]) => {
      if (String(value ?? '').trim() !== '') values[key] = value;
    });
    if (!Object.keys(values).length) {
      setError('Enter at least one value to update.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const data = await saveAppSettings(values);
      setItems(data.items || []);
      setEnvFile(data.env_file || envFile);
      setDraft({});
      setSaved('Settings saved. New agent runs will use the updated values.');
      window.dispatchEvent(new CustomEvent('ftth:maps-key-updated'));
    } catch (err) {
      setError(err.message || 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/60 p-3 sm:p-4">
      <div className="w-full max-w-4xl max-h-[92vh] overflow-hidden rounded-xl border border-slate-200 dark:border-dark-600 bg-white dark:bg-dark-800 shadow-2xl">
        <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-slate-200 dark:border-dark-600">
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white">API Keys & Tokens</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">Paste only the values you want to change. Existing secrets stay hidden.</p>
          </div>
          <button onClick={onClose} className="w-8 h-8 rounded-lg inline-flex items-center justify-center text-slate-500 dark:text-slate-300 hover:text-red-500 hover:bg-slate-100 dark:hover:bg-dark-700 leading-none" title="Close"><Icon name="x" size={16} /></button>
        </div>

        <div className="overflow-y-auto max-h-[62vh] px-5 py-4">
          {loading ? (
            <div className="py-12 text-center text-sm text-slate-500 dark:text-slate-400">Loading settings...</div>
          ) : (
            <div className="space-y-5">
              {Object.entries(groups).map(([group, groupItems]) => (
                <section key={group}>
                  <h3 className="text-xs font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">{group}</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {groupItems.map(item => (
                      <label key={item.key} className="block rounded-lg border border-slate-200 dark:border-dark-600 bg-slate-50 dark:bg-dark-700/60 p-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">{item.label}</div>
                            <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">{item.key}</div>
                          </div>
                          <span className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-semibold ${item.is_set ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300' : 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'}`}>
                            {item.is_set ? 'Set' : 'Missing'}
                          </span>
                        </div>
                        {item.hint && <div className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">{item.hint}</div>}
                        <div className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">Current: {item.masked_value || 'not set'}</div>
                        {item.type === 'boolean' ? (
                          <select
                            value={draft[item.key] || ''}
                            onChange={e => updateDraft(item.key, e.target.value)}
                            className="mt-2 w-full rounded border border-slate-300 dark:border-dark-600 bg-white dark:bg-dark-900 px-3 py-2 text-sm text-slate-900 dark:text-white outline-none focus:border-blue-500"
                          >
                            <option value="">No change</option>
                            <option value="true">Enabled</option>
                            <option value="false">Disabled</option>
                          </select>
                        ) : (
                          <input
                            type="password"
                            value={draft[item.key] || ''}
                            onChange={e => updateDraft(item.key, e.target.value)}
                            placeholder="Paste new value"
                            autoComplete="off"
                            className="mt-2 w-full rounded border border-slate-300 dark:border-dark-600 bg-white dark:bg-dark-900 px-3 py-2 text-sm text-slate-900 dark:text-white outline-none focus:border-blue-500"
                          />
                        )}
                      </label>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>

        <div className="px-5 py-4 border-t border-slate-200 dark:border-dark-600">
          {envFile && <div className="mb-2 text-[11px] text-slate-500 dark:text-slate-400">Saving to: {envFile}</div>}
          {error && <div className="mb-2 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300">{error}</div>}
          {saved && <div className="mb-2 rounded border border-green-300 bg-green-50 px-3 py-2 text-xs text-green-700 dark:border-green-800 dark:bg-green-950/40 dark:text-green-300">{saved}</div>}
          <div className="flex items-center justify-end gap-3 flex-wrap">
            <button onClick={onClose} className="rounded-lg border border-slate-300 dark:border-dark-600 px-4 py-2 text-sm text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-dark-700">Close</button>
            <button onClick={handleSave} disabled={saving || loading} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
              {saving ? 'Saving...' : 'Save Settings'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.AppSettingsModal = AppSettingsModal;
