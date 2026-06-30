/** @jsx React.createElement */

const { useState, useEffect } = React;

const {
  Icon, AgentProgressPanel, RecordsTable, FlowBuilderModal,
  API, getToken, authHeaders, apiFetch, fetchJob, startProcessing, fetchAgentStatus,
  deleteJob, fetchJobFlow, subscribeProgress, showProcessButton, showReProcessButton, mergeAgentStatus,
  computeSelectedPipelineProgress, enrichAgentsWithFlow,
  Breadcrumbs, PageLoader, EmptyState, StatusBadge, useToast, useConfirm, pipelineTotalTimeText,
} = window.FTTH_APP;

function ProjectDetailPage({ jobId, navigate }) {
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const [job, setJob] = useState(null);
  const [agentStatus, setAgentStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [showFlowBuilder, setShowFlowBuilder] = useState(false);
  const [appliedFlowName, setAppliedFlowName] = useState(null);
  const [appliedFlowConfig, setAppliedFlowConfig] = useState(null);
  const [recordsKey, setRecordsKey] = useState(0);

  useEffect(() => { loadData(); }, [jobId]);

  useEffect(() => {
    fetchJobFlow(jobId)
      .then(d => {
        setAppliedFlowName(d.flow_config?.name || 'Default FTTH Pipeline');
        setAppliedFlowConfig(d.flow_config || null);
      })
      .catch(() => {
        setAppliedFlowName('Default FTTH Pipeline');
        setAppliedFlowConfig(null);
      });
  }, [jobId]);

  useEffect(() => {
    if (agentStatus?.status !== 'processing') return undefined;
    const evtSource = subscribeProgress(jobId, async (data) => {
      if (data.done || data.error) {
        try { setAgentStatus(await fetchAgentStatus(jobId)); } catch (e) {}
        setRecordsKey(k => k + 1);
        if (data.done) toast('Pipeline finished', 'success');
        if (data.error) toast('Pipeline error — check agent status', 'error');
      } else {
        setAgentStatus(prev => mergeAgentStatus(prev, data));
      }
    }, () => {});
    const interval = setInterval(async () => {
      try {
        const status = await fetchAgentStatus(jobId);
        setAgentStatus(prev => mergeAgentStatus(prev, status));
        if (status.status === 'completed' || status.status === 'failed') setRecordsKey(k => k + 1);
      } catch (e) {}
    }, 1000);
    return () => { evtSource.close(); clearInterval(interval); };
  }, [agentStatus?.status, jobId]);

  async function loadData() {
    setLoadError(null);
    try {
      const [jobData, status] = await Promise.all([fetchJob(jobId), fetchAgentStatus(jobId)]);
      setJob(jobData);
      setAgentStatus(status);
    } catch (err) {
      setLoadError(err.message || 'Failed to load project');
    } finally {
      setLoading(false);
    }
  }

  async function handleStartProcessing() {
    setAgentStatus(prev => ({ ...(prev || {}), status: 'processing', overall_progress: prev?.overall_progress ?? 0 }));
    try {
      await startProcessing(jobId);
      setAgentStatus(await fetchAgentStatus(jobId));
      toast('Pipeline started', 'success');
    } catch (err) {
      toast(err.message || 'Failed to start processing', 'error');
      setAgentStatus(await fetchAgentStatus(jobId));
    }
  }

  async function handleForceReset() {
    const ok = await confirm({
      title: 'Force stop pipeline',
      message: 'Stop this stuck job and mark it as failed so you can re-run or delete it.',
      confirmLabel: 'Force stop',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await apiFetch(`${API}/jobs/${jobId}/force-reset`, { method: 'POST', headers: authHeaders() });
      setAgentStatus(await fetchAgentStatus(jobId));
      toast('Job reset', 'warning');
    } catch (err) {
      toast('Failed to reset: ' + (err.message || err), 'error');
    }
  }

  async function handleDeleteJob() {
    const ok = await confirm({
      title: 'Delete project',
      message: `Delete this project and all records?\n\n${job?.source_file || ''}\n${jobId}`,
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteJob(jobId);
      toast('Project deleted', 'success');
      navigate('/');
    } catch (err) {
      toast(err.message || 'Delete failed', 'error');
    }
  }

  if (loading) {
    return (
      <div className="app-page flex items-center justify-center">
        <PageLoader message="Loading project…" />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="app-page flex items-center justify-center">
        <EmptyState
          icon={<Icon name="x" size={28} />}
          title="Project not found"
          description={loadError || 'This job may have been deleted or the link is invalid.'}
          action={(
            <button type="button" onClick={() => navigate('/')} className="app-button app-button-primary text-white px-4 py-2 rounded-lg">
              Back to Projects
            </button>
          )}
        />
      </div>
    );
  }

  const isProcessing = agentStatus?.status === 'processing';
  const enrichedAgents = enrichAgentsWithFlow(
    agentStatus?.agents || [],
    appliedFlowConfig,
    agentStatus?.current_agent,
    agentStatus?.status || job.status,
  );
  const pipelineProgress = computeSelectedPipelineProgress(enrichedAgents, agentStatus?.status || job.status);
  const backendProgress = agentStatus?.overall_progress ?? (job.status === 'COMPLETED' ? 100 : 0);
  const displayProgress = Math.max(pipelineProgress.pct, isProcessing ? backendProgress : pipelineProgress.pct);

  return (
    <div className="app-page project-detail-page">
      {showFlowBuilder && (
        <FlowBuilderModal
          jobId={jobId}
          onClose={() => setShowFlowBuilder(false)}
          onApplied={() => {
            fetchJobFlow(jobId)
              .then(d => {
                setAppliedFlowName(d.flow_config?.name || 'Custom Flow');
                setAppliedFlowConfig(d.flow_config || null);
                toast('Flow template applied', 'success');
              })
              .catch(() => {});
          }}
        />
      )}

      <header className="project-detail-header flex-shrink-0">
        <Breadcrumbs items={[
          { label: 'Projects', onClick: () => navigate('/') },
          { label: job.source_file },
        ]} />

        <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-2 mt-1">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="font-bold truncate">{job.source_file}</h1>
              <StatusBadge status={isProcessing ? 'PROCESSING' : job.status} pulse={isProcessing} />
              {isProcessing && (
                <span className="text-[10px] font-semibold text-cyan-600 dark:text-cyan-400 tabular-nums inline-flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-cyan-500 animate-pulse" />
                  {displayProgress}%
                </span>
              )}
              {pipelineTotalTimeText(agentStatus) && (
                <span className="text-[10px] font-mono text-[var(--app-text-soft)] tabular-nums">
                  {pipelineTotalTimeText(agentStatus)}{isProcessing ? ' · running' : ''}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 text-[11px] text-[var(--app-text-soft)] flex-wrap mt-0.5">
              <span className="font-mono" title={job.project_id || job.id}>Project {String(job.project_id || job.id).slice(0, 8)}</span>
              <span className="inline-flex items-center gap-1"><Icon name="globe" size={12} /> {job.customer_id || '—'}</span>
              <span>{job.row_count} records</span>
              <span className="inline-flex items-center gap-1 text-purple-600 dark:text-purple-400 truncate max-w-[14rem]">
                <Icon name="settings" size={12} />
                {appliedFlowName || 'Default FTTH Pipeline'}
              </span>
              <span className="inline-flex items-center gap-1 font-mono truncate max-w-[28rem]" title={job.project_folder || ''}>
                <Icon name="sheet" size={12} /> {job.project_folder || 'Project folder pending'}
              </span>
            </div>
          </div>

          <div className="action-bar flex-shrink-0">
            <button type="button" onClick={() => navigate('/')} className="app-button bg-[var(--app-surface-soft)] border border-[color:var(--app-border)]" title="Back to projects">
              <Icon name="arrowLeft" size={14} /> Back
            </button>
            <button type="button" onClick={() => setShowFlowBuilder(true)} className="app-button bg-purple-600 hover:bg-purple-700 text-white">
              <Icon name="settings" size={14} /> Flow
            </button>
            <button type="button" onClick={() => navigate(`/projects/${jobId}/map`)} className="app-button bg-[var(--app-surface-soft)] border border-[color:var(--app-border)]">
              <Icon name="map" size={14} /> Map
            </button>
            {showProcessButton(agentStatus) && (
              <button type="button" onClick={handleStartProcessing} className="app-button bg-blue-600 hover:bg-blue-700 text-white">
                <Icon name="play" size={14} /> Process
              </button>
            )}
            {showReProcessButton(agentStatus) && (
              <button type="button" onClick={handleStartProcessing} className="app-button bg-amber-600 hover:bg-amber-700 text-white">
                <Icon name="refresh" size={14} /> Re-Process
              </button>
            )}
            {isProcessing && (
              <button type="button" onClick={handleForceReset} className="app-button bg-orange-600 hover:bg-orange-700 text-white">
                <Icon name="stop" size={14} /> Force Stop
              </button>
            )}
            {!isProcessing && (
              <button type="button" onClick={handleDeleteJob} className="app-button bg-red-600/90 hover:bg-red-600 text-white">
                <Icon name="trash" size={14} /> Delete
              </button>
            )}
            {agentStatus?.output_csv && (
              <a href={`${API}/export/csv?job_id=${jobId}&_t=${getToken()}`} className="app-button bg-emerald-600 hover:bg-emerald-700 text-white" download>
                <Icon name="download" size={14} /> CSV
              </a>
            )}
            {agentStatus?.status === 'completed' && (
              <a href={`${API}/export/excel?job_id=${jobId}&_t=${getToken()}`} className="app-button bg-emerald-600 hover:bg-emerald-700 text-white" download>
                <Icon name="sheet" size={14} /> Excel
              </a>
            )}
            {agentStatus?.output_kmz && (
              <a href={`${API}/export/kmz?job_id=${jobId}&_t=${getToken()}`} className="app-button bg-emerald-600 hover:bg-emerald-700 text-white" download>
                <Icon name="download" size={14} /> KMZ
              </a>
            )}
          </div>
        </div>
      </header>

      <div className="project-detail-grid flex-1 min-h-0">
        <div className="project-detail-main app-card rounded-xl min-w-0">
          <RecordsTable key={recordsKey} jobId={jobId} navigate={navigate} />
        </div>

        {agentStatus?.agents && (
          <aside className="project-detail-aside app-card rounded-xl">
            <AgentProgressPanel
              agents={agentStatus.agents}
              flowConfig={appliedFlowConfig}
              currentAgentId={agentStatus?.current_agent}
              status={agentStatus?.status || job.status}
              variant="sidebar"
              jobTiming={agentStatus}
            />
          </aside>
        )}
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.ProjectDetailPage = ProjectDetailPage;
