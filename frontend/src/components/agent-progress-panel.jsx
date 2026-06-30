/** @jsx React.createElement */

const { useState, useEffect } = React;

const {
  Icon,
  timingText,
  pipelineTotalTimeText,
  enrichAgentsWithFlow,
  computeSelectedPipelineProgress,
} = window.FTTH_APP;

function shortAgentLabel(name) {
  const text = String(name || '');
  const match = text.match(/^Agent\s+[\d-]+:\s*(.+)$/i);
  return match ? match[1] : text.replace(/^Agent\s+[\d-]+:\s*/i, '');
}

function agentIndexLabel(agentId) {
  const match = String(agentId || '').match(/agent(\d+(?:_\d+)?)/i);
  if (!match) return '';
  return match[1].replace('_', '-');
}

function StatusIcon({ status, selectedInFlow, size = 12 }) {
  if (!selectedInFlow) {
    return (
      <span className="pipeline-status-dot pipeline-status-dot--off" aria-hidden="true">
        <Icon name="x" size={size - 2} />
      </span>
    );
  }
  if (status === 'completed') {
    return (
      <span className="pipeline-status-dot pipeline-status-dot--done" aria-hidden="true">
        <Icon name="check" size={size - 1} />
      </span>
    );
  }
  if (status === 'running') {
    return (
      <span className="pipeline-status-dot pipeline-status-dot--run" aria-hidden="true">
        <Icon name="refresh" size={size - 1} className="animate-spin" />
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="pipeline-status-dot pipeline-status-dot--fail" aria-hidden="true">
        <Icon name="x" size={size - 2} />
      </span>
    );
  }
  return <span className="pipeline-status-dot pipeline-status-dot--idle" aria-hidden="true" />;
}

