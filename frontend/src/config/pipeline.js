/**
 * Pipeline defaults, flow builder config, and agent merge helpers.
 */
(function (global) {
const DEFAULT_PIPELINE_OPTIONS = {
  agent0: {
    enabled: true,
    reverse_geocode: true,
    grid_step: 0.00025,
    max_candidates_per_polygon: 0,
    max_candidates_per_job: 0,
    dedup_distance_m: 20,
    multi_unit_suffixing: true,
    max_units_per_base_address: 4,
    validate_discovered: false,
    validate_with_smarty: true,
    validate_with_regrid: false,
    validation_threshold: 90,
    exclude_discovered_from_agent1: true,
  },
  agent2: {
    enabled: true,
    reverse_geocoder: true,
    coord_validation: true,
    google_geocoding: true,
    osm_geocoding: true,
    street_interpolation: true,
    coord_match_threshold_m: 100,
    coord_mismatch_warn_m: 500,
  },
  agent1: { enabled: true, smarty: true, melissa: false, use_cache: true },
  agent3: { enabled: true, regrid: true, tigerline: true, nominatim: true },
  agent4: { enabled: true, building_footprint: true, building_provider: 'microsoft' },
  agent5_0: {
    enabled: false,
    street_view: true,
    paddle_ocr: true,
    ocr_engine: 'vision_primary',
    ollama_guidance: true,
    max_workers: 4,
    max_iterations: 6,
    confidence_gate: 90,
  },
  agent5: {
    enabled: true,
    analysis_mode: 'hybrid',
    street_view: true,
    satellite_fallback: true,
    azure_vision: true,
    gpt_vision: true,
    llm_provider: 'online',
    ollama_vision_model: 'qwen2.5vl:latest',
  },
  agent6: { enabled: true, ftth_synthesis: true },
  agent7: {
    enabled: false,
    search_distance_m: 60,
    samples_per_address: 4,
    max_candidates_per_job: 500,
    concurrency: 8,
    dedup_distance_m: 25,
    validation_provider: 'reverse_rooftop',
    validation_threshold: 90,
    use_validation_cache: false,
    microsoft_building_enrichment: true,
  },
};

const PIPELINE_AGENT_SECTIONS = [
  {
    id: 'agent0',
    name: 'Agent 0: House Discovery',
    description: 'Discover households inside uploaded polygon layers',
    options: [
      { key: 'reverse_geocode', label: 'Reverse geocode', hint: 'Use Google reverse geocoding for candidate points', type: 'checkbox' },
      { key: 'grid_step', label: 'Grid step', hint: 'Candidate spacing in degrees', type: 'number', min: 0.00001, max: 0.01, step: 0.00001 },
      { key: 'max_candidates_per_polygon', label: 'Max per polygon', hint: '0 = scan all candidate points in each polygon', type: 'number', min: 0, max: 1000000, step: 1 },
      { key: 'max_candidates_per_job', label: 'Max per job', hint: '0 = no job-level candidate cap', type: 'number', min: 0, max: 10000000, step: 1 },
      { key: 'dedup_distance_m', label: 'Dedup meters', hint: 'Treat nearby discovered points as duplicates', type: 'number', min: 1, max: 200, step: 1 },
      { key: 'multi_unit_suffixing', label: 'Multi-unit suffixing', hint: 'Use ATTOM unit count to create addresses like 4341-1 and 4341-2', type: 'checkbox' },
      { key: 'max_units_per_base_address', label: 'Max unit suffixes', hint: 'Safety cap for generated -1, -2 unit addresses', type: 'number', min: 2, max: 20, step: 1 },
      { key: 'validate_discovered', label: 'Validate new addresses', hint: 'Validate Agent 0-discovered rows before keeping them as new addresses', type: 'checkbox' },
      { key: 'validate_with_smarty', label: 'A0 validation: Smarty', hint: 'Use Smarty inside Agent 0 validation for discovered rows', type: 'checkbox' },
      { key: 'validate_with_regrid', label: 'A0 validation: Regrid', hint: 'Use Regrid parcel confidence inside Agent 0 validation for discovered rows', type: 'checkbox' },
      { key: 'validation_threshold', label: 'A0 validation threshold', hint: 'Delete discovered rows when selected validators all score below this percent', type: 'number', min: 1, max: 100, step: 1 },
      { key: 'exclude_discovered_from_agent1', label: 'Skip normal Agent 2 for A0 rows', hint: 'Do not send Agent 0 records to the regular Smarty/Melissa stage; other agents can still use them', type: 'checkbox' },
    ],
  },
  {
    id: 'agent2',
    name: 'Agent 1: Geocoding',
    description: 'Reverse + forward geocoding (runs first)',
    options: [
      { key: 'reverse_geocoder', label: 'Reverse geocoder', hint: 'Coords -> address (Nominatim / Google)' },
      { key: 'coord_validation', label: 'Coord validation', hint: 'Compare uploaded address vs coordinates' },
      { key: 'google_geocoding', label: 'Google geocoding', hint: 'Forward geocode via Google Maps API' },
      { key: 'osm_geocoding', label: 'OSM / Nominatim', hint: 'Forward geocode via OpenStreetMap' },
      { key: 'street_interpolation', label: 'Street interpolation', hint: 'Estimate location from street centerline' },
      { key: 'coord_match_threshold_m', label: 'Match distance (m)', hint: 'Distance at or below this is treated as coordinate match', type: 'number', min: 1, max: 5000, step: 1 },
      { key: 'coord_mismatch_warn_m', label: 'Warning distance (m)', hint: 'Distances above match and below warning lower confidence before hard mismatch', type: 'number', min: 1, max: 20000, step: 1 },
    ],
  },
  {
    id: 'agent1',
    name: 'Agent 2: Address Validation',
    description: 'Smarty + Melissa postal validation',
    options: [
      { key: 'smarty', label: 'Smarty Streets', hint: 'Primary address validation provider' },
      { key: 'melissa', label: 'Melissa', hint: 'Dual-provider arbitration when enabled with Smarty' },
      { key: 'use_cache', label: 'Validation cache', hint: 'Reuse cached Smarty/Melissa results' },
    ],
  },
  {
    id: 'agent3',
    name: 'Agent 3: Parcel & Land Use',
    description: 'Parcel lookup fallbacks',
    options: [
      { key: 'regrid', label: 'Regrid API', hint: 'Primary parcel / owner / land-use lookup' },
      { key: 'tigerline', label: 'TIGER / census', hint: 'County boundary fallback' },
      { key: 'nominatim', label: 'Nominatim', hint: 'County name last-resort fallback' },
    ],
  },
  {
    id: 'agent4',
    name: 'Agent 4: Building',
    description: 'Building footprint enrichment before final synthesis',
    options: [
      { key: 'building_footprint', label: 'Building footprint', hint: 'Spatial footprint enrichment & classification' },
      {
        key: 'building_provider',
        label: 'Footprint provider',
        hint: 'Choose building footprint source for Agent 4',
        type: 'select',
        choices: [
          { value: 'microsoft', label: 'Microsoft Building' },
          { value: 'attom', label: 'ATTOM Property / Building API' },
        ],
      },
    ],
  },
  {
    id: 'agent5_0',
    name: 'Agent 5-0: Offline OCR',
    description: 'qwen2.5vl vision OCR (primary) + PaddleOCR fallback for low-confidence rows',
    options: [
      { key: 'street_view', label: 'Street View', hint: 'Fetch Street View images for offline OCR', type: 'checkbox' },
      { key: 'paddle_ocr', label: 'PaddleOCR', hint: 'Enable PaddleOCR fallback (used only if Vision OCR confidence is below threshold)', type: 'checkbox' },
      {
        key: 'ocr_engine',
        label: 'OCR engine',
        hint: 'Vision Primary = qwen2.5vl reads first, PaddleOCR is fallback. Paddle Primary = legacy order. Vision Only = Ollama only, no PaddleOCR.',
        type: 'select',
        choices: [
          { value: 'vision_primary', label: 'Vision Primary (qwen2.5vl -> PaddleOCR fallback)' },
          { value: 'paddle_primary', label: 'Paddle Primary (PaddleOCR -> Ollama guidance)' },
          { value: 'vision_only',    label: 'Vision Only (qwen2.5vl, no PaddleOCR)' },
        ],
      },
      { key: 'max_workers', label: 'Workers', hint: 'Parallel records for Agent 5-0', type: 'number', min: 1, max: 16, step: 1 },
      { key: 'max_iterations', label: 'Max iterations', hint: 'Retry zoom / heading search per record', type: 'number', min: 1, max: 10, step: 1 },
      { key: 'confidence_gate', label: 'Prior confidence gate', hint: 'Only run when Agent 1, 2, or 3 confidence is below this percent', type: 'number', min: 1, max: 100, step: 1 },
    ],
  },
  {
    id: 'agent5',
    name: 'Agent 5: Street View',
    description: 'Imagery analysis',
    options: [
      {
        key: 'analysis_mode',
        label: 'Mode',
        hint: 'Offline=PaddleOCR+Qwen, Online=Azure OCR+Azure LLM, Hybrid=offline + online readers',
        type: 'select',
        choices: [
          { value: 'hybrid', label: 'Hybrid' },
          { value: 'offline', label: 'Offline' },
          { value: 'online', label: 'Online' },
        ],
      },
      { key: 'street_view', label: 'Street View', hint: 'Google Street View imagery + variants' },
      { key: 'satellite_fallback', label: 'Satellite / ESRI', hint: 'Real aerial imagery for every coordinate (OCR reads roof numbers when visible)' },
    ],
  },
  {
    id: 'agent6',
    name: 'Agent 6: FTTH Final',
    description: 'Final suitability score',
    options: [
      { key: 'ftth_synthesis', label: 'FTTH synthesis', hint: 'Combine agents 1-5 into priority score' },
    ],
  },
  {
    id: 'agent7',
    name: 'Agent 7: Neighborhood Discovery',
    description: 'Find polygon addresses missing from Final columns',
    options: [
      { key: 'search_distance_m', label: 'Search distance (m)', hint: 'Distance from each Final coordinate to sample nearby addresses', type: 'number', min: 10, max: 500, step: 5 },
      { key: 'samples_per_address', label: 'Samples per Final address', hint: 'Bounded neighborhood samples; maximum 8', type: 'number', min: 1, max: 8, step: 1 },
      { key: 'max_candidates_per_job', label: 'Max candidates per job', hint: 'Hard cap on reverse-geocoding requests', type: 'number', min: 1, max: 10000, step: 1 },
      { key: 'concurrency', label: 'Parallel requests', hint: 'Concurrent reverse-geocoding requests', type: 'number', min: 1, max: 16, step: 1 },
      { key: 'dedup_distance_m', label: 'Final dedup distance (m)', hint: 'Discard discoveries near any Final coordinate', type: 'number', min: 1, max: 200, step: 1 },
      {
        key: 'validation_provider',
        label: 'Validation provider',
        hint: 'Validate each new Agent 7 address with the selected provider',
        type: 'select',
        choices: [
          { value: 'reverse_rooftop', label: 'Reverse geocode ROOFTOP' },
          { value: 'smarty', label: 'Smarty' },
          { value: 'regrid', label: 'Regrid' },
        ],
      },
      { key: 'validation_threshold', label: 'Validation threshold', hint: 'Delete Agent 7 rows unless selected provider confidence is greater than this percent', type: 'number', min: 1, max: 100, step: 1 },
      { key: 'use_validation_cache', label: 'A7 validation cache', hint: 'Reuse cached Smarty results while validating Agent 7 rows', type: 'checkbox' },
      { key: 'microsoft_building_enrichment', label: 'Microsoft building enrichment', hint: 'Classify each new Agent 7 record with Microsoft building footprints', type: 'checkbox' },
    ],
  },
];

const FLOW_AGENT_TO_PIPELINE_ID = {
  agent0_house_discovery: 'agent0',
  agent2_geocoding: 'agent2',
  agent1_geocoding: 'agent2',
  agent1_reverse_geocoding: 'agent2',
  geocoding: 'agent2',
  reverse_geocoder: 'agent2',
  agent1_address_validator: 'agent1',
  address_validator: 'agent1',
  agent2_address_validation: 'agent1',
  agent2_address_validator: 'agent1',
  address_validation: 'agent1',
  smarty: 'agent1',
  melissa: 'agent1',
  agent3_parcel: 'agent3',
  agent4_building: 'agent4',
  agent5_0_offline_ocr: 'agent5_0',
  agent50_offline_ocr: 'agent5_0',
  agent5_offline_ocr: 'agent5_0',
  agent5_streetview: 'agent5',
  agent6_finalization: 'agent6',
  agent6_final: 'agent6',
  agent7_neighborhood_discovery: 'agent7',
};

const REQUIRED_FLOW_AGENTS = [
  {
    agent_name: 'agent0_house_discovery',
    enabled: true,
    data_source: 'database',
    confidence_thresholds: {},
    order: 0,
  },
  {
    agent_name: 'agent7_neighborhood_discovery',
    enabled: false,
    data_source: 'all_agents',
    confidence_thresholds: {},
    order: 7,
  },
  {
    agent_name: 'agent5_0_offline_ocr',
    enabled: false,
    data_source: 'database',
    confidence_thresholds: {},
    order: 5,
  },
];

function sectionHasActiveMode(sectionId, sectionOpts) {
  if (!sectionOpts?.enabled) return true;
  if (sectionId === 'agent1') return !!(sectionOpts.smarty || sectionOpts.melissa);
  if (sectionId === 'agent5_0') {
    if (!sectionOpts.street_view) return false;
    // vision_only and vision_primary can run without paddle_ocr
    const engine = sectionOpts.ocr_engine || 'vision_primary';
    if (engine === 'paddle_primary') return !!sectionOpts.paddle_ocr;
    return true;
  }
  const toggles = (PIPELINE_AGENT_SECTIONS.find((s) => s.id === sectionId)?.options || [])
    .filter((opt) => opt.type !== 'number');
  if (!toggles.length) return true;
  return toggles.some(({ key }) => sectionOpts[key]);
}

function flowAgentDefaults(agentName) {
  const sectionId = FLOW_AGENT_TO_PIPELINE_ID[agentName];
  if (!sectionId) return { enabled: true };
  return { ...DEFAULT_PIPELINE_OPTIONS[sectionId] };
}

function normalizeDataSource(value) {
  const v = String(value || 'database').toLowerCase();
  return (v === 'raw_input' || v === 'db') ? 'database' : v;
}

function canonicalFlowAgentName(agentName) {
  const name = String(agentName || '').trim();
  if (['agent1_geocoding', 'agent1_reverse_geocoding', 'geocoding', 'reverse_geocoder'].includes(name)) return 'agent2_geocoding';
  if (['address_validator', 'agent2_address_validation', 'agent2_address_validator', 'address_validation', 'smarty', 'melissa'].includes(name)) return 'agent1_address_validator';
  if (['agent50_offline_ocr', 'agent5_offline_ocr'].includes(name)) return 'agent5_0_offline_ocr';
  return name;
}

function normalizeFlowAgents(agents) {
  const normalized = (agents || []).map((agent) => {
    const canonicalName = canonicalFlowAgentName(agent.agent_name);
    const defaults = flowAgentDefaults(canonicalName);
    const pipelineOptions = { ...defaults, ...(agent.pipeline_options || {}) };
    const enabled = agent.enabled ?? pipelineOptions.enabled ?? true;
    pipelineOptions.enabled = enabled;
    return {
      ...agent,
      agent_name: canonicalName,
      enabled,
      data_source: normalizeDataSource(agent.data_source),
      pipeline_options: pipelineOptions,
    };
  });
  for (const required of REQUIRED_FLOW_AGENTS) {
    if (!normalized.some((agent) => agent.agent_name === required.agent_name)) {
      const defaults = flowAgentDefaults(required.agent_name);
      normalized.unshift({
        ...required,
        pipeline_options: { ...defaults, enabled: required.enabled },
      });
    }
  }
  return normalized;
}

function _activeFlowSubchecks(cfg) {
  const p = cfg?.pipeline_options || {};
  return Object.keys(p).filter(function (k) {
    return k !== 'enabled' && typeof p[k] === 'boolean' && p[k];
  });
}

function getAgentFlowSelection(agentId, flowConfig) {
  const flowAgents = Array.isArray(flowConfig?.agents) ? flowConfig.agents : [];
  if (!flowAgents.length) return { selected: true, subchecks: [] };
  const flowByName = new Map(flowAgents.map(function (a) { return [a.agent_name, a]; }));

  const id = String(agentId || '');
  if (id === 'address_validator') return getAgentFlowSelection('agent1_address_validator', flowConfig);

  const pick = function (name) {
    const cfg = flowByName.get(name);
    return { selected: !!cfg?.enabled, subchecks: _activeFlowSubchecks(cfg) };
  };

  if (id === 'agent0_house_discovery') return pick('agent0_house_discovery');
  if (id === 'agent2_geocoding') return pick('agent2_geocoding');
  if (id === 'agent1_address_validator') return pick('agent1_address_validator');
  if (id === 'agent3_parcel') return pick('agent3_parcel');
  if (id === 'agent4_building') return pick('agent4_building');
  if (id === 'agent5_0_offline_ocr') return pick('agent5_0_offline_ocr');
  if (id === 'agent5_streetview') return pick('agent5_streetview');
  if (id === 'agent6_final' || id === 'agent6_finalization') {
    const cfg = flowByName.get('agent6_finalization') || flowByName.get('agent6_final');
    return { selected: !!cfg?.enabled, subchecks: _activeFlowSubchecks(cfg) };
  }
  if (id === 'agent7_neighborhood_discovery') return pick('agent7_neighborhood_discovery');
  return { selected: true, subchecks: [] };
}

function enrichAgentsWithFlow(agents, flowConfig, currentAgentId, status) {
  const isProcessing = String(status || '').toLowerCase() === 'processing';
  return (agents || []).map(function (agent) {
    const flow = getAgentFlowSelection(agent.agent_id, flowConfig);
    const effectiveStatus = flow.selected ? agent.status : 'not_selected';
    const isCurrent = currentAgentId === agent.agent_id && flow.selected && (
      effectiveStatus === 'running'
      || (isProcessing && effectiveStatus !== 'completed' && effectiveStatus !== 'not_selected' && effectiveStatus !== 'failed')
    );
    return Object.assign({}, agent, {
      selectedInFlow: flow.selected,
      subchecks: flow.subchecks,
      effectiveStatus: effectiveStatus,
      isCurrent: isCurrent,
    });
  });
}

function computeSelectedPipelineProgress(enrichedAgents, status) {
  const selected = (enrichedAgents || []).filter(function (a) { return a.selectedInFlow; });
  const selectedCount = selected.length;
  const normalizedStatus = String(status || '').toLowerCase();

  if (selectedCount === 0) {
    return {
      pct: normalizedStatus === 'completed' ? 100 : 0,
      selectedCount: 0,
      completedCount: 0,
      runningAgent: null,
      segments: [],
    };
  }

  if (normalizedStatus === 'completed') {
    return {
      pct: 100,
      selectedCount: selectedCount,
      completedCount: selectedCount,
      runningAgent: null,
      segments: selected.map(function (a) { return Object.assign({}, a, { segmentStatus: 'completed' }); }),
    };
  }

  var completedCount = 0;
  var runningAgent = null;
  var runningFraction = 0;

  var segments = selected.map(function (agent) {
    var segmentStatus = 'pending';
    if (agent.effectiveStatus === 'completed') {
      completedCount += 1;
      segmentStatus = 'completed';
    } else if (agent.effectiveStatus === 'running') {
      runningAgent = agent;
      runningFraction = (Number(agent.progress) || 0) / 100;
      segmentStatus = 'running';
    } else if (agent.effectiveStatus === 'failed') {
      segmentStatus = 'failed';
    } else if (agent.isCurrent) {
      runningAgent = agent;
      runningFraction = (Number(agent.progress) || 0) / 100;
      segmentStatus = 'running';
    }
    return Object.assign({}, agent, { segmentStatus: segmentStatus, isCurrent: !!agent.isCurrent });
  });

  if (!runningAgent) {
    runningAgent = selected.find(function (a) {
      return a.effectiveStatus === 'running' || a.isCurrent;
    }) || null;
    if (runningAgent) runningFraction = (Number(runningAgent.progress) || 0) / 100;
  }

  var pct = Math.round(((completedCount + runningFraction) / selectedCount) * 100);
  if (normalizedStatus === 'processing') pct = Math.min(99, pct);
  if (normalizedStatus === 'failed') pct = Math.min(pct, 99);

  return {
    pct: pct,
    selectedCount: selectedCount,
    completedCount: completedCount,
    runningAgent: runningAgent,
    segments: segments,
  };
}

function showProcessButton(agentStatus) {
  const s = agentStatus?.status;
  return s !== 'processing' && s !== 'completed' && s !== 'failed';
}

function showReProcessButton(agentStatus) {
  const s = agentStatus?.status;
  return s !== 'processing' && (s === 'completed' || s === 'failed');
}

function mergeAgentLists(prevAgents, nextAgents) {
  prevAgents = prevAgents || [];
  nextAgents = nextAgents || [];
  if (!Array.isArray(prevAgents) || prevAgents.length === 0) return nextAgents;
  if (!Array.isArray(nextAgents) || nextAgents.length === 0) return prevAgents;
  const prevById = new Map(prevAgents.map(function (agent) { return [agent.agent_id, agent]; }));
  return nextAgents.map(function (agent) {
    const prev = prevById.get(agent.agent_id);
    if (!prev) return agent;
    const nextProgress = Number(agent.progress ?? 0);
    const prevProgress = Number(prev.progress ?? 0);
    return Object.assign({}, prev, agent, {
      progress: Math.max(prevProgress, nextProgress),
      records_processed: Math.max(Number(prev.records_processed ?? 0), Number(agent.records_processed ?? 0)),
      errors: agent.errors || prev.errors || [],
      started_at: prev.started_at || agent.started_at,
      completed_at: agent.completed_at || prev.completed_at,
      elapsed_seconds: Math.max(Number(prev.elapsed_seconds ?? 0), Number(agent.elapsed_seconds ?? 0)) || agent.elapsed_seconds || prev.elapsed_seconds,
      elapsed_time: agent.elapsed_time || prev.elapsed_time,
    });
  });
}

function mergeAgentStatus(prev, next) {
  if (!next) return prev;
  if (!prev) return next;
  if (next.status === 'completed' || next.status === 'failed' || next.done || next.error) return next;
  const prevProgress = Number(prev.overall_progress ?? 0);
  const nextProgress = Number(next.overall_progress ?? 0);
  return Object.assign({}, prev, next, {
    overall_progress: Math.max(prevProgress, nextProgress),
    current_agent: next.current_agent != null ? next.current_agent : prev.current_agent,
    pipeline_started_at: next.pipeline_started_at || prev.pipeline_started_at,
    pipeline_completed_at: next.pipeline_completed_at || prev.pipeline_completed_at,
    total_elapsed_seconds: next.total_elapsed_seconds != null ? next.total_elapsed_seconds : prev.total_elapsed_seconds,
    total_elapsed_time: next.total_elapsed_time || prev.total_elapsed_time,
    agents: mergeAgentLists(prev.agents, next.agents),
  });
}

  global.FTTH = Object.assign({}, global.FTTH || {}, {
    DEFAULT_PIPELINE_OPTIONS: DEFAULT_PIPELINE_OPTIONS,
    PIPELINE_AGENT_SECTIONS: PIPELINE_AGENT_SECTIONS,
    FLOW_AGENT_TO_PIPELINE_ID: FLOW_AGENT_TO_PIPELINE_ID,
    REQUIRED_FLOW_AGENTS: REQUIRED_FLOW_AGENTS,
    sectionHasActiveMode: sectionHasActiveMode,
    flowAgentDefaults: flowAgentDefaults,
    normalizeDataSource: normalizeDataSource,
    canonicalFlowAgentName: canonicalFlowAgentName,
    normalizeFlowAgents: normalizeFlowAgents,
    showProcessButton: showProcessButton,
    showReProcessButton: showReProcessButton,
    mergeAgentLists: mergeAgentLists,
    mergeAgentStatus: mergeAgentStatus,
    getAgentFlowSelection: getAgentFlowSelection,
    enrichAgentsWithFlow: enrichAgentsWithFlow,
    computeSelectedPipelineProgress: computeSelectedPipelineProgress,
  });
})(window);
