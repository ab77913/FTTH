/** @jsx React.createElement */

const { useState, useEffect } = React;

const {
  Icon, ProgressBar, AgentProgressPanel, UploadModal,
  fetchJobs, uploadFile, startProcessing, fetchAgentStatus, deleteJob,
  showProcessButton, showReProcessButton, mergeAgentStatus,
  API, authHeaders, apiFetch,
  PageHeader, StatusBadge, Skeleton, EmptyState, PageLoader, Spinner, useToast, useConfirm,
} = window.FTTH_APP;

function ProjectCardSkeleton() {
  return (
    <div className="app-card rounded-xl p-5 space-y-4">
      <div className="flex justify-between"><Skeleton className="h-5 w-24" /><Skeleton className="h-5 w-16" /></div>
      <Skeleton className="h-6 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-10 w-20" />
      <Skeleton className="h-2 w-full" />
      <div className="flex gap-2"><Skeleton className="h-8 w-16" /><Skeleton className="h-8 w-20" /></div>
    </div>
  );
}

function ProjectsPage({ navigate }) {
  const { toast } = useToast();
  const { confirm } = useConfirm();
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [viewMode, setViewMode] = useState('cards');
  const [showUpload, setShowUpload] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [agentStatuses, setAgentStatuses] = useState({});
  const [expandedJob, setExpandedJob] = useState(null);

  useEffect(() => { loadJobs(); }, []);

  async function loadJobs() {
    setLoadError(null);
    try {
      const data = await fetchJobs();
      setJobs(data);
      const statusPairs = await Promise.all(
        data.map(async (job) => {
          try { return [job.id, await fetchAgentStatus(job.id)]; }
          catch (e) { return [job.id, null]; }
        })
      );
      const merged = {};
      statusPairs.forEach(([jobId, status]) => { if (status) merged[jobId] = status; });
      setAgentStatuses(merged);
    } catch (err) {
      setLoadError(err.message || 'Failed to load projects');
      toast('Could not load projects. Check your connection.', 'error');
    } finally {
      setLoading(false);
    }
  }

  async function handleUpload(file, customerId) {
    const result = await uploadFile(file, customerId);
    setShowUpload(false);
    toast(`Upload complete — ${result.total_records || 0} records ingested`, 'success');
    loadJobs();
  }

  async function handleStartProcessing(jobId) {
    setAgentStatuses(prev => ({
      ...prev,
      [jobId]: { ...(prev[jobId] || {}), status: 'processing', overall_progress: prev[jobId]?.overall_progress ?? 0 },
    }));
    try {
      await startProcessing(jobId);
      toast('Pipeline started', 'success');
      pollAgentStatus(jobId);
    } catch (err) {
      toast(err.message || 'Failed to start processing', 'error');
      pollAgentStatus(jobId);
    }
  }

  async function handleForceResetJob(jobId) {
    const ok = await confirm({
      title: 'Force stop pipeline',
      message: 'Stop this stuck job and mark it as failed so you can re-run or delete it.',
      confirmLabel: 'Force stop',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await apiFetch(`${API}/jobs/${jobId}/force-reset`, { method: 'POST', headers: authHeaders() });
      const status = await fetchAgentStatus(jobId);
      setAgentStatuses(prev => ({ ...prev, [jobId]: status }));
      toast('Job reset — you can re-process or delete', 'warning');
    } catch (err) {
      toast('Failed to reset job: ' + (err.message || err), 'error');
    }
  }

  async function handleDeleteJob(job) {
    const ok = await confirm({
      title: 'Delete project',
      message: `Delete this project and all its records?\n\n${job.source_file}\n${job.id}`,
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteJob(job.id);
      setJobs(prev => prev.filter(j => j.id !== job.id));
      setAgentStatuses(prev => { const next = { ...prev }; delete next[job.id]; return next; });
      toast('Project deleted', 'success');
    } catch (err) {
      toast(err.message || 'Delete failed', 'error');
    }
  }

  function pollAgentStatus(jobId) {
    const interval = setInterval(async () => {
      try {
        const status = await fetchAgentStatus(jobId);
        setAgentStatuses(prev => ({ ...prev, [jobId]: mergeAgentStatus(prev[jobId], status) }));
        if (status.status === 'completed') {
          clearInterval(interval);
          toast('Pipeline completed', 'success');
        }
        if (status.status === 'failed') {
          clearInterval(interval);
          toast('Pipeline failed — check agent logs', 'error');
        }
      } catch (e) { clearInterval(interval); }
    }, 1000);
  }

  const processingJobIds = jobs
    .filter(job => agentStatuses[job.id]?.status === 'processing')
    .map(job => job.id)
    .join('|');

  useEffect(() => {
    if (!processingJobIds) return undefined;
    const ids = processingJobIds.split('|').filter(Boolean);
    const interval = setInterval(async () => {
      const updates = await Promise.all(ids.map(async (jobId) => {
        try { return [jobId, await fetchAgentStatus(jobId)]; }
        catch (e) { return [jobId, null]; }
      }));
      setAgentStatuses(prev => {
        const next = { ...prev };
        updates.forEach(([jobId, status]) => { if (status) next[jobId] = mergeAgentStatus(prev[jobId], status); });
        return next;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [processingJobIds]);

  function getRelativeTime(dateStr) {
    const diffDays = Math.floor((new Date() - new Date(dateStr)) / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return 'today';
    if (diffDays === 1) return '1d ago';
    return `${diffDays}d ago`;
  }

  const filteredJobs = jobs.filter(j =>
    j.source_file.toLowerCase().includes(searchQuery.toLowerCase()) ||
    j.id.toLowerCase().includes(searchQuery.toLowerCase())
  );
  const dashboardStats = {
    projects: jobs.length,
    records: jobs.reduce((sum, job) => sum + (Number(job.row_count) || 0), 0),
    processing: Object.values(agentStatuses).filter(s => s?.status === 'processing').length,
    completed: jobs.filter(job => job.status === 'COMPLETED').length,
  };

  if (loading) {
    return (
      <div className="app-page projects-page-compact">
        <div className="flex-shrink-0 flex items-center justify-between gap-3 mb-2">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-[var(--app-text-soft)]">FTTH Operations</div>
            <h1 className="text-lg font-bold">Projects</h1>
          </div>
        </div>
        <div className="app-page-scroll">
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-2 mb-3">
            {[1, 2, 3, 4].map(i => <div key={i} className="app-card rounded-xl p-3"><Skeleton className="h-3 w-16 mb-2" /><Skeleton className="h-6 w-12" /></div>)}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {[1, 2, 3, 4, 5, 6].map(i => <ProjectCardSkeleton key={i} />)}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-page projects-page-compact">
      <div className="flex-shrink-0 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-2">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wide text-[var(--app-text-soft)]">FTTH Operations</div>
          <h1 className="text-lg font-bold">Projects</h1>
        </div>
        <button type="button" onClick={() => setShowUpload(true)} className="app-button app-button-primary flex items-center gap-2 text-white px-4 py-2 rounded-lg font-semibold self-start sm:self-auto">
          <Icon name="upload" size={15} /> New upload
        </button>
      </div>

      {loadError && (
        <div className="flex-shrink-0 mb-2 p-2 rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-300 text-sm flex items-center justify-between gap-3">
          <span>{loadError}</span>
          <button type="button" className="app-button app-btn-secondary text-xs" onClick={() => { setLoading(true); loadJobs(); }}>Retry</button>
        </div>
      )}

      <div className="app-page-scroll">
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-2 mb-3">
        {[
          ['Projects', dashboardStats.projects, 'Total uploads'],
          ['Records', dashboardStats.records.toLocaleString(), 'Rows in workspace'],
          ['Processing', dashboardStats.processing, 'Active pipelines'],
          ['Completed', dashboardStats.completed, 'Ready outputs'],
        ].map(([label, value, hint]) => (
          <div key={label} className="app-card app-stat rounded-xl">
            <div className="relative z-10">
              <div className="text-[10px] uppercase tracking-wide text-[var(--app-text-soft)]">{label}</div>
              <div className="text-2xl font-bold mt-1">{value}</div>
              <div className="text-[10px] text-[var(--app-text-soft)] mt-0.5">{hint}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="app-card rounded-xl p-2 sm:p-3 flex items-center justify-between gap-2 flex-wrap mb-3">
        <div className="relative flex-1 min-w-full sm:min-w-[260px] max-w-xl">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--app-text-soft)]"><Icon name="search" size={15} /></span>
          <input type="search" placeholder="Search by file name or job ID…" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
            className="app-input pl-9 pr-4 py-2.5 rounded-lg w-full focus:outline-none" aria-label="Search projects" />
        </div>
        <div className="flex items-center gap-1 bg-[var(--app-surface-soft)] rounded-lg p-1 border border-[color:var(--app-border)] w-full sm:w-auto" role="tablist" aria-label="View mode">
          <button type="button" role="tab" aria-selected={viewMode === 'cards'} onClick={() => setViewMode('cards')}
            className={`px-3 py-1.5 rounded text-sm flex flex-1 sm:flex-none items-center justify-center gap-1.5 ${viewMode === 'cards' ? 'bg-[var(--app-surface)] shadow text-[var(--app-text)]' : 'text-[var(--app-text-soft)]'}`}>
            <Icon name="grid" size={14} /> Cards
          </button>
          <button type="button" role="tab" aria-selected={viewMode === 'table'} onClick={() => setViewMode('table')}
            className={`px-3 py-1.5 rounded text-sm flex flex-1 sm:flex-none items-center justify-center gap-1.5 ${viewMode === 'table' ? 'bg-[var(--app-surface)] shadow text-[var(--app-text)]' : 'text-[var(--app-text-soft)]'}`}>
            <Icon name="table" size={14} /> Table
          </button>
        </div>
      </div>

      {filteredJobs.length === 0 ? (
        <EmptyState
          icon={<Icon name="upload" size={28} />}
          title={searchQuery ? 'No matching projects' : 'No projects yet'}
          description={searchQuery ? 'Try a different search term or clear the filter.' : 'Upload a CSV, Excel, KML, or KMZ file to create your first ingestion job.'}
          action={!searchQuery && (
            <button type="button" onClick={() => setShowUpload(true)} className="app-button app-button-primary text-white px-5 py-2.5 rounded-lg font-semibold inline-flex items-center gap-2">
              <Icon name="upload" size={16} /> Upload your first file
            </button>
          )}
        />
      ) : viewMode === 'cards' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 pb-2">
          {filteredJobs.map(job => {
            const agentStatus = agentStatuses[job.id];
            const overallProgress = agentStatus?.overall_progress ?? (job.status === 'COMPLETED' ? 100 : 0);
            const isProcessing = agentStatus?.status === 'processing';
            const displayStatus = isProcessing ? 'PROCESSING' : job.status;
            return (
              <article key={job.id} className="app-card app-card-hover rounded-xl p-4 cursor-pointer group min-w-0" onClick={() => navigate(`/projects/${job.id}`)}>
                <div className="flex items-center justify-between mb-3">
                  <StatusBadge status={displayStatus} pulse={isProcessing} />
                  <span className="text-xs text-[var(--app-text-soft)] font-mono" title={job.project_id || job.id}>Project {String(job.project_id || job.id).slice(0, 8)}</span>
                </div>
                <h3 className="font-semibold mb-2 truncate">{job.source_file}</h3>
                <div className="text-[10px] font-mono text-[var(--app-text-soft)] truncate mb-2" title={job.project_folder || ''}>{job.project_folder || ('uploads/' + (job.project_id || job.id))}</div>
                <div className="flex items-center gap-3 text-xs text-[var(--app-text-soft)] mb-4 min-w-0 flex-wrap">
                  <span className="inline-flex items-center gap-1 min-w-0 truncate"><Icon name="globe" size={13} /> <span className="truncate">{job.customer_id || 'unknown'}</span></span>
                  <span className="inline-flex items-center gap-1 whitespace-nowrap"><Icon name="clock" size={13} /> {getRelativeTime(job.created_at)}</span>
                </div>
                <div className="mb-3">
                  <div className="text-2xl font-bold">{job.row_count}</div>
                  <div className="text-xs text-[var(--app-text-soft)] uppercase tracking-wide">Records</div>
                </div>
                <ProgressBar progress={overallProgress} isActive={isProcessing} label={`${job.row_count} ingested`} />
                <div className="flex items-center gap-2 mt-4 flex-wrap" onClick={e => e.stopPropagation()}>
                  <button type="button" onClick={() => navigate(`/projects/${job.id}/map`)} className="app-button flex items-center gap-1 text-xs bg-[var(--app-surface-soft)] px-3 py-1.5 rounded">
                    <Icon name="map" size={14} /> Map
                  </button>
                  {showProcessButton(agentStatus) && (
                    <button type="button" onClick={() => handleStartProcessing(job.id)} className="app-button flex items-center gap-1 text-xs bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded text-white">
                      <Icon name="play" size={14} /> Process
                    </button>
                  )}
                  {showReProcessButton(agentStatus) && (
                    <button type="button" onClick={() => handleStartProcessing(job.id)} className="app-button flex items-center gap-1 text-xs bg-amber-600 hover:bg-amber-700 px-3 py-1.5 rounded text-white">
                      <Icon name="refresh" size={14} /> Re-Process
                    </button>
                  )}
                  {isProcessing && (
                    <button type="button" onClick={() => handleForceResetJob(job.id)} className="app-button flex items-center gap-1 text-xs bg-orange-600 hover:bg-orange-700 px-3 py-1.5 rounded text-white">
                      <Icon name="stop" size={14} /> Force Stop
                    </button>
                  )}
                  {!isProcessing && (
                    <button type="button" onClick={() => handleDeleteJob(job)} className="app-button flex items-center gap-1 text-xs bg-red-50 dark:bg-red-950/40 hover:bg-red-100 dark:hover:bg-red-950/60 text-red-700 dark:text-red-300 px-3 py-1.5 rounded border border-red-200 dark:border-red-800/50">
                      <Icon name="trash" size={14} /> Delete
                    </button>
                  )}
                </div>
                {isProcessing && (
                  <div className="mt-3 pt-3 border-t border-[color:var(--app-border)]">
                    <button type="button" onClick={(e) => { e.stopPropagation(); setExpandedJob(expandedJob === job.id ? null : job.id); }} className="text-xs text-cyan-600 dark:text-cyan-400 hover:underline inline-flex items-center gap-1">
                      {expandedJob === job.id ? 'Hide agents' : 'Show agents'} <Icon name={expandedJob === job.id ? 'chevronUp' : 'chevronDown'} size={13} />
                    </button>
                    {expandedJob === job.id && agentStatus && (
                      <AgentProgressPanel
                        agents={agentStatus.agents}
                        compact
                        status={agentStatus.status}
                        currentAgentId={agentStatus.current_agent}
                        jobTiming={agentStatus}
                      />
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      ) : (
        <div className="app-card rounded-xl overflow-x-auto">
          <table className="w-full min-w-[920px] text-sm">
            <thead>
              <tr className="border-b border-[color:var(--app-border)] bg-[var(--app-surface-soft)]">
                <th className="text-left px-4 py-3 text-[var(--app-text-soft)] font-semibold text-xs uppercase tracking-wide">File</th>
                <th className="text-left px-4 py-3 text-[var(--app-text-soft)] font-semibold text-xs uppercase tracking-wide">Status</th>
                <th className="text-left px-4 py-3 text-[var(--app-text-soft)] font-semibold text-xs uppercase tracking-wide">Customer</th>
                <th className="text-right px-4 py-3 text-[var(--app-text-soft)] font-semibold text-xs uppercase tracking-wide">Records</th>
                <th className="text-left px-4 py-3 text-[var(--app-text-soft)] font-semibold text-xs uppercase tracking-wide w-48">Progress</th>
                <th className="text-left px-4 py-3 text-[var(--app-text-soft)] font-semibold text-xs uppercase tracking-wide">Created</th>
                <th className="text-left px-4 py-3 text-[var(--app-text-soft)] font-semibold text-xs uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredJobs.map(job => {
                const agentStatus = agentStatuses[job.id];
                const overallProgress = agentStatus?.overall_progress ?? (job.status === 'COMPLETED' ? 100 : 0);
                const isProcessing = agentStatus?.status === 'processing';
                return (
                  <tr key={job.id} className="border-b border-[color:var(--app-border)] hover:bg-[var(--app-surface-soft)] cursor-pointer transition-colors" onClick={() => navigate(`/projects/${job.id}`)}>
                    <td className="px-4 py-3">
                      <div className="font-medium">{job.source_file}</div>
                      <div className="text-xs text-[var(--app-text-soft)] font-mono" title={job.project_folder || ''}>Project {String(job.project_id || job.id).slice(0, 8)}</div>
                    </td>
                    <td className="px-4 py-3"><StatusBadge status={isProcessing ? 'PROCESSING' : job.status} pulse={isProcessing} /></td>
                    <td className="px-4 py-3 text-[var(--app-text-soft)]">{job.customer_id || '—'}</td>
                    <td className="px-4 py-3 text-right font-bold">{job.row_count}</td>
                    <td className="px-4 py-3"><ProgressBar progress={overallProgress} isActive={isProcessing} compact /></td>
                    <td className="px-4 py-3 text-[var(--app-text-soft)] text-xs">{getRelativeTime(job.created_at)}</td>
                    <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                      <div className="flex items-center gap-1">
                        <button type="button" onClick={() => navigate(`/projects/${job.id}/map`)} className="p-1.5 hover:bg-[var(--app-surface-soft)] rounded" title="Map"><Icon name="map" size={15} /></button>
                        {showProcessButton(agentStatus) && <button type="button" onClick={() => handleStartProcessing(job.id)} className="p-1.5 hover:bg-[var(--app-surface-soft)] rounded" title="Process"><Icon name="play" size={15} /></button>}
                        {showReProcessButton(agentStatus) && <button type="button" onClick={() => handleStartProcessing(job.id)} className="p-1.5 hover:bg-amber-500/10 rounded text-amber-500" title="Re-Process"><Icon name="refresh" size={15} /></button>}
                        {isProcessing && <button type="button" onClick={() => handleForceResetJob(job.id)} className="p-1.5 hover:bg-orange-500/10 rounded text-orange-500" title="Force stop"><Icon name="stop" size={15} /></button>}
                        {!isProcessing && <button type="button" onClick={() => handleDeleteJob(job)} className="p-1.5 hover:bg-red-500/10 rounded text-red-500" title="Delete"><Icon name="trash" size={15} /></button>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      </div>

      {showUpload && <UploadModal onClose={() => setShowUpload(false)} onUpload={handleUpload} />}
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.ProjectsPage = ProjectsPage;
