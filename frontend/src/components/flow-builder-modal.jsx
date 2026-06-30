/** @jsx React.createElement */

const { useState, useEffect } = React;

const { Icon, API, authHeaders, apiFetch, fetchAgentEstimates, PIPELINE_AGENT_SECTIONS, FLOW_AGENT_TO_PIPELINE_ID, sectionHasActiveMode, flowAgentDefaults, normalizeDataSource, normalizeFlowAgents } = window.FTTH_APP;

function FlowBuilderModal({ jobId, onClose, onApplied }) {
  const [templates, setTemplates] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [loadingTemplates, setLoadingTemplates] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingAgents, setEditingAgents] = useState(null); // local editable copy
  const [agentEstimates, setAgentEstimates] = useState(null);

  const AGENT_LABELS = {
    agent0_house_discovery: 'Agent 0: House Discovery',
    agent2_geocoding: 'Agent 1: Geocoding',
    agent1_address_validator: 'Agent 2: Address Validation',
    agent3_parcel: 'Agent 3: Parcel & Land Use',
    agent4_building: 'Agent 4: Building',
    agent5_0_offline_ocr: 'Agent 5-0: Offline OCR',
    agent5_streetview: 'Agent 5: Street View',
    agent6_finalization: 'Agent 6: FTTH Final',
    agent7_neighborhood_discovery: 'Agent 7: Neighborhood Discovery',
  };

  const DATA_SOURCE_OPTIONS = [
    { value: 'database', label: 'Database (Stored Records)' },
    { value: 'agent0_output', label: 'Agent 0 Output' },
    { value: 'agent2_output', label: 'Agent 1 Output' },
    { value: 'agent1_output', label: 'Agent 2 Output' },
    { value: 'agent3_output', label: 'Agent 3 Output' },
    { value: 'agent4_output', label: 'Agent 4 Output' },
    { value: 'agent5_0_output', label: 'Agent 5-0 Output' },
    { value: 'agent5_output', label: 'Agent 5 Output' },
    { value: 'all_agents', label: 'All Agents (Final)' },
  ];

  useEffect(() => {
    Promise.all([
      apiFetch(`${API}/flow-templates`, { headers: authHeaders() }).then(r => r.json()),
      apiFetch(`${API}/jobs/${jobId}/flow`, { headers: authHeaders() }).then(r => r.json()).catch(() => null),
      fetchAgentEstimates(jobId).catch(() => null),
    ]).then(([templatesData, jobFlow, estimates]) => {
      const tmplList = templatesData.templates || [];
      setTemplates(tmplList);
      if (estimates) setAgentEstimates(estimates);
      // Prefer job's existing flow template, else first available
      const activeTemplateId = jobFlow?.template_id || tmplList[0]?.id;
      setSelectedId(activeTemplateId);
      const tmpl = tmplList.find(t => t.id === activeTemplateId);
      const agents = jobFlow?.flow_config?.agents || tmpl?.config?.agents || [];
      setEditingAgents(normalizeFlowAgents(JSON.parse(JSON.stringify(agents))));
    }).catch(err => console.error('Flow load error:', err))
      .finally(() => setLoadingTemplates(false));
  }, [jobId]);

  function onTemplateChange(templateId) {
    setSelectedId(templateId);
    const tmpl = templates.find(t => t.id === templateId);
    if (tmpl) setEditingAgents(normalizeFlowAgents(JSON.parse(JSON.stringify(tmpl.config?.agents || []))));
  }

  function updateAgent(idx, patch) {
    setEditingAgents(prev => prev.map((a, i) => i === idx ? { ...a, ...patch } : a));
  }

  function updateThreshold(idx, key, value) {
    setEditingAgents(prev => prev.map((a, i) => {
      if (i !== idx) return a;
      const parsed = value === '' ? null : Math.max(0, Math.min(100, parseInt(value, 10)));
      const thresholds = { ...(a.confidence_thresholds || {}), [key]: Number.isNaN(parsed) ? null : parsed };
      return { ...a, confidence_thresholds: thresholds };
    }));
  }

  function setAllAgentsEnabled(enabled) {
    setEditingAgents(prev => (prev || []).map((agent) => {
      const pipelineOptions = { ...(agent.pipeline_options || flowAgentDefaults(agent.agent_name)), enabled };
      return { ...agent, enabled, pipeline_options: pipelineOptions };
    }));
  }

  function updateAgentPipelineOption(idx, key, value) {
    setEditingAgents(prev => prev.map((a, i) => {
      if (i !== idx) return a;
      const pipelineOptions = {
        ...(a.pipeline_options || flowAgentDefaults(a.agent_name)),
        enabled: a.enabled,
        [key]: value,
      };
      return { ...a, pipeline_options: pipelineOptions };
    }));
  }

  async function handleApply() {
    if (!selectedId) return;
    const normalizedAgents = normalizeFlowAgents(editingAgents || []);
    if (!normalizedAgents.some(agent => agent.enabled)) {
      alert('Enable at least one agent before applying the flow.');
      return;
    }
    for (const agent of normalizedAgents) {
      const sectionId = FLOW_AGENT_TO_PIPELINE_ID[agent.agent_name];
      const section = PIPELINE_AGENT_SECTIONS.find(s => s.id === sectionId);
      if (!section || !agent.enabled) continue;
      if (!sectionHasActiveMode(section.id, agent.pipeline_options || {})) {
        alert(`Select at least one sub-option for ${section.name}.`);
        return;
      }
    }
    setSaving(true);
    try {
      // Save edited config back to template first
      const tmpl = templates.find(t => t.id === selectedId);
      if (tmpl) {
        await apiFetch(`${API}/flow-templates/${selectedId}`, {
          method: 'PUT',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ config: { ...tmpl.config, agents: normalizedAgents } }),
        });
      }
      // Apply flow to job
      const res = await apiFetch(`${API}/jobs/${jobId}/set-flow`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ template_id: selectedId }),
      });
      if (res.ok) {
        fetchAgentEstimates(jobId).then(setAgentEstimates).catch(() => {});
        onApplied && onApplied();
        onClose();
      } else {
        alert('Failed to apply flow');
      }
    } catch (err) {
      alert('Error: ' + err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveAsNew() {
    const name = window.prompt('Enter a name for the new flow template:');
    if (!name) return;
    setSaving(true);
    try {
      const tmpl = templates.find(t => t.id === selectedId);
      const res = await apiFetch(`${API}/flow-templates`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          description: `Based on: ${tmpl?.name || 'custom'}`,
          config: { name, agents: editingAgents },
        }),
      });
      if (res.ok) {
        const newTmpl = await res.json();
        const updated = await apiFetch(`${API}/flow-templates`, { headers: authHeaders() }).then(r => r.json());
        setTemplates(updated.templates || []);
        setSelectedId(newTmpl.id);
        alert(`Template "${name}" saved!`);
      }
    } catch (err) {
      alert('Error: ' + err.message);
    } finally {
      setSaving(false);
    }
  }

  const sortedAgents = [...(editingAgents || [])].sort((a, b) => (a.order || 0) - (b.order || 0));
  const enabledCount = sortedAgents.filter(a => a.enabled).length;
  const allAgentsEnabled = sortedAgents.length > 0 && enabledCount === sortedAgents.length;
  const estimateByAgent = new Map((agentEstimates?.agents || []).map((item) => [item.agent_id, item]));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-white dark:bg-dark-800 rounded-2xl shadow-2xl w-full max-w-3xl mx-4 flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-dark-600">
          <div>
            <h2 className="text-lg font-bold flex items-center gap-2"><Icon name="settings" size={18} /> Flow Builder</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Configure agent execution, data sources & confidence thresholds</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 leading-none" title="Close"><Icon name="x" size={18} /></button>
        </div>

        {loadingTemplates ? (
          <div className="flex-1 flex items-center justify-center py-12 text-gray-500">Loading flow templates...</div>
        ) : (
          <>
            {/* Template selector */}
            <div className="px-6 py-4 border-b border-gray-100 dark:border-dark-600 flex items-center gap-3">
              <label className="text-sm font-semibold whitespace-nowrap">Flow Template:</label>
              <select
                value={selectedId || ''}
                onChange={e => onTemplateChange(e.target.value)}
                className="flex-1 rounded-lg border border-gray-300 dark:border-dark-600 bg-white dark:bg-dark-700 px-3 py-2 text-sm"
              >
                {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
              <button
                onClick={handleSaveAsNew}
                disabled={saving}
                className="shrink-0 text-xs bg-gray-100 dark:bg-dark-700 hover:bg-gray-200 dark:hover:bg-dark-600 border border-gray-300 dark:border-dark-600 px-3 py-2 rounded-lg"
              >
                + Save as New
              </button>
            </div>

            {/* Agent grid */}
            <div className="flex-1 overflow-y-auto px-6 py-4">
              <div className="grid grid-cols-1 gap-0 text-sm">
                <div className="flex items-center justify-between gap-3 mb-3 px-2">
                  <label className="flex items-center gap-2 text-xs font-semibold text-slate-700 dark:text-gray-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={allAgentsEnabled}
                      onChange={e => setAllAgentsEnabled(e.target.checked)}
                      className="w-4 h-4 accent-blue-600"
                    />
                    <span>Check all agents</span>
                  </label>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setAllAgentsEnabled(true)}
                      className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                    >
                      Select all
                    </button>
                    <span className="text-gray-300 dark:text-dark-500">|</span>
                    <button
                      type="button"
                      onClick={() => setAllAgentsEnabled(false)}
                      className="text-xs text-gray-600 dark:text-gray-400 hover:underline"
                    >
                      Clear all
                    </button>
                  </div>
                </div>

                {/* Column headers */}
                <div className="grid grid-cols-12 gap-2 mb-2 px-2">
                  <div className="col-span-1 text-xs font-semibold text-gray-500">On</div>
                  <div className="col-span-3 text-xs font-semibold text-gray-500">Agent</div>
                  <div className="col-span-3 text-xs font-semibold text-gray-500">
                    Data Source
                    <span className="block font-normal text-[10px] text-gray-400">Always reads stored DB rows - never re-opens upload files</span>
                  </div>
                  <div className="col-span-2 text-xs font-semibold text-gray-500 text-center">Threshold 1 (%)</div>
                  <div className="col-span-2 text-xs font-semibold text-gray-500 text-center">Threshold 2 (%)</div>
                  <div className="col-span-1 text-xs font-semibold text-gray-500 text-right">Time</div>
                </div>

                {sortedAgents.map((agent, rawIdx) => {
                  const idx = (editingAgents || []).indexOf(agent);
                  const sectionId = FLOW_AGENT_TO_PIPELINE_ID[agent.agent_name];
                  const section = PIPELINE_AGENT_SECTIONS.find(s => s.id === sectionId);
                  const sectionOptions = agent.pipeline_options || flowAgentDefaults(agent.agent_name);
                  const estimate = estimateByAgent.get(agent.agent_name);
                  return (
                    <div key={agent.agent_name}
                      className={`grid grid-cols-12 gap-2 items-center px-2 py-2.5 rounded-lg mb-1 ${agent.enabled ? 'bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800' : 'bg-gray-50 dark:bg-dark-700 border border-gray-200 dark:border-dark-600 opacity-60'}`}
                    >
                      {/* Enabled toggle */}
                      <div className="col-span-1 flex justify-center">
                        <input
                          type="checkbox"
                          checked={!!agent.enabled}
                          onChange={e => {
                            const enabled = e.target.checked;
                            updateAgent(idx, {
                              enabled,
                              pipeline_options: { ...sectionOptions, enabled },
                            });
                          }}
                          className="w-4 h-4 accent-blue-600"
                        />
                      </div>

                      {/* Agent name */}
                      <div className="col-span-3">
                        <span className={`font-medium text-xs ${agent.enabled ? 'text-gray-800 dark:text-gray-100' : 'text-gray-400 dark:text-gray-500'}`}>
                          {AGENT_LABELS[agent.agent_name] || agent.agent_name}
                        </span>
                      </div>

                      {/* Data source */}
                      <div className="col-span-3">
                        <select
                          value={normalizeDataSource(agent.data_source)}
                          onChange={e => updateAgent(idx, { data_source: e.target.value })}
                          disabled={!agent.enabled}
                          className="w-full text-xs rounded border border-gray-300 dark:border-dark-600 bg-white dark:bg-dark-700 px-2 py-1 disabled:opacity-50"
                        >
                          {DATA_SOURCE_OPTIONS.map(opt => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ))}
                        </select>
                      </div>

                      {/* Threshold 1 */}
                      <div className="col-span-2 flex justify-center">
                        <input
                          type="number"
                          min="0" max="100"
                          value={agent.confidence_thresholds?.threshold_1 ?? ''}
                          onChange={e => updateThreshold(idx, 'threshold_1', e.target.value)}
                          disabled={!agent.enabled}
                          placeholder="-"
                          className="w-16 text-xs text-center rounded border border-gray-300 dark:border-dark-600 bg-white dark:bg-dark-700 px-2 py-1 disabled:opacity-50"
                        />
                      </div>

                      {/* Threshold 2 */}
                      <div className="col-span-2 flex justify-center">
                        <input
                          type="number"
                          min="0" max="100"
                          value={agent.confidence_thresholds?.threshold_2 ?? ''}
                          onChange={e => updateThreshold(idx, 'threshold_2', e.target.value)}
                          disabled={!agent.enabled}
                          placeholder="-"
                          className="w-16 text-xs text-center rounded border border-gray-300 dark:border-dark-600 bg-white dark:bg-dark-700 px-2 py-1 disabled:opacity-50"
                        />
                      </div>

                      <div className="col-span-1 text-right">
                        <span title={estimate?.estimate_basis || ''} className="text-[10px] font-mono text-slate-600 dark:text-slate-300">
                          {agent.enabled ? (estimate?.estimated_time || '-') : '-'}
                        </span>
                      </div>

                      <div className="col-span-12 pl-9 -mt-1 mb-1">
                        {section?.options?.length > 0 && agent.enabled && (
                          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
                            {section.options.map(({ key, label, hint, type, min, max, step, choices }) => (
                              type === 'number' ? (
                                <label key={key} className="flex items-center justify-between gap-2 text-[11px] rounded px-2 py-1 bg-white/70 dark:bg-dark-700/70 border border-blue-100 dark:border-blue-900/60">
                                  <span className="min-w-0">
                                    <span className="block font-medium text-slate-700 dark:text-slate-200">{label}</span>
                                    <span className="block text-slate-500 dark:text-slate-400">{hint}</span>
                                  </span>
                                  <input
                                    type="number"
                                    min={min}
                                    max={max}
                                    step={step}
                                    className="w-24 shrink-0 rounded border border-gray-300 dark:border-dark-600 bg-white dark:bg-dark-700 px-2 py-1 text-right"
                                    value={sectionOptions[key] ?? ''}
                                    onChange={(e) => {
                                      const raw = e.target.value;
                                      updateAgentPipelineOption(idx, key, raw === '' ? '' : Number(raw));
                                    }}
                                  />
                                </label>
                              ) : type === 'select' ? (
                                <label key={key} className="flex items-center justify-between gap-2 text-[11px] rounded px-2 py-1 bg-white/70 dark:bg-dark-700/70 border border-blue-100 dark:border-blue-900/60">
                                  <span className="min-w-0">
                                    <span className="block font-medium text-slate-700 dark:text-slate-200">{label}</span>
                                    <span className="block text-slate-500 dark:text-slate-400">{hint}</span>
                                  </span>
                                  <select
                                    className="w-36 shrink-0 rounded border border-gray-300 dark:border-dark-600 bg-white dark:bg-dark-700 px-2 py-1"
                                    value={sectionOptions[key] ?? choices?.[0]?.value ?? ''}
                                    onChange={(e) => updateAgentPipelineOption(idx, key, e.target.value)}
                                  >
                                    {(choices || []).map(choice => (
                                      <option key={choice.value} value={choice.value}>{choice.label}</option>
                                    ))}
                                  </select>
                                </label>
                              ) : (
                                <label key={key} className="flex items-start gap-2 text-[11px] cursor-pointer rounded px-2 py-1 bg-white/70 dark:bg-dark-700/70 border border-blue-100 dark:border-blue-900/60">
                                  <input
                                    type="checkbox"
                                    className="mt-0.5"
                                    checked={!!sectionOptions[key]}
                                    onChange={(e) => updateAgentPipelineOption(idx, key, e.target.checked)}
                                  />
                                  <span>
                                    <span className="block font-medium text-slate-700 dark:text-slate-200">{label}</span>
                                    <span className="block text-slate-500 dark:text-slate-400">{hint}</span>
                                  </span>
                                </label>
                              )
                            ))}
                          </div>
                        )}
                        {section?.options?.length > 0 && agent.enabled && !sectionHasActiveMode(section.id, sectionOptions) && (
                          <p className="text-[11px] text-amber-700 dark:text-amber-400 mt-1">Select at least one sub-option for this agent.</p>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Legend */}
              {agentEstimates?.total_estimated_time && (
                <div className="mt-4 p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 text-xs text-blue-800 dark:text-blue-200">
                  <strong>Estimated runtime:</strong> {agentEstimates.total_estimated_time}
                  <span className="ml-2 text-blue-700/80 dark:text-blue-200/80">
                    Based on {agentEstimates.row_count || 0} stored record(s), current flow options, and API/OCR latency assumptions.
                  </span>
                </div>
              )}
              <div className="mt-4 p-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 text-xs text-amber-800 dark:text-amber-200">
                <strong>Confidence Thresholds:</strong> If an agent returns confidence below Threshold 1, those records cascade to the next enabled agent.
                If below Threshold 2, they skip to the agent after that. Leave blank to pass all records regardless of confidence.
              </div>
              {editingAgents && enabledCount === 0 && (
                <p className="mt-3 text-xs text-amber-600 dark:text-amber-400">
                  Enable at least one agent before applying this flow.
                </p>
              )}
            </div>

            {/* Footer */}
            <div className="px-6 py-4 border-t border-gray-200 dark:border-dark-600 flex items-center justify-between gap-3">
              <div className="text-xs text-gray-500 dark:text-gray-400">
                {enabledCount} / {sortedAgents.length} agents enabled
              </div>
              <div className="flex gap-3">
                <button onClick={onClose} className="px-4 py-2 border border-gray-300 dark:border-dark-600 rounded-lg text-sm hover:bg-gray-50 dark:hover:bg-dark-700">
                  Cancel
                </button>
                <button
                  onClick={handleApply}
                  disabled={saving || !selectedId || enabledCount === 0}
                  className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white rounded-lg text-sm font-semibold"
                >
                  {saving ? 'Applying...' : <span className="inline-flex items-center gap-1"><Icon name="check" size={14} /> Apply Flow</span>}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.FlowBuilderModal = FlowBuilderModal;
