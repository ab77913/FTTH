/** @jsx React.createElement */

const { useState, useEffect } = React;

const { Icon, useTheme, API, deleteRecord, fetchRecords, fetchJobFlow } = window.FTTH_APP;

const PAGE_SIZE_OPTIONS = [25, 50, 100];
const DEFAULT_PAGE_SIZE = 50;

function hasValidCoord(lat, lon) {
  const latNum = Number(lat);
  const lonNum = Number(lon);
  return Number.isFinite(latNum) && Number.isFinite(lonNum) && latNum >= -90 && latNum <= 90 && lonNum >= -180 && lonNum <= 180;
}

function googleMapsCoordUrl(lat, lon) {
  return `https://www.google.com/maps?q=${encodeURIComponent(`${Number(lat)},${Number(lon)}`)}`;
}

function RecordsTable({ jobId, navigate }) {
  const { isDark } = useTheme();
  const [data, setData] = useState(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [flowConfig, setFlowConfig] = useState(null);

  const FLOW_SOURCE_TO_AGENT = {
    agent0: 'agent0_house_discovery',
    agent1: 'agent1_address_validator',
    agent2: 'agent2_geocoding',
    agent3: 'agent3_parcel',
    agent4: 'agent4_building',
    agent5_0: 'agent5_0_offline_ocr',
    agent5: 'agent5_streetview',
    agent6: 'agent6_finalization',
    agent7: 'agent7_neighborhood_discovery',
  };

  const DEMO_COLUMN_ORDER = [
    'id', 'source_file', 'address_source', 'source_row_number',
    'raw_address', 'city', 'state', 'zip_code', 'latitude', 'longitude',
    'agent0_is_new_address', 'agent0_status', 'agent0_discovered_address',
    'coord_address_match_status', 'coord_address_distance_m', 'reverse_geocode_confidence_score',
    'validated_raw_address', 'validated_latitude', 'validated_longitude',
    'agent2_status', 'agent2_formatted_address', 'agent2_latitude', 'agent2_longitude',
    'agent2_location_type', 'agent2_confidence',
    'agent1_validation_status', 'agent1_confidence_score', 'agent1_chosen_provider',
    'agent1_smarty_lat', 'agent1_smarty_lon',
    'agent3_land_use', 'agent3_county_name',
    'agent4_latitude', 'agent4_longitude', 'agent4_structure_type', 'agent4_unit_count', 'agent4_confidence', 'agent4_building_provider',
    'agent50_status', 'agent50_reason', 'agent50_ocr_match_found', 'agent50_confidence',
    'agent50_ocr_engine_used', 'agent50_ollama_ocr_text', 'agent50_ollama_ocr_confidence',
    'agent50_paddle_ocr_text',
    'agent5_latitude', 'agent5_longitude', 'agent5_structure_type', 'agent5_confidence',
    'agent5_imagery_source', 'agent5_paddleocr_scan_recognized',
    'agent6_final_structure_type', 'agent6_final_confidence', 'agent6_ftth_priority',
    'agent7_new_address_count', 'agent7_new_addresses',
    'final_latitude', 'final_longitude', 'final_address', 'final_confidence', 'final_provider',
    'final_structure_type', 'final_structure_confidence', 'final_new_addresses',
    'ai_address', 'ai_latitude', 'ai_longitude', 'ai_confidence', 'ftth_priority',
  ];

  const COLUMN_GROUP_LABEL = {
    raw: 'Raw',
    canonical: 'Canonical',
    validated: 'Validated',
    agent0: 'Agent 0',
    agent1: 'Agent 2',
    agent2: 'Agent 1 (Geocoding)',
    agent3: 'Agent 3',
    agent4: 'Agent 4',
    agent5_0: 'Agent 5-0',
    agent5: 'Agent 5',
    agent6: 'Agent 6',
    agent7: 'Agent 7',
    final: 'Final',
    debug: 'Debug',
  };

  useEffect(() => {
    fetchJobFlow(jobId)
      .then(d => setFlowConfig(d.flow_config || null))
      .catch(() => setFlowConfig(null));
  }, [jobId]);

  // Debounce search - fires fresh API fetch 400ms after user stops typing
  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPage(1); }, 400);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => { loadRecords(); }, [jobId, page, search, pageSize]);

  async function loadRecords() {
    setLoading(true);
    try { setData(await fetchRecords(jobId, page, pageSize, search)); }
    catch (err) { console.error(err); }
    finally { setLoading(false); }
  }

  if (loading && !data) return <div className="text-center py-8 text-slate-500 dark:text-slate-400">Loading records...</div>;
  if (!data) return <div className="text-center py-8 text-slate-500 dark:text-slate-400">No records found</div>;

  const columns = data.columns || [];
  const hasGeo = data.records.some(r => hasValidCoord(r.latitude, r.longitude));
  const hasRecords = data.records.length > 0;

  const flowAgents = flowConfig?.agents || [];
  const flowAgentCfg = new Map(flowAgents.map(a => [a.agent_name, a]));
  const addressColumns = [
    { key: 'raw_address', label: 'Address', source: 'raw' },
    { key: 'latitude', label: 'Latitude', source: 'raw' },
    { key: 'longitude', label: 'Longitude', source: 'raw' },
  ];

  function getFlowAgentConfigBySource(source) {
    const name = FLOW_SOURCE_TO_AGENT[source];
    if (!name) return null;
    if (name === 'agent6_finalization') {
      return flowAgentCfg.get('agent6_finalization') || flowAgentCfg.get('agent6_final') || null;
    }
    return flowAgentCfg.get(name) || null;
  }

  function isAgentEnabledForSource(source) {
    if (!source || !source.startsWith('agent')) return true;
    const cfg = getFlowAgentConfigBySource(source);
    if (!cfg) return true;
    return !!cfg.enabled;
  }

  function subOptionEnabled(source, optionKey, fallback = true) {
    const cfg = getFlowAgentConfigBySource(source);
    if (!cfg) return fallback;
    const p = cfg.pipeline_options || {};
    if (source === 'agent5') {
      const mode = String(p.analysis_mode || 'hybrid').toLowerCase();
      if (mode === 'offline' && optionKey === 'azure_vision') return false;
      if (mode === 'online' && optionKey === 'azure_vision') return true;
      if (mode === 'offline' && optionKey === 'gpt_vision') return true;
      if (mode === 'online' && optionKey === 'gpt_vision') return true;
    }
    if (Object.prototype.hasOwnProperty.call(p, optionKey)) return !!p[optionKey];
    return fallback;
  }

  function isColumnEnabledBySubchecks(col) {
    const key = col.key || '';

    if (key.startsWith('agent1_smarty_')) {
      return subOptionEnabled('agent1', 'smarty', true);
    }
    if (key.startsWith('agent1_melissa_')) {
      return subOptionEnabled('agent1', 'melissa', true);
    }

    const a0ForwardKeys = new Set([
      'agent2_status', 'agent2_formatted_address', 'agent2_latitude', 'agent2_longitude',
      'agent2_location_type', 'agent2_confidence',
    ]);
    const a0ValidationKeys = new Set([
      'coord_address_match_status', 'coord_address_distance_m', 'reverse_geocode_confidence_score',
      'validated_street_line', 'validated_postcode', 'validated_city_state', 'validated_country_code',
      'validated_latitude', 'validated_longitude', 'validated_raw_address', 'ADDRESS',
    ]);
    if (key === 'reverse_geocode_confidence_score' || key.startsWith('old_') || key.startsWith('new_')) {
      return subOptionEnabled('agent2', 'reverse_geocoder', true)
        || subOptionEnabled('agent2', 'coord_validation', true);
    }
    if (key === 'coord_address_match_status' || key === 'coord_address_distance_m') {
      return subOptionEnabled('agent2', 'coord_validation', true);
    }
    if (a0ValidationKeys.has(key)) {
      return subOptionEnabled('agent2', 'reverse_geocoder', true)
        || subOptionEnabled('agent2', 'coord_validation', true);
    }
    if (a0ForwardKeys.has(key)) {
      return (
        subOptionEnabled('agent2', 'google_geocoding', true) ||
        subOptionEnabled('agent2', 'osm_geocoding', true) ||
        subOptionEnabled('agent2', 'street_interpolation', true)
      );
    }

    if (
      key === 'agent5_ocr_match_found' || key === 'agent5_vision_score' ||
      key === 'agent5_metadata_score' || key === 'agent5_agreement_score' || key === 'agent5_quality_score' ||
      key === 'agent5_confidence_breakdown' ||
      key === 'agent5_tesseract_ocr_used' || key === 'agent5_tesseract_ocr_match_found' ||
      key === 'agent5_tesseract_ocr_text'
    ) {
      return subOptionEnabled('agent5', 'azure_vision', true);
    }
    if (
      key === 'agent5_paddle_ocr_used' ||
      key === 'agent5_paddle_ocr_match_found' || key === 'agent5_paddle_ocr_text' ||
      key === 'agent5_paddleocr_scan_recognized' || key === 'agent5_paddleocr_scan_confidence' ||
      key === 'agent5_paddleocr_scan_matched'
    ) {
      return subOptionEnabled('agent5', 'street_view', true);
    }
    if (key === 'agent5_imagery_source' || key === 'agent5_image_quality' || key === 'agent5_images_fetched') {
      return subOptionEnabled('agent5', 'street_view', true) || subOptionEnabled('agent5', 'satellite_fallback', true);
    }

    return true;
  }

  const flowVisibleColumns = columns.filter((col) => {
    return isColumnEnabledBySubchecks(col);
  });

  const demoColumns = DEMO_COLUMN_ORDER
    .map((key) => flowVisibleColumns.find((c) => c.key === key))
    .filter(Boolean);

  const finalColumns = demoColumns.length ? demoColumns : addressColumns;

  const addressCountLabel = `${data.total} ${data.total === 1 ? 'address' : 'addresses'}`;

  function getCellValue(record, col) {
    const rawCoreKeys = new Set([
      'source_row_number', 'address_source', 'raw_address', 'city', 'state', 'zip_code', 'latitude', 'longitude',
    ]);
    if (col.source === 'raw' || rawCoreKeys.has(col.key)) {
      return record[col.key] ?? record.raw_data?.[col.key] ?? '';
    }
    const agentSources = new Set(['raw','agent0','agent1','agent2','agent3','agent4','agent5_0','agent5','agent6','agent7','final','debug']);
    if (agentSources.has(col.source) && record.raw_data)
      return record.raw_data[col.key] ?? record[col.key] ?? '';
    if (col.key === 'ADDRESS' || col.key.startsWith('validated_') || col.key.startsWith('coord_address_')
        || col.key.startsWith('old_') || col.key.startsWith('new_')
        || col.key.startsWith('agent2_') || col.key === 'reverse_geocode_confidence_score')
      return record.raw_data?.[col.key] ?? record[col.key] ?? '';
    return record[col.key] ?? '';
  }

  async function handleDeleteRecord(record) {
    const label = record.raw_address || record.validated_street_line || `ID ${record.id}`;
    if (!window.confirm(`Delete record #${record.id}-\n${label}`)) return;
    try {
      await deleteRecord(record.id);
      await loadRecords();
    } catch (err) {
      alert(err.message || 'Delete failed');
    }
  }

  function isMissing(v) { return v === '' || v === null || v === undefined; }

  const COORD_COLUMN_PAIRS = {
    latitude: 'longitude',
    validated_latitude: 'validated_longitude',
    old_latitude: 'old_longitude',
    new_latitude: 'new_longitude',
    original_latitude: 'original_longitude',
    final_latitude: 'final_longitude',
    ai_latitude: 'ai_longitude',
    agent1_smarty_lat: 'agent1_smarty_lon',
    agent2_latitude: 'agent2_longitude',
    agent4_latitude: 'agent4_longitude',
    agent5_latitude: 'agent5_longitude',
  };

  function formatCoordValue(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return String(value);
    return num.toFixed(6);
  }

  function getCoordPair(record, colKey) {
    const lonKey = COORD_COLUMN_PAIRS[colKey];
    if (!lonKey) return null;
    const lat = record.raw_data?.[colKey] ?? record[colKey];
    const lon = record.raw_data?.[lonKey] ?? record[lonKey];
    if (isMissing(lat) || isMissing(lon)) return null;
    const latNum = Number(lat);
    const lonNum = Number(lon);
    if (!Number.isFinite(latNum) || !Number.isFinite(lonNum) || (latNum === 0 && lonNum === 0)) return null;
    return { lat: latNum, lon: lonNum };
  }

  // Validation status / priority to colour
  function vsStyle(val) {
    const dark = {
      success: { background:'#14532d', color:'#86efac', fontWeight:700 },
      danger: { background:'#450a0a', color:'#fca5a5', fontWeight:700 },
      warning: { background:'#451a03', color:'#fde68a', fontWeight:700 },
      violet: { background:'#3b0764', color:'#e9d5ff', fontWeight:700 },
      info: { background:'#1e3a5f', color:'#93c5fd', fontWeight:700 },
      muted: { background:'#374151', color:'#9ca3af', fontWeight:700 },
      indigo: { background:'#1e2757', color:'#a78bfa', fontWeight:700 },
      sfh: { background:'#052e16', color:'#4ade80', fontWeight:700 },
    };
    const light = {
      success: { background:'#dcfce7', color:'#166534', fontWeight:700 },
      danger: { background:'#fee2e2', color:'#991b1b', fontWeight:700 },
      warning: { background:'#fef3c7', color:'#92400e', fontWeight:700 },
      violet: { background:'#f3e8ff', color:'#7e22ce', fontWeight:700 },
      info: { background:'#dbeafe', color:'#1d4ed8', fontWeight:700 },
      muted: { background:'#f1f5f9', color:'#64748b', fontWeight:700 },
      indigo: { background:'#e0e7ff', color:'#4338ca', fontWeight:700 },
      sfh: { background:'#dcfce7', color:'#166534', fontWeight:700 },
    };
    const t = isDark ? dark : light;
    if (val === 'AUTO_ACCEPT') return t.success;
    if (val === 'REJECT') return t.danger;
    if (val === 'MANUAL_REVIEW') return t.warning;
    if (val === 'REVERSE_GEOCODED') return t.violet;
    if (val === 'MATCH') return t.success;
    if (val === 'MISMATCH_WARN') return t.warning;
    if (val === 'ADDRESS_MISMATCH') return t.danger;
    if (val === 'MISMATCH') return t.danger;
    if (val === 'COORDS_VALIDATED') return t.info;
    if (val === 'reverse_geocoder') return t.info;
    if (val === 'address_validator') return t.success;
    if (val === 'skipped') return t.muted;
    if (val === 'HIGH') return t.success;
    if (val === 'MEDIUM') return t.info;
    if (val === 'LOW') return t.warning;
    if (val === 'SKIP') return t.muted;
    if (val === 'SFH') return t.sfh;
    if (val === 'MDU') return t.info;
    if (val === 'MDU_SMALL') return t.info;
    if (val === 'MDU_LARGE') return t.indigo;
    if (val === 'Commercial') return t.warning;
    return {};
  }

  return (
    <div className="records-table-root">
      <div className="records-table-toolbar">
      <div className="flex items-center justify-between mb-1.5 gap-2 flex-wrap">
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          <span className="text-xs text-slate-600 dark:text-gray-400 font-mono bg-slate-100 dark:bg-dark-700 px-2 py-1 rounded">
            {addressCountLabel}
          </span>
          <button onClick={loadRecords} disabled={loading}
            className="flex items-center gap-1 text-xs bg-slate-100 dark:bg-dark-700 hover:bg-slate-200 dark:hover:bg-dark-600 border border-slate-300 dark:border-dark-600 px-3 py-1.5 rounded font-medium text-slate-700 dark:text-gray-300"
            title="Reload records">
            <Icon name="refresh" size={13} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
          {hasGeo && navigate && (
            <button onClick={() => navigate(`/projects/${jobId}/map`)}
              className="flex items-center gap-1 text-xs bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded font-medium">
              <Icon name="map" size={13} /> View on Map
            </button>
          )}
        </div>
        <input type="text" placeholder="Search all records..." value={searchInput}
          onChange={e => setSearchInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.currentTarget.value.trim()) {
              setSearchInput('');
              setSearch('');
              setPage(1);
            }
          }}
          className="px-3 py-1.5 bg-white dark:bg-dark-700 border border-slate-300 dark:border-dark-600 rounded text-xs text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-gray-500 focus:outline-none focus:border-blue-500 w-full sm:w-60" />
      </div>

      </div>

      {hasRecords ? (
      <div className="records-table-scroll overflow-x-auto overflow-y-auto rounded-lg border border-slate-300 dark:border-dark-600 bg-white dark:bg-dark-800">
        <table className="records-table text-xs border-collapse" style={{minWidth:'max-content'}}>
          <thead>
            <tr className="bg-slate-100 dark:bg-dark-700">
              {finalColumns.map(col => (
                <th key={col.key}
                  className="text-left px-2.5 py-2 font-semibold whitespace-nowrap border-b border-slate-200 dark:border-dark-600 border-r border-slate-200 dark:border-dark-700 last:border-r-0"
                  style={{
                    position:'sticky', top:0, zIndex:10,
                    background: col.source === 'agent0' || col.source === 'agent2' ? '#0f3a46' :
                                col.source === 'agent1' ? '#14532d' :
                                col.source === 'agent3' ? '#2d1e40' :
                                col.source === 'agent4' ? '#401e2d' :
                                col.source === 'agent5_0' ? '#3b2f0b' :
                                col.source === 'agent5' ? '#1e402d' :
                                col.source === 'agent6' ? '#40301e' :
                                col.source === 'agent7' ? '#083344' :
                                col.source === 'final'  ? '#12333a' :
                                col.source === 'debug'  ? '#1c2533' : '#252832',
                    color: col.source === 'agent0' || col.source === 'agent2' ? '#67e8f9' :
                           col.source === 'agent1' ? '#86efac' :
                           col.source === 'agent3' ? '#c4b5fd' :
                           col.source === 'agent4' ? '#fca5a5' :
                           col.source === 'agent5_0' ? '#fde047' :
                           col.source === 'agent5' ? '#6ee7b7' :
                           col.source === 'agent6' ? '#fde68a' :
                           col.source === 'agent7' ? '#67e8f9' :
                           col.source === 'final'  ? '#67e8f9' :
                           col.source === 'debug'  ? '#60a5fa' : '#d1d5db',
                  }}>
                  {col.source && col.source !== 'raw' && col.source !== 'canonical' &&
                    <span style={{marginRight:'4px',fontSize:'9px',opacity:0.8,textTransform:'uppercase'}}>{col.source.replace('agent','A')}</span>}
                  {col.label}
                </th>
              ))}
              {hasGeo && (
                <th className="text-center px-2.5 py-2 text-slate-700 dark:text-gray-300 font-semibold whitespace-nowrap border-b border-slate-200 dark:border-dark-600"
                  style={{position:'sticky', top:0, zIndex:10, background: isDark ? '#252832' : '#e2e8f0'}}>Map</th>
              )}
              <th className="text-center px-2.5 py-2 text-slate-700 dark:text-gray-300 font-semibold whitespace-nowrap border-b border-slate-200 dark:border-dark-600"
                style={{position:'sticky', top:0, zIndex:10, background: isDark ? '#252832' : '#e2e8f0'}}>Delete</th>
            </tr>
          </thead>
          <tbody>
            {data.records.map(record => (
              <tr key={record.id} className="border-b border-slate-200 dark:border-dark-700 hover:bg-slate-100 dark:hover:bg-dark-700/60 transition-colors">
                {finalColumns.map(col => {
                  const value = getCellValue(record, col);
                  const missing = isMissing(value);
                  const coordPair = COORD_COLUMN_PAIRS[col.key] ? getCoordPair(record, col.key) : null;
                  const str = missing ? '' : (coordPair ? formatCoordValue(value) : String(value));
                  const isDebug = col.source === 'debug';
                  const _BADGE_KEYS = new Set([
                    'agent1_validation_status','debug_routed_to',
                    'agent4_structure_type','agent5_structure_type',
                    'agent6_final_structure_type','agent6_ftth_priority',
                    'coord_address_match_status', 'ADDRESS',
                  ]);
                  const isStatus = _BADGE_KEYS.has(col.key);
                  const agentBg = {
                    agent0:'#062c35', agent1:'#052e16', agent2:'#0c1a2e', agent3:'#180c2e',
                    agent4:'#2e0c1a', agent5_0:'#2f2608', agent5:'#0c2e1a', agent6:'#2e1e0c', agent7:'#083344', final:'#08333a',
                  };
                  const agentFg = {
                    agent0:'#22d3ee', agent1:'#4ade80', agent2:'#60a5fa', agent3:'#a78bfa',
                    agent4:'#f87171', agent5_0:'#fde047', agent5:'#34d399', agent6:'#fbbf24', agent7:'#67e8f9', final:'#67e8f9',
                  };
                  const validatedBg = {validated:'#0c2e3e'};
                  const validatedFg = {validated:'#67e8f9'};
                  const agentCellStyle = ((col.source === 'validated' || col.source === 'agent0' || col.source === 'agent2') && !missing)
                    ? {background: agentBg.agent0 || validatedBg.validated, color: agentFg.agent0 || validatedFg.validated}
                    : (agentBg[col.source] && !missing)
                    ? {background: agentBg[col.source], color: agentFg[col.source]}
                    : {};
                  return (
                    <td key={col.key}
                      className="px-3 py-1.5 border-r border-slate-200 dark:border-dark-700/50 last:border-r-0"
                      style={{
                        maxWidth:'220px', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
                        ...(isStatus && !missing ? vsStyle(str)
                              : isDebug && !missing ? {background: isDark ? '#1c2533' : '#e0f2fe', color: isDark ? '#60a5fa' : '#075985'}
                            : Object.keys(agentCellStyle).length ? agentCellStyle
                            : missing ? {color:'rgba(248,113,113,0.4)', fontStyle:'italic'}
                              : {color: isDark ? '#d1d5db' : '#334155'})
                      }}
                      title={coordPair ? `${coordPair.lat.toFixed(6)}, ${coordPair.lon.toFixed(6)}` : (str || undefined)}>
                      {missing ? '-' : (
                        coordPair ? (
                          <a href={googleMapsCoordUrl(coordPair.lat, coordPair.lon)}
                            target="_blank" rel="noopener noreferrer"
                            className="text-blue-400 hover:text-blue-300 hover:underline"
                            onClick={(e) => e.stopPropagation()}>
                            {str}
                          </a>
                        ) : str
                      )}
                    </td>
                  );
                })}
                {hasGeo && (
                  <td className="px-3 py-1.5 text-center border-l border-slate-200 dark:border-dark-700">
                    {hasValidCoord(record.latitude, record.longitude) ? (
                      <a href={googleMapsCoordUrl(record.latitude, record.longitude)}
                        target="_blank" rel="noopener noreferrer"
                        title={`Open ${Number(record.latitude).toFixed(5)}, ${Number(record.longitude).toFixed(5)} in Google Maps`}
                        className="inline-flex text-blue-400 hover:text-blue-300 text-sm"><Icon name="pin" size={14} /></a>
                    ) : <span className="text-slate-400 dark:text-gray-700 text-xs">-</span>}
                  </td>
                )}
                <td className="px-3 py-1.5 text-center border-l border-slate-200 dark:border-dark-700">
                  <button type="button" onClick={() => handleDeleteRecord(record)}
                    className="text-red-500 dark:text-red-400 hover:text-red-600 dark:hover:text-red-300 text-xs font-medium px-2 py-1 rounded hover:bg-red-50 dark:hover:bg-red-950/40"
                    title="Delete this record"><Icon name="trash" size={14} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      ) : (
        <div className="rounded-lg border border-slate-300 dark:border-dark-600 bg-white dark:bg-dark-800 px-4 py-10 text-center">
          <div className="text-sm font-semibold text-slate-700 dark:text-slate-200">No records found</div>
          <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {search ? 'Clear the search box and press Enter to show all records again.' : 'There are no records to display.'}
          </div>
        </div>
      )}

      {hasRecords && (
      <div className="records-table-footer flex items-center justify-between mt-1.5 pt-1.5 border-t border-slate-200 dark:border-dark-600 gap-2 flex-wrap">
        <span className="text-xs text-slate-500 dark:text-gray-500">
          Showing {data.records.length ? ((data.page - 1) * pageSize + 1) : 0}-{(data.page - 1) * pageSize + data.records.length} of {data.total} addresses
          {search ? ` matching "${search}"` : ''}
        </span>
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-gray-400">
            Addresses
            <select
              value={pageSize}
              onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
              className="px-2 py-1 rounded border border-slate-300 dark:border-dark-600 bg-white dark:bg-dark-700 text-slate-800 dark:text-gray-200 text-xs">
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          <div className="flex items-center gap-2">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}
            className="px-2 py-1 text-xs hover:bg-slate-100 dark:hover:bg-dark-600 rounded disabled:opacity-30">Prev</button>
          <span className="text-xs text-slate-500 dark:text-gray-500 font-mono px-1">{page} / {data.total_pages}</span>
          <button onClick={() => setPage(p => Math.min(data.total_pages, p + 1))} disabled={page >= data.total_pages}
            className="px-2 py-1 text-xs hover:bg-slate-100 dark:hover:bg-dark-600 rounded disabled:opacity-30">Next</button>
          </div>
        </div>
      </div>
      )}
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.RecordsTable = RecordsTable;