function PipelineHeroProgress({ pct = 0, isActive = false, segments = [] }) {
  const safePct = Math.max(0, Math.min(100, Number(pct) || 0));
  return (
    <div className="pipeline-hero">
      <div className="pipeline-hero-top">
        <span className="pipeline-hero-label">Overall progress</span>
        <span className="pipeline-hero-value tabular-nums">{safePct}%</span>
      </div>
      <div className={`pipeline-hero-track${isActive ? ' is-live' : ''}`} role="progressbar" aria-valuenow={safePct} aria-valuemin={0} aria-valuemax={100}>
        <div className="pipeline-hero-glow" style={{ width: `${safePct}%` }} />
        <div className="pipeline-hero-fill" style={{ width: `${safePct}%` }} />
      </div>
      {segments.length > 0 && (
        <div className="pipeline-segments pipeline-segments--hero">
          {segments.map((seg) => (
            <div
              key={seg.agent_id}
              className={`pipeline-segment pipeline-segment--${seg.segmentStatus || 'pending'}${seg.isCurrent ? ' pipeline-segment--current' : ''}${isActive && seg.segmentStatus === 'running' ? ' pipeline-segment--pulse' : ''}`}
              title={`${shortAgentLabel(seg.agent_name)}${seg.segmentStatus === 'running' ? ` (${seg.progress || 0}%)` : ''}`}
              style={seg.segmentStatus === 'running' ? { '--seg-progress': `${Math.max(12, seg.progress || 0)}%` } : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function flowStatusTag(agent, rowStatus) {
  if (!agent.selectedInFlow) {
    return <span className="pipeline-tag pipeline-tag-off">Off</span>;
  }
  if (rowStatus === 'completed') return <span className="pipeline-tag pipeline-tag-on">Done</span>;
  if (rowStatus === 'running') return <span className="pipeline-tag pipeline-tag-live">Live</span>;
  if (rowStatus === 'failed') return <span className="pipeline-tag pipeline-tag-fail">Failed</span>;
  return <span className="pipeline-tag pipeline-tag-on">In flow</span>;
}

function AgentProgressPanel({
  agents = [],
  compact = false,
  flowConfig = null,
  currentAgentId = null,
  status = null,
  variant = 'default',
  jobTiming = null,
}) {
  const enrichedAgents = enrichAgentsWithFlow(agents, flowConfig, currentAgentId, status);
  const pipelineProgress = computeSelectedPipelineProgress(enrichedAgents, status);
  const { segments, runningAgent, selectedCount, completedCount, pct } = pipelineProgress;
  const jobStatus = String((jobTiming && jobTiming.status) || status || '').toLowerCase();
  const isLive = jobStatus === 'processing';
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!isLive) return undefined;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [isLive]);

  const timingSource = jobTiming || { status, agents: enrichedAgents };
  const totalTimeText = pipelineTotalTimeText(timingSource);
  const isComplete = jobStatus === 'completed' || jobStatus === 'failed';
  const offCount = enrichedAgents.filter((a) => !a.selectedInFlow).length;

  if (compact) {
    return (
      <div className="pipeline-compact-list">
        {totalTimeText && (
          <div className="pipeline-total-time pipeline-total-time--compact">
            <span>Total</span>
            <span className="tabular-nums">{totalTimeText}{isLive ? ' · live' : ''}</span>
          </div>
        )}
        {enrichedAgents.map((agent) => (
          <div
            key={agent.agent_id}
            className={`pipeline-compact-row${agent.isCurrent ? ' is-current' : ''}${agent.selectedInFlow ? ' is-on' : ' is-off'}`}>
            <StatusIcon status={agent.effectiveStatus} selectedInFlow={agent.selectedInFlow} size={11} />
            <span className="pipeline-compact-name">{shortAgentLabel(agent.agent_name)}</span>
            {agent.selectedInFlow && timingText(agent) && (
              <span className="pipeline-compact-time tabular-nums">{timingText(agent)}</span>
            )}
          </div>
        ))}
      </div>
    );
  }

  if (variant === 'sidebar') {
    return (
      <div className="pipeline-sidebar">
        <div className="pipeline-sidebar-head">
          <div className="pipeline-sidebar-title-row">
            <div>
              <div className="pipeline-sidebar-title">Pipeline</div>
              <div className="pipeline-sidebar-subtitle tabular-nums">
                {completedCount}/{selectedCount} agents complete
              </div>
            </div>
          </div>

          <div className="pipeline-flow-legend">
            <span className="pipeline-legend-item pipeline-legend-item--on">
              <span className="pipeline-legend-dot" /> In flow ({selectedCount})
            </span>
            <span className="pipeline-legend-item pipeline-legend-item--off">
              <span className="pipeline-legend-dot" /> Off ({offCount})
            </span>
          </div>

          <div className="pipeline-total-row">
            <div className="pipeline-total-row-label">
              <Icon name="clock" size={13} />
              <span>{isComplete ? 'Total time taken' : isLive ? 'Elapsed time' : 'Total time taken'}</span>
            </div>
            <div className="pipeline-total-row-value tabular-nums">
              {totalTimeText || (isLive ? '0s' : '—')}
              {isLive && totalTimeText && <span className="pipeline-live-dot pipeline-live-dot--inline" />}
            </div>
          </div>

          <PipelineHeroProgress pct={pct} isActive={status === 'processing'} segments={segments} />
        </div>

        {runningAgent && status === 'processing' && runningAgent.selectedInFlow && (
          <div className="pipeline-current-banner">
            <StatusIcon status="running" selectedInFlow size={14} />
            <div className="min-w-0 flex-1">
              <div className="pipeline-current-label">Running now</div>
              <div className="pipeline-current-name truncate">{shortAgentLabel(runningAgent.agent_name)}</div>
            </div>
            <div className="pipeline-current-side text-right">
              <div className="pipeline-current-pct tabular-nums">{runningAgent.progress || 0}%</div>
              {timingText(runningAgent) && (
                <div className="pipeline-current-time tabular-nums">{timingText(runningAgent)}</div>
              )}
            </div>
          </div>
        )}

        <div className="pipeline-agent-list">
          {enrichedAgents.map((agent) => {
            const inFlow = agent.selectedInFlow;
            const rowStatus = inFlow ? agent.effectiveStatus : 'not_selected';
            const timeLabel = inFlow ? timingText(agent) : '';
            return (
              <div
                key={agent.agent_id}
                className={`pipeline-agent-card${agent.isCurrent ? ' is-current' : ''}${inFlow ? ' is-on' : ' is-off'}`}
                title={agent.description || agent.agent_name}>
                <div className="pipeline-agent-card-accent" aria-hidden="true" />
                <StatusIcon status={rowStatus} selectedInFlow={inFlow} size={13} />
                <div className="pipeline-agent-main min-w-0">
                  <div className="pipeline-agent-topline">
                    <span className="pipeline-agent-index">{agentIndexLabel(agent.agent_id)}</span>
                    <span className="pipeline-agent-name truncate">{shortAgentLabel(agent.agent_name)}</span>
                    {flowStatusTag(agent, rowStatus)}
                  </div>
                  {inFlow && rowStatus === 'running' && (
                    <div className="pipeline-agent-bar">
                      <div className="pipeline-agent-bar-fill" style={{ width: `${agent.progress || 0}%` }} />
                    </div>
                  )}
                  {inFlow && rowStatus === 'running' && (
                    <div className="pipeline-agent-records tabular-nums">
                      {agent.records_processed}/{agent.records_total} records
                    </div>
                  )}
                </div>
                <div className="pipeline-agent-side shrink-0">
                  {timeLabel ? (
                    <span className="pipeline-agent-time tabular-nums">{timeLabel}</span>
                  ) : (
                    <span className="pipeline-agent-time pipeline-agent-time--empty">{inFlow ? '—' : ''}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="pipeline-default">
      <PipelineHeroProgress pct={pct} isActive={status === 'processing'} segments={segments} />
      <div className="space-y-1 mt-3">
        {enrichedAgents.map((agent) => (
          <div key={agent.agent_id} className={`pipeline-agent-card${agent.selectedInFlow ? ' is-on' : ' is-off'}`}>
            <StatusIcon status={agent.effectiveStatus} selectedInFlow={agent.selectedInFlow} />
            <span className="text-sm flex-1 truncate">{agent.agent_name}</span>
            {agent.selectedInFlow && timingText(agent) && (
              <span className="pipeline-agent-time tabular-nums">{timingText(agent)}</span>
            )}
            {flowStatusTag(agent, agent.effectiveStatus)}
          </div>
        ))}
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.AgentProgressPanel = AgentProgressPanel;
window.FTTH_APP.enrichAgentsWithFlow = enrichAgentsWithFlow;
window.FTTH_APP.computeSelectedPipelineProgress = computeSelectedPipelineProgress;
