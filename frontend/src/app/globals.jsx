/** @jsx React.createElement */
window.FTTH_APP = window.FTTH_APP || {};
Object.assign(window.FTTH_APP, window.FTTH_UI || {}, window.FTTH || {});

window.FTTH_APP.formatDuration = function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return '';
  const total = Math.max(0, Math.round(Number(seconds)));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  if (minutes < 60) return secs === 0 ? `${minutes}m` : `${minutes}m ${secs}s`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return remMinutes === 0 ? `${hours}h` : `${hours}h ${remMinutes}m`;
};

window.FTTH_APP.parsePipelineTimestamp = function parsePipelineTimestamp(value) {
  if (!value) return null;
  const text = String(value).trim();
  const normalized = text.endsWith('Z') ? text.slice(0, -1) : text;
  const parsed = Date.parse(normalized.endsWith('Z') ? normalized : `${normalized}Z`);
  return Number.isFinite(parsed) ? parsed : null;
};

window.FTTH_APP.agentElapsedSeconds = function agentElapsedSeconds(agent) {
  if (!agent) return null;
  if (agent.elapsed_seconds != null && Number.isFinite(Number(agent.elapsed_seconds))) {
    return Number(agent.elapsed_seconds);
  }
  const started = window.FTTH_APP.parsePipelineTimestamp(agent.started_at);
  if (started == null) return null;
  const status = String(agent.status || agent.effectiveStatus || '').toLowerCase();
  const ended = status === 'completed' || status === 'failed'
    ? window.FTTH_APP.parsePipelineTimestamp(agent.completed_at)
    : Date.now();
  if (ended == null) return null;
  return Math.max(0, Math.round((ended - started) / 1000));
};

window.FTTH_APP.agentElapsedText = function agentElapsedText(agent) {
  if (!agent) return '';
  if (agent.elapsed_time) return agent.elapsed_time;
  const seconds = window.FTTH_APP.agentElapsedSeconds(agent);
  return seconds != null ? window.FTTH_APP.formatDuration(seconds) : '';
};

window.FTTH_APP.timingText = function timingText(agent) {
  if (!agent) return '';
  const elapsed = window.FTTH_APP.agentElapsedText(agent);
  const status = String(agent.effectiveStatus || agent.status || '').toLowerCase();
  if (elapsed && (status === 'completed' || status === 'failed')) return elapsed;
  if (elapsed && status === 'running') return `${elapsed} · live`;
  if (status === 'running' && agent.eta_time) return `ETA ${agent.eta_time}`;
  if (agent.estimated_time && (status === 'pending' || status === 'not_selected')) return `Est. ${agent.estimated_time}`;
  if (elapsed) return elapsed;
  return '';
};

window.FTTH_APP.pipelineTotalTimeText = function pipelineTotalTimeText(agentStatus) {
  if (!agentStatus) return '';
  if (agentStatus.total_elapsed_time) return agentStatus.total_elapsed_time;
  if (agentStatus.total_elapsed_seconds != null) {
    return window.FTTH_APP.formatDuration(agentStatus.total_elapsed_seconds);
  }

  const status = String(agentStatus.status || '').toLowerCase();
  const agents = agentStatus.agents || [];

  let started = window.FTTH_APP.parsePipelineTimestamp(agentStatus.pipeline_started_at);
  let ended = (status === 'completed' || status === 'failed')
    ? window.FTTH_APP.parsePipelineTimestamp(agentStatus.pipeline_completed_at)
    : null;

  if (started == null && agents.length) {
    const starts = agents
      .map((a) => window.FTTH_APP.parsePipelineTimestamp(a.started_at))
      .filter((v) => v != null);
    if (starts.length) started = Math.min(...starts);
  }

  if (ended == null && (status === 'completed' || status === 'failed') && agents.length) {
    const ends = agents
      .map((a) => window.FTTH_APP.parsePipelineTimestamp(a.completed_at))
      .filter((v) => v != null);
    if (ends.length) ended = Math.max(...ends);
  }

  if (started == null) return '';

  const endMs = ended != null
    ? ended
    : (status === 'processing' ? Date.now() : null);
  if (endMs == null) return '';

  return window.FTTH_APP.formatDuration(Math.max(0, Math.round((endMs - started) / 1000)));
};
