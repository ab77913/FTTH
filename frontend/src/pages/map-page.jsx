/** @jsx React.createElement */

const { useState, useEffect, useRef, useCallback, useMemo } = React;

const { Icon, useTheme, API, getToken, fetchMapsKey, fetchJob, startProcessing, fetchAgentStatus, fetchGeoRecords, fetchMapOverlay, fetchStreetViewMeta, subscribeProgress, mergeAgentStatus, PageLoader, EmptyState, Breadcrumbs, StatusBadge, Spinner, useToast, StreetViewPanel, MapLayersPanel, MapSearchPanel, MapSelectedPanel } = window.FTTH_APP;
const attachMapTools = window.FTTH_MAP_TOOLS && window.FTTH_MAP_TOOLS.attachMapTools;
const createBasemaps = window.FTTH_MAP_TOOLS && window.FTTH_MAP_TOOLS.createBasemaps;

function MapPage({ jobId, navigate }) {
  const { theme, toggleTheme } = useTheme();
  const { toast } = useToast();
  const [job, setJob] = useState(null);
  const [features, setFeatures] = useState([]);
  const [agentStatus, setAgentStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [toolbarExpanded, setToolbarExpanded] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [selectedPoint, setSelectedPoint] = useState(null);
  const [colorMode, setColorMode] = useState('rule'); // 'rule' | 'quality' | 'kmz' | 'agent'
  const [coordinateMode, setCoordinateMode] = useState('updated'); // 'updated' | 'raw'
  const [overlay, setOverlay] = useState({ overlay: {}, legend: [] });
  const [activeFilter, setActiveFilter] = useState(null); // legend label | null = show all
  const [activeCategory, setActiveCategory] = useState(null); // mode-aware category/filter selection
  const [addressSearch, setAddressSearch] = useState('');
  const [showAddressMatches, setShowAddressMatches] = useState(false);
  const [selectedFeatureId, setSelectedFeatureId] = useState(null);
  const [legendMinimized, setLegendMinimized] = useState(false);
  const [svFullscreen, setSvFullscreen] = useState(false);
  const [gmapsKey, setGmapsKey] = useState(() => localStorage.getItem('ftth_gmaps_key') || '');
  const [showKeyInput, setShowKeyInput] = useState(false);
  const [keyDraft, setKeyDraft] = useState('');
  const [layersOpen, setLayersOpen] = useState(false);
  const [activeBasemap, setActiveBasemap] = useState('Google Streets');
  const [overlayVisibility, setOverlayVisibility] = useState({ 'Data Points': true, 'Boundary Lines': true });
  const [measureResult, setMeasureResult] = useState(null);
  const [baseMapCatalog, setBaseMapCatalog] = useState({});
  const searchInputRef = useRef(null);
  const activeFilterRef = useRef(null); // always holds latest filter; avoids stale closures
  const mapRef = useRef(null);
  const mapInstanceRef = useRef(null);
  const markersLayerRef = useRef(null);
  const boundaryLayerRef = useRef(null);
  const markerByIdRef = useRef({});
  const initialFitDoneRef = useRef(false);
  const suppressMapMoveRef = useRef(false);
  const userChangedMapRef = useRef(false);
  const baseLayersRef = useRef({});
  const activeBaseLayerRef = useRef(null);
  const mapToolsRef = useRef(null);

  useEffect(() => {
    initialFitDoneRef.current = false;
    userChangedMapRef.current = false;
    suppressMapMoveRef.current = false;
    setSelectedFeatureId(null);
    setSelectedPoint(null);
    loadData();
  }, [jobId]);

  useEffect(() => {
    let active = true;
    async function loadMapsKey() {
      try {
        const data = await fetchMapsKey();
        const serverKey = String(data?.key || '').trim();
        if (serverKey) {
          localStorage.setItem('ftth_gmaps_key', serverKey);
          localStorage.removeItem('ftth_gmaps_key_custom');
          if (active) setGmapsKey(serverKey);
          return;
        }
      } catch (_) {}

      const saved = (localStorage.getItem('ftth_gmaps_key') || '').trim();
      if (active) setGmapsKey(saved);
    }
    loadMapsKey();
    const onMapsKeyUpdated = () => { loadMapsKey(); };
    window.addEventListener('ftth:maps-key-updated', onMapsKeyUpdated);
    return () => {
      active = false;
      window.removeEventListener('ftth:maps-key-updated', onMapsKeyUpdated);
    };
  }, []);

  useEffect(() => {
    if (agentStatus?.status === 'processing') {
      const interval = setInterval(() => {
        loadGeoData();
        fetchAgentStatus(jobId)
          .then(status => setAgentStatus(prev => mergeAgentStatus(prev, status)))
          .catch(() => {});
      }, 1000);
      return () => clearInterval(interval);
    }
    // When processing just completed, do one final geo reload to pick up all validated points
    if (agentStatus?.status === 'completed') {
      loadGeoData();
    }
  }, [agentStatus?.status]);

  useEffect(() => {
    if (agentStatus?.status === 'processing') {
      const evtSource = subscribeProgress(jobId, (data) => {
        if (data.error) {
          setAgentStatus(prev => ({ ...(prev || {}), status: 'failed', error: data.error }));
          return;
        }

        // Keep live updates flowing.
        setAgentStatus(prev => mergeAgentStatus(prev, data));

        // On terminal events, force-refresh final state + data so UI never appears stuck.
        if (data.done || data.status === 'completed' || data.status === 'failed') {
          fetchAgentStatus(jobId)
            .then(finalStatus => setAgentStatus(prev => mergeAgentStatus(prev, finalStatus)))
            .catch(() => {})
            .finally(() => {
              loadGeoData().catch(() => {});
            });
        }
      }, () => {
        fetchAgentStatus(jobId)
          .then(status => setAgentStatus(prev => mergeAgentStatus(prev, status)))
          .catch(() => {});
      });
      return () => evtSource.close();
    }
  }, [agentStatus?.status, jobId]);

  async function loadData() {
    setLoadError(null);
    try {
      const [jobData, status, ov] = await Promise.all([
        fetchJob(jobId), fetchAgentStatus(jobId), fetchMapOverlay(jobId)
      ]);
      setJob(jobData); setAgentStatus(status); setOverlay(ov);
      await loadGeoData();
    } catch (err) {
      setLoadError(err.message || 'Failed to load map data');
      toast('Could not load map data', 'error');
    } finally { setLoading(false); }
  }

  async function loadGeoData() {
    try {
      const [data, ov] = await Promise.all([fetchGeoRecords(jobId), fetchMapOverlay(jobId)]);
      setFeatures(data.features || []);
      setOverlay(ov);
      updateMap(data.features || [], ov);
    } catch (err) { console.error(err); }
  }

  async function handleMapProcess() {
    setAgentStatus(prev => ({
      ...(prev || {}),
      status: 'processing',
      overall_progress: prev?.overall_progress ?? 0,
    }));
    try {
      await startProcessing(jobId);
      fetchAgentStatus(jobId)
        .then(status => setAgentStatus(prev => mergeAgentStatus(prev, status)))
        .catch(() => {});
    } catch (err) {
      toast(err.message || 'Failed to start processing', 'error');
      fetchAgentStatus(jobId)
        .then(status => setAgentStatus(prev => mergeAgentStatus(prev, status)))
        .catch(() => {});
    }
  }

  useEffect(() => {
    if (!mapRef.current || mapInstanceRef.current) return;
    const map = L.map(mapRef.current).setView([39.8283, -98.5795], 4);
    map.on('zoomstart movestart', () => {
      if (!suppressMapMoveRef.current) userChangedMapRef.current = true;
    });

    const basemapPack = createBasemaps ? createBasemaps() : { baseMaps: {}, defaultKey: 'Google Streets' };
    baseLayersRef.current = basemapPack.baseMaps;
    setBaseMapCatalog(basemapPack.baseMaps);
    const defaultBasemap = basemapPack.defaultKey || 'Google Streets';
    setActiveBasemap(defaultBasemap);

    if (attachMapTools) {
      mapToolsRef.current = attachMapTools(map, {
        defaultBasemap: defaultBasemap,
        onMeasureChange: setMeasureResult,
      });
      activeBaseLayerRef.current = basemapPack.baseMaps[defaultBasemap] || null;
    } else {
      const googleStreets = L.tileLayer('https://{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', {
        maxZoom: 20, subdomains: ['mt0','mt1','mt2','mt3'], attribution: '&copy; Google',
      });
      googleStreets.addTo(map);
      activeBaseLayerRef.current = googleStreets;
      baseLayersRef.current = { 'Google Streets': googleStreets };
    }

    markersLayerRef.current = L.layerGroup().addTo(map);
    boundaryLayerRef.current = L.layerGroup().addTo(map);

    L.control.scale({ imperial: true, metric: true }).addTo(map);

    const StreetViewControl = L.Control.extend({
      options: { position: 'topleft' },
      onAdd: function(m) {
        const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control map-pegman-control');
        const btn = L.DomUtil.create('a', 'map-pegman-btn', container);
        btn.href = '#';
        btn.title = 'Street View — click map to explore';
        btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#111827" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="2"/><path d="M9 20v-8h6v8"/><path d="M12 7v3"/><path d="m8 12 4-2 4 2"/></svg>';
        btn.id = 'streetview-btn';
        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.on(btn, 'click', function(e) {
          L.DomEvent.preventDefault(e);
          const isActive = container.classList.toggle('sv-active');
          btn.classList.toggle('map-pegman-btn--active', isActive);
          m.getContainer().style.cursor = isActive ? 'crosshair' : '';
          if (isActive) {
            m._svClickHandler = function(ev) {
              setSelectedPoint({ lat: ev.latlng.lat, lon: ev.latlng.lng, name: 'Street View (' + ev.latlng.lat.toFixed(5) + ', ' + ev.latlng.lng.toFixed(5) + ')' });
              container.classList.remove('sv-active');
              btn.classList.remove('map-pegman-btn--active');
              m.getContainer().style.cursor = '';
              m.off('click', m._svClickHandler);
              m._svClickHandler = null;
            };
            m.on('click', m._svClickHandler);
          } else if (m._svClickHandler) {
            m.off('click', m._svClickHandler);
            m._svClickHandler = null;
          }
        });
        return container;
      }
    });
    new StreetViewControl().addTo(map);

    mapInstanceRef.current = map;
    if (features.length > 0) updateMap(features, overlay);
    return () => {
      if (mapToolsRef.current && mapToolsRef.current.measure && mapToolsRef.current.measure.destroy) {
        mapToolsRef.current.measure.destroy();
      }
      mapToolsRef.current = null;
      if (mapInstanceRef.current) { mapInstanceRef.current.remove(); mapInstanceRef.current = null; }
    };
  }, [loading]);

  // Keep ref in sync so updateMap always reads latest filter even from old closures
  useEffect(() => { activeFilterRef.current = activeFilter; }, [activeFilter]);

  // Reset legend filter when color mode changes (labels differ per mode).
  // activeCategory is mode-independent (folder/category name) so it persists.
  useEffect(() => {
    setActiveFilter(null);
    activeFilterRef.current = null;
  }, [colorMode]);

  // Re-render map whenever data, color mode, overlay, filter, or KMZ category changes.
  // Including `features` ensures boundaries are drawn as soon as data loads,
  // even if the map was already initialised before the first data fetch returned.
  useEffect(() => {
    if (features.length > 0 && mapInstanceRef.current) updateMap(features, overlay);
  }, [features, colorMode, coordinateMode, overlay, activeFilter, activeCategory, selectedFeatureId]);

  // Auto-load Street View with the first available Point when data arrives
  useEffect(() => {
    if (features.length > 0 && !selectedPoint) {
      const fp = features.find(f => f.geometry_type === 'Point' && featureCoord(f)) || features.find(f => featureCoord(f));
      if (fp) {
        const coord = featureCoord(fp);
        setSelectedFeatureId(fp.id);
        setSelectedPoint({ lat: coord.lat, lon: coord.lon, name: `${fp.display_name || fp.address || `${coord.lat.toFixed(5)}, ${coord.lon.toFixed(5)}`} (${coordLabel()})` });
      }
    }
  }, [features, coordinateMode]);

  useEffect(() => {
    if (!selectedFeatureId) return;
    const feat = features.find(f => f.id === selectedFeatureId);
    const coord = featureCoord(feat);
    if (!feat || !coord) return;
    setSelectedPoint({ lat: coord.lat, lon: coord.lon, name: `${featureLabel(feat)} (${coordLabel()})` });
  }, [coordinateMode, selectedFeatureId, features]);

  useEffect(() => {
    setAddressSearch('');
    setShowAddressMatches(false);
  }, [coordinateMode]);

  useEffect(() => {
    setLegendMinimized(window.innerWidth <= 768);
    setToolbarExpanded(false);
  }, [jobId]);

  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth > 768) setToolbarExpanded(false);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Handle fullscreen resize
  useEffect(() => {
    if (mapInstanceRef.current) {
      setTimeout(() => mapInstanceRef.current.invalidateSize(), 100);
    }
  }, [isFullscreen, svFullscreen]);

  // Refit map when split-screen opens/closes (selectedPoint changes)
  useEffect(() => {
    if (mapInstanceRef.current) {
      setTimeout(() => mapInstanceRef.current.invalidateSize(), 350);
    }
  }, [selectedPoint]);
// Convex hull
  // Returns the minimal convex polygon enclosing all input [lat,lon] points.
  function convexHull(points) {
    if (points.length < 2) return points;
    const pts = [...points].sort((a, b) => a[0] !== b[0] ? a[0] - b[0] : a[1] - b[1]);
    const cross = (O, A, B) => (A[0]-O[0])*(B[1]-O[1]) - (A[1]-O[1])*(B[0]-O[0]);
    const lower = [];
    for (const p of pts) {
      while (lower.length >= 2 && cross(lower[lower.length-2], lower[lower.length-1], p) <= 0) lower.pop();
      lower.push(p);
    }
    const upper = [];
    for (let i = pts.length-1; i >= 0; i--) {
      const p = pts[i];
      while (upper.length >= 2 && cross(upper[upper.length-2], upper[upper.length-1], p) <= 0) upper.pop();
      upper.push(p);
    }
    lower.pop(); upper.pop();
    return lower.concat(upper);
  }

  function updatedAddressLabel(f) {
    return f?.final_address || f?.address || f?.canonical_address || f?.raw_address || f?.placemark_name || f?.category || `Record ${f?.id}`;
  }

  function storedAddressLabel(f) {
    return f?.raw_address || f?.placemark_name || f?.address || f?.final_address || f?.category || `Record ${f?.id}`;
  }

  function featureLabel(f, mode = coordinateMode) {
    return mode === 'raw' ? storedAddressLabel(f) : updatedAddressLabel(f);
  }

  function addressSearchHaystack(f, mode = coordinateMode) {
    const fields = mode === 'raw'
      ? [f.raw_address, f.placemark_name, f.city, f.state, f.network_node, f.terminal_id]
      : [f.final_address, f.address, f.canonical_address, f.city, f.state, f.final_source_agent, String(f.final_confidence ?? '')];
    return fields.filter(Boolean).join(' ');
  }

  function normalizeAddressSearchKey(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/\bdrive\b/g, 'dr')
      .replace(/\bavenue\b/g, 'ave')
      .replace(/\bstreet\b/g, 'st')
      .replace(/\broad\b/g, 'rd')
      .replace(/\blane\b/g, 'ln')
      .replace(/\bcourt\b/g, 'ct')
      .replace(/\bterrace\b/g, 'ter')
      .replace(/\bgeorgia\b/g, 'ga')
      .replace(/\bflorida\b/g, 'fl')
      .replace(/\busa\b/g, '')
      .replace(/\bunited states\b/g, '')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  function isAddressSearchFeature(f) {
    if (!f || f.map_layer_only) return false;
    if (f.geometry_type === 'Polygon' || f.geometry_type === 'LineString') return false;
    if (featureCoord(f)) return true;
    const cat = String(f.category || f.folder_path || '').toLowerCase();
    if (!cat) return true;
    return /\b(household|households|address|addresses|premise|premises)\b/.test(cat);
  }

  function dedupeAddressMatches(items, mode = coordinateMode) {
    const byAddress = new Map();
    items.forEach((feat) => {
      const key = normalizeAddressSearchKey(featureLabel(feat, mode));
      if (!key) return;
      const current = byAddress.get(key);
      if (!current) {
        byAddress.set(key, feat);
        return;
      }
      const currentRank = current.merge_status === 'verified' ? 3 : current.merge_status === 'new' ? 2 : 1;
      const nextRank = feat.merge_status === 'verified' ? 3 : feat.merge_status === 'new' ? 2 : 1;
      if (nextRank > currentRank) byAddress.set(key, feat);
    });
    return Array.from(byAddress.values());
  }

  function featureCoord(f, mode = coordinateMode) {
    const lat = mode === 'raw' ? Number(f?.raw_latitude) : Number(f?.lat);
    const lon = mode === 'raw' ? Number(f?.raw_longitude) : Number(f?.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat === 0 || lon === 0) return null;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    return { lat, lon };
  }

  function coordLabel() {
    return coordinateMode === 'raw' ? 'Stored Coordinates' : 'Final Updated';
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function hasCoordChange(f) {
    const rawLat = Number(f?.raw_latitude), rawLon = Number(f?.raw_longitude);
    const updLat = Number(f?.lat), updLon = Number(f?.lon);
    if (!Number.isFinite(rawLat) || !Number.isFinite(rawLon) || rawLat === 0 || rawLon === 0) return false;
    if (!Number.isFinite(updLat) || !Number.isFinite(updLon) || updLat === 0 || updLon === 0) return false;
    return Math.abs(updLat - rawLat) > 0.0001 || Math.abs(updLon - rawLon) > 0.0001;
  }

  function coordDeltaMeters(f) {
    const dlat = (Number(f.lat) - Number(f.raw_latitude)) * 111000;
    const dlon = (Number(f.lon) - Number(f.raw_longitude)) * 92000;
    return Math.round(Math.sqrt(dlat * dlat + dlon * dlon));
  }

  function confidenceValue(f) {
    const value = Number(f?.final_confidence ?? f?.confidence_score ?? f?.house_number_conf ?? 0);
    return Number.isFinite(value) ? value : 0;
  }

  function agentOverlayFor(f) {
    return f?.id != null ? ((overlay.overlay || {})[f.id] || null) : null;
  }

  function featureHasProcessedAgentOutput(f, ovMap = overlay.overlay || {}) {
    if (!f) return false;
    if (f.id != null && ovMap && ovMap[f.id]) return true;
    return Boolean(
      f.validation_status ||
      f.confidence_score != null ||
      f.agent2_status ||
      f.agent2_confidence != null ||
      f.agent3_status ||
      f.agent3_confidence != null ||
      f.agent4_status ||
      f.agent4_confidence != null ||
      f.agent5_status ||
      f.agent5_confidence != null ||
      f.agent6_status ||
      f.agent6_final_confidence != null ||
      f.rule_status ||
      f.final_source_agent ||
      f.final_status ||
      f.final_confidence != null
    );
  }

  function hasProcessedAgentOutput(feats = features, ovData = overlay) {
    const ovMap = (ovData && ovData.overlay) ? ovData.overlay : {};
    return (feats || []).some(f => featureHasProcessedAgentOutput(f, ovMap));
  }

  function isInvalidFeature(f) {
    const coord = featureCoord(f);
    return f?.validation_status === 'REJECT' || !coord;
  }

  function confidenceBucket(f) {
    if (isInvalidFeature(f)) {
      return { key: 'invalid', label: 'Invalid / Rejected', color: '#ef4444', desc: 'Rejected or invalid coordinate' };
    }
    const confidence = confidenceValue(f);
    if (confidence >= 90) {
      return { key: 'high', label: 'Confidence >90%', color: '#16a34a', desc: 'Highest confidence final coordinate' };
    }
    if (confidence >= 70) {
      return { key: 'medium', label: 'Confidence 70%-90%', color: '#f59e0b', desc: 'Medium confidence final coordinate' };
    }
    return { key: 'other', label: 'Other <70%', color: '#6b7280', desc: 'Needs review or low confidence' };
  }

  function mergeRuleBucket(f) {
    const status = String(f?.rule_status || '').trim().toLowerCase().replace(/[-_]+/g, ' ');
    if (status === 'duplicate') {
      return { key: 'duplicate', label: 'Duplicate Address', color: '#ffffff', desc: 'Duplicate address' };
    }
    if (status === 'new') {
      return { key: 'new', label: 'New Address', color: '#9C6500', desc: 'New address identified but not present in original CSV/Excel' };
    }
    if (status === 'valid') {
      return { key: 'verified', label: 'Address Found', color: '#006100', desc: 'Rule engine output returned valid' };
    }
    if (status === 'invalid') {
      return { key: 'invalid', label: 'Address Not Found', color: '#C00000', desc: 'Rule engine output returned invalid' };
    }
    return { key: 'unclassified', label: 'Unclassified', color: '#64748b', desc: 'No explicit rule_status output yet' };
  }

  function selectFeature(feat, zoom = true) {
    const coord = featureCoord(feat);
    if (!feat || !coord) return;
    setSelectedFeatureId(feat.id);
    setSelectedPoint({ lat: coord.lat, lon: coord.lon, name: `${featureLabel(feat)} (${coordLabel()})` });
    setAddressSearch(featureLabel(feat));
    setShowAddressMatches(false);
    const map = mapInstanceRef.current;
    if (!map) return;
    if (zoom) {
      map.flyTo([coord.lat, coord.lon], 20, { animate: true, duration: 0.9 });
      setTimeout(() => {
        map.invalidateSize();
        map.setView([coord.lat, coord.lon], 20, { animate: true });
      }, 420);
    }
    setTimeout(() => {
      const marker = markerByIdRef.current[feat.id];
      if (marker) marker.openPopup();
    }, zoom ? 950 : 120);
  }

  function recenterSelectedFeature(zoom = 20) {
    const feat = features.find(f => f.id === selectedFeatureId);
    const coord = featureCoord(feat);
    const map = mapInstanceRef.current;
    if (!feat || !coord || !map) return;
    setSelectedPoint({ lat: coord.lat, lon: coord.lon, name: `${featureLabel(feat)} (${coordLabel()})` });
    setAddressSearch(featureLabel(feat));
    setShowAddressMatches(false);
    suppressMapMoveRef.current = true;
    map.flyTo([coord.lat, coord.lon], zoom, { animate: true, duration: 0.85 });
    setTimeout(() => {
      suppressMapMoveRef.current = false;
      const marker = markerByIdRef.current[feat.id];
      if (marker) marker.openPopup();
    }, 900);
  }

  function updateMap(feats, ovData) {
    if (!mapInstanceRef.current || !markersLayerRef.current) return;
    markersLayerRef.current.clearLayers();
    markerByIdRef.current = {};
    const bounds = [];

    const ovMap = (ovData && ovData.overlay) ? ovData.overlay : {};
    const processedForColors = hasProcessedAgentOutput(feats, ovData);
    const useAgentColors = processedForColors && colorMode === 'agent' && Object.keys(ovMap).length > 0;
    const useQualityColors = processedForColors && colorMode === 'quality';
    const useRuleColors = processedForColors && colorMode === 'rule';
    // When Agent mode is on but no overlay data, fall back to confidence coloring
    const agentFallback = processedForColors && colorMode === 'agent' && !useAgentColors;

    // Returns color based on the final selected address confidence.
    function qualityColor(f) {
      // Out-of-bounds or rejected maps to red always
      return confidenceBucket(f).color;
    }

    // KMZ-style color palette for categories/folders
    const categoryColors = {};
    const palette = ['#FF4136','#0074D9','#2ECC40','#FF851B','#B10DC9','#FFDC00','#01FF70','#F012BE','#7FDBFF','#85144b'];
    let colorIdx = 0;

    feats.forEach(feat => {
      const catKey = feat.category || feat.folder_path || 'default';
      if (!categoryColors[catKey]) {
        categoryColors[catKey] = feat.style_color || palette[colorIdx % palette.length];
        colorIdx++;
      }

      // KMZ layer filter - always uses folder/category name (catKey) so polygons and
      // address points alike are filtered consistently across all color modes.
      const isKmlGeometry = feat.geometry_type === 'Polygon' || feat.geometry_type === 'LineString';
      const featureProcessedForColors = featureHasProcessedAgentOutput(feat, ovMap);
      if (activeCategory && catKey !== activeCategory && !isKmlGeometry) return;

      // Apply legend filter - skip features that don't match the active filter
      // Read from ref so this works correctly even when called from a stale closure
      const currentFilter = activeFilterRef.current;
      if (currentFilter && !isKmlGeometry && (processedForColors || colorMode === 'kmz')) {
        if (!featureProcessedForColors && colorMode !== 'kmz') return;
        const agentOvForFilter = useAgentColors ? ovMap[feat.id] : null;
        const filterKey = useAgentColors
          ? (agentOvForFilter ? agentOvForFilter.label : null)
          : useRuleColors
          ? mergeRuleBucket(feat).label
          : (useQualityColors || agentFallback)
          ? confidenceBucket(feat).label
          : catKey;
        if (filterKey !== currentFilter) return;
      }

      // Choose color: quality / agent-fallback to confidence color; overlay to overlay color; else category
      const agentOv = useAgentColors ? ovMap[feat.id] : null;
      const featureColor = !featureProcessedForColors && !isKmlGeometry
        ? '#64748b'
        : (useQualityColors || agentFallback)
        ? qualityColor(feat)
        : useRuleColors
        ? mergeRuleBucket(feat).color
        : (agentOv ? agentOv.color : categoryColors[catKey]);
      const geometryColor = feat.style_color || categoryColors[catKey] || featureColor;
      const displayName = featureLabel(feat);

      if (feat.coordinates && feat.geometry_type === 'Polygon') {
        try {
          const polygon = L.polygon(
            feat.coordinates.map(ring => ring.map(c => [c[1], c[0]])),
            { color: geometryColor, weight: 4, fillColor: geometryColor, fillOpacity: 0.16, opacity: 0.95 }
          );
          markersLayerRef.current.addLayer(polygon);
          const center = polygon.getBounds().getCenter();
          bounds.push([center.lat, center.lng]);
        } catch(e) { console.warn('Polygon render error', e); }
        return;
      }

      if (feat.coordinates && feat.geometry_type === 'LineString') {
        try {
          const polyline = L.polyline(
            feat.coordinates.map(c => [c[1], c[0]]),
            { color: geometryColor, weight: 3, opacity: 0.9 }
          );
          markersLayerRef.current.addLayer(polyline);
          feat.coordinates.forEach(c => bounds.push([c[1], c[0]]));
        } catch(e) { console.warn('LineString render error', e); }
        return;
      }

      const markerCoord = featureCoord(feat);
      if (markerCoord) {
        const isWrong = (useQualityColors || agentFallback) && featureColor === '#ef4444';
        const isSelected = selectedFeatureId === feat.id;
        const rawMode = coordinateMode === 'raw';
        const coordChanged = hasCoordChange(feat);
        const dotSize = isSelected ? '22px' : (isWrong ? '13px' : (coordChanged ? '13px' : (agentOv ? '12px' : '10px')));
        const border = isSelected ? '4px solid #fef08a'
          : (coordChanged && !rawMode) ? '3px solid #f59e0b'
          : (coordChanged && rawMode) ? '2.5px solid #38bdf8'
          : (isWrong ? '2.5px solid rgba(255,255,255,0.9)' : (agentOv ? '2.5px solid #fff' : '2px solid #fff'));
        const shadow = isSelected ? '0 0 0 6px rgba(250,204,21,0.25),0 0 14px rgba(250,204,21,0.85),0 2px 5px rgba(0,0,0,0.6)'
          : (coordChanged && !rawMode) ? '0 0 10px rgba(245,158,11,0.85),0 2px 4px rgba(0,0,0,0.5)'
          : (coordChanged && rawMode) ? '0 0 8px rgba(56,189,248,0.75),0 2px 4px rgba(0,0,0,0.5)'
          : (isWrong ? '0 0 6px rgba(239,68,68,0.7),0 2px 4px rgba(0,0,0,0.5)' : '0 2px 4px rgba(0,0,0,0.5)');
        const icon = L.divIcon({
          className: 'custom-pin',
          html: '<div style="width:' + dotSize + ';height:' + dotSize + ';border-radius:50%;background:' + featureColor + ';border:' + border + ';box-shadow:' + shadow + ';display:flex;align-items:center;justify-content:center;color:#111827;font-size:10px;line-height:1;">' + (feat.geometry_type === 'Point' || !feat.geometry_type ? '&#8962;' : '') + '</div>',
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        });
        const marker = L.marker([markerCoord.lat, markerCoord.lon], { icon });
        marker.bindTooltip(escapeHtml(displayName || 'Address point'), {
          direction: 'top',
          offset: [0, -10],
          opacity: 0.95,
        });
        const locationParts = [feat.city, feat.state, feat.zip_code || feat.zip || feat.postal_code].filter(Boolean);
        const locationHtml = locationParts.length
          ? '<div class="map-marker-popup-meta">' + escapeHtml(locationParts.join(', ')) + '</div>'
          : '';
        marker.bindPopup(
          '<div class="map-marker-popup">' +
          '<div class="map-marker-popup-title">' + escapeHtml(displayName || 'Address point') + '</div>' +
          '<div class="map-marker-popup-coord">' + markerCoord.lat.toFixed(6) + ', ' + markerCoord.lon.toFixed(6) + '</div>' +
          locationHtml +
          '</div>',
          {
            className: 'map-marker-popup-shell',
            maxWidth: 260,
            minWidth: 220,
            autoPanPaddingTopLeft: [16, 72],
            autoPanPaddingBottomRight: [16, 96],
          }
        );
        marker.on('click', () => {
          selectFeature(feat, false);
        });
        markerByIdRef.current[feat.id] = marker;
        markersLayerRef.current.addLayer(marker);
        bounds.push([markerCoord.lat, markerCoord.lon]);
      }
    });
// Boundary lines around coordinate clusters
    if (boundaryLayerRef.current) {
      boundaryLayerRef.current.clearLayers();

      // Collect only plain-point features (not polygons/linestrings from KMZ)
      const pointFeats = feats.filter(f => featureCoord(f) && f.geometry_type !== 'Polygon' && f.geometry_type !== 'LineString');

      if (pointFeats.length >= 2) {
        // Group by network_node; fall back to a single 'all' group
        const groups = {};
        const groupColors = {};
        pointFeats.forEach(f => {
          const key = f.network_node && f.network_node.trim() ? f.network_node.trim() : '__all__';
          if (!groups[key]) {
            groups[key] = [];
            const catKey = f.category || f.folder_path || 'default';
            groupColors[key] = !processedForColors
              ? '#64748b'
              : (useAgentColors && ovMap[f.id])
              ? ovMap[f.id].color
              : (categoryColors[catKey] || '#3b82f6');
          }
          const c = featureCoord(f);
          groups[key].push([c.lat, c.lon]);
        });

        Object.entries(groups).forEach(([nodeKey, pts]) => {
          const col = groupColors[nodeKey] || '#3b82f6';
          const label = nodeKey === '__all__' ? 'Service Area Boundary' : `Node: ${nodeKey}`;

          if (pts.length === 2) {
            // Two points - white halo + coloured dashed line
            L.polyline(pts, { color: '#ffffff', weight: 6, opacity: 0.6 })
              .addTo(boundaryLayerRef.current);
            L.polyline(pts, { color: col, weight: 3, dashArray: '10,6', opacity: 0.95 })
              .bindTooltip(label, { sticky: true, opacity: 0.9 })
              .addTo(boundaryLayerRef.current);
            return;
          }

          // 3+ points - compute convex hull
          const hull = convexHull(pts);
          if (hull.length >= 3) {
            // Layer 1: white halo (drawn first, beneath the coloured line)
            L.polygon(hull, {
              color: '#ffffff',
              weight: 6,
              opacity: 0.55,
              fill: false,
            }).addTo(boundaryLayerRef.current);

            // Layer 2: bold coloured dashed boundary
            L.polygon(hull, {
              color: col,
              weight: 3,
              dashArray: '12,7',
              opacity: 0.95,
              fillColor: col,
              fillOpacity: 0.08,
            })
              .bindTooltip(label, { sticky: true, opacity: 0.9,
                className: 'leaflet-tooltip-boundary' })
              .addTo(boundaryLayerRef.current);
          }
        });
      }
    }

    if (bounds.length > 0 && !initialFitDoneRef.current && !userChangedMapRef.current) {
      suppressMapMoveRef.current = true;
      mapInstanceRef.current.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
      initialFitDoneRef.current = true;
      setTimeout(() => { suppressMapMoveRef.current = false; }, 500);
    }
  }

  function refitMapToPoints() {
    const map = mapInstanceRef.current;
    if (!map || typeof L === 'undefined') return;
    const bounds = [];
    features.forEach(f => {
      const coord = featureCoord(f);
      if (!coord) return;
      bounds.push([coord.lat, coord.lon]);
    });
    if (bounds.length === 0) return;
    suppressMapMoveRef.current = true;
    if (bounds.length === 1) {
      map.setView(bounds[0], 17);
    } else {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
    }
    setTimeout(() => { suppressMapMoveRef.current = false; }, 500);
  }

  function handleBasemapChange(name) {
    const map = mapInstanceRef.current;
    const next = baseLayersRef.current[name];
    if (!map || !next) return;
    if (activeBaseLayerRef.current) map.removeLayer(activeBaseLayerRef.current);
    next.addTo(map);
    activeBaseLayerRef.current = next;
    setActiveBasemap(name);
  }

  function handleOverlayToggle(key, visible) {
    const map = mapInstanceRef.current;
    const layer = key === 'Data Points' ? markersLayerRef.current : boundaryLayerRef.current;
    if (!map || !layer) return;
    if (visible) {
      if (!map.hasLayer(layer)) layer.addTo(map);
    } else {
      map.removeLayer(layer);
    }
    setOverlayVisibility((prev) => ({ ...prev, [key]: visible }));
  }

  if (loading) return <PageLoader message="Loading map and geocoded points…" />;

  if (loadError && !job) {
    return (
      <EmptyState
        icon={<Icon name="map" size={28} />}
        title="Map unavailable"
        description={loadError}
        action={(
          <button type="button" onClick={() => { setLoading(true); loadData(); }} className="app-button app-button-primary text-white px-4 py-2 rounded-lg">
            Retry
          </button>
        )}
      />
    );
  }

  const isProcessing = agentStatus?.status === 'processing';
  const addressPointFeatures = features.filter(f => featureCoord(f) && isAddressSearchFeature(f) && normalizeAddressSearchKey(featureLabel(f)));
  const totalWithCoords = addressPointFeatures.length;
  const changedCoordsCount = features.filter(f => f.geometry_type !== 'Polygon' && f.geometry_type !== 'LineString' && hasCoordChange(f)).length;
  const totalMissingData = features.filter(f => !f.address || !f.city || !f.state).length;
  const colorCodingEnabled = hasProcessedAgentOutput(features, overlay);
  const hasAgentOverlay = colorCodingEnabled && overlay.legend && overlay.legend.length > 0;
  // True when Agent mode is selected but no color_rules overlay exists yet -
  // fall back to confidence-bucket coloring so the mode still shows useful data.
  const useAgentConfFallback = colorCodingEnabled && colorMode === 'agent' && !hasAgentOverlay;
  const overlayMapForColorCounts = overlay.overlay || {};
  const processedColorFeatures = features.filter(f =>
    featureCoord(f) &&
    f.geometry_type !== 'Polygon' &&
    f.geometry_type !== 'LineString' &&
    featureHasProcessedAgentOutput(f, overlayMapForColorCounts)
  );

  // Agent 2 validation summary counts from features
  const agent1Accepted = features.filter(f => f.validation_status === 'AUTO_ACCEPT').length;
  const agent1Rejected = features.filter(f => f.validation_status === 'REJECT').length;
  const agent1Review   = features.filter(f => f.validation_status === 'MANUAL_REVIEW').length;
  const agent1Done     = agent1Accepted + agent1Rejected + agent1Review;

  // Build KMZ legend categories from features
  const kmzLegend = {};
  const palette = ['#FF4136','#0074D9','#2ECC40','#FF851B','#B10DC9','#FFDC00','#01FF70','#F012BE','#7FDBFF','#85144b'];
  let ci = 0;
  features.forEach(f => {
    if (f.geometry_type === 'Polygon' || f.geometry_type === 'LineString') return;
    const cat = f.category || f.folder_path || 'Other';
    if (!kmzLegend[cat]) {
      kmzLegend[cat] = { color: f.style_color || palette[ci % palette.length], count: 0, type: f.geometry_type || 'Point' };
      ci++;
    }
    kmzLegend[cat].count++;
  });

  // Build agent legend: group by display_name
  const agentLegend = {};
  (overlay.legend || []).forEach(rule => {
    if (!agentLegend[rule.display_name]) agentLegend[rule.display_name] = [];
    agentLegend[rule.display_name].push(rule);
  });

  const legendItems = colorMode === 'agent' && hasAgentOverlay ? null : kmzLegend;

  const confidenceLegend = [
    { key: 'high', label: 'Confidence >90%', color: '#16a34a', desc: 'Highest confidence final coordinate' },
    { key: 'medium', label: 'Confidence 70%-90%', color: '#f59e0b', desc: 'Medium confidence final coordinate' },
    { key: 'other', label: 'Other <70%', color: '#6b7280', desc: 'Low confidence or missing score' },
    { key: 'invalid', label: 'Invalid / Rejected', color: '#ef4444', desc: 'Rejected or invalid coordinate' },
  ].map(item => {
    const count = processedColorFeatures.filter(f => confidenceBucket(f).key === item.key).length;
    const percent = processedColorFeatures.length ? Math.round((count / processedColorFeatures.length) * 100) : 0;
    return { ...item, count, percent };
  });

  const ruleClassifiedFeatures = processedColorFeatures.filter(f => mergeRuleBucket(f).key !== 'unclassified');
  const ruleTotal = ruleClassifiedFeatures.length;
  const simpleAgentLabel = (agentName) => {
    const value = String(agentName || '').trim();
    const lower = value.toLowerCase();
    if (!value) return 'Processed';
    if (/agent0_house_discovery|house discovery/.test(lower)) return 'House Discovery';
    if (/agent2_geocoding|geocoding|geocode|reverse geocoding|reverse_geocoding|google_geocoding|osm/.test(lower)) return 'Geocoding';
    if (/agent1_address_validator|address_validator|address validation|smarty|melissa/.test(lower)) return 'Address Validation';
    if (/agent3|parcel/.test(lower)) return 'Parcel';
    if (/agent4|building/.test(lower)) return 'Building';
    if (/agent5|streetview|street view/.test(lower)) return 'Street View';
    if (/agent6|final/.test(lower)) return 'Final Validation';
    return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  };
  const agentWiseCounts = ruleClassifiedFeatures.reduce((acc, f) => {
    const bucket = mergeRuleBucket(f).key;
    if (bucket !== 'verified') return acc;
    const label = f.final_source_agent_label || simpleAgentLabel(f.final_source_agent || (overlay.overlay || {})[f.id]?.agent_name || (overlay.overlay || {})[f.id]?.agent);
    acc[label] = (acc[label] || 0) + 1;
    return acc;
  }, {});
  const agentSortRank = (label) => {
    const order = ['Geocoding', 'Address Validation', 'Parcel', 'Building', 'Street View', 'Final Validation', 'Processed'];
    const index = order.indexOf(label);
    return index >= 0 ? index : 99;
  };
  const mergeRuleLegend = [
    { key: 'verified', label: 'Address Found', color: '#006100', desc: 'Address found and validated' },
    { key: 'duplicate', label: 'Duplicate Address', color: '#ffffff', desc: 'Duplicate address in uploaded source data' },
    { key: 'invalid', label: 'Address Not Found', color: '#C00000', desc: 'Address not found or could not be validated' },
    { key: 'new', label: 'New Address', color: '#9C6500', desc: 'New address identified but not present in original CSV/Excel' },
  ].map(item => {
    const count = ruleClassifiedFeatures.filter(f => mergeRuleBucket(f).key === item.key).length;
    const percent = ruleTotal ? Math.round((count / ruleTotal) * 100) : 0;
    return { ...item, count, percent };
  });
  const ruleCounts = Object.fromEntries(mergeRuleLegend.map(item => [item.key, item.count]));
  const agentWisePercentages = [
    ...Object.entries(agentWiseCounts)
      .map(([label, count]) => ({
        label,
        count,
        percent: ruleTotal ? ((count / ruleTotal) * 100) : 0,
        type: 'agent',
      }))
      .sort((a, b) => (agentSortRank(a.label) - agentSortRank(b.label)) || a.label.localeCompare(b.label)),
    ...[
      { label: 'Duplicate Address', count: ruleCounts.duplicate || 0, type: 'status' },
      { label: 'Invalid Address', count: ruleCounts.invalid || 0, type: 'status' },
      { label: 'New Address', count: ruleCounts.new || 0, type: 'status' },
    ]
      .filter(item => item.count > 0)
      .map(item => ({
        ...item,
        percent: ruleTotal ? ((item.count / ruleTotal) * 100) : 0,
      })),
  ];

  // All selectable filter labels with their colour for the dropdown
  const filterOptions = !colorCodingEnabled && colorMode !== 'kmz'
    ? []
    : colorMode === 'agent' && hasAgentOverlay
    ? (overlay.legend || []).map(rule => ({ label: rule.label, color: rule.color }))
    : colorMode === 'rule'
    ? mergeRuleLegend.filter(item => item.count > 0 || ['verified', 'duplicate', 'invalid'].includes(item.key))
    : colorMode === 'quality' || useAgentConfFallback
    ? confidenceLegend.filter(item => item.key !== 'invalid' || item.count > 0)
    : Object.entries(kmzLegend).map(([cat, info]) => ({ label: cat, color: info.color }));

  // Points visible after applying the active filter
  const _catKey = (f) => {
    const ov = (overlay.overlay || {})[f.id];
    if (!featureHasProcessedAgentOutput(f, overlay.overlay || {}) && colorMode !== 'kmz') return null;
    if (colorMode === 'agent' && hasAgentOverlay) return ov ? ov.label : null;
    if (colorMode === 'rule') return mergeRuleBucket(f).label;
    if (colorMode === 'quality' || useAgentConfFallback) return confidenceBucket(f).label;
    return f.category || f.folder_path || 'Other';
  };
  // catKey helper for filteredCount - KMZ category always uses folder/category name
  const _fCatKey = (f) => f.category || f.folder_path || 'Other';
  const filteredCount = (activeFilter || activeCategory)
    ? features.filter(f => {
        if (!featureCoord(f) || f.geometry_type === 'Polygon' || f.geometry_type === 'LineString') return false;
        // KMZ layer filter: compare by folder/category name, not mode-specific bucket
        if (activeCategory && _fCatKey(f) !== activeCategory) return false;
        if (!activeFilter) return true;
        if (!featureHasProcessedAgentOutput(f, overlay.overlay || {}) && colorMode !== 'kmz') return false;
        if (colorMode === 'agent' && hasAgentOverlay) {
          const ov = (overlay.overlay || {})[f.id];
          return ov && ov.label === activeFilter;
        }
        if (colorMode === 'rule') return mergeRuleBucket(f).label === activeFilter;
        if (colorMode === 'quality' || useAgentConfFallback) return confidenceBucket(f).label === activeFilter;
        return _fCatKey(f) === activeFilter;
      }).length
    : addressPointFeatures.length;

  const addressQuery = addressSearch.trim().toLowerCase();
  const addressMatches = dedupeAddressMatches(features
    .filter(f => featureCoord(f))
    .filter(isAddressSearchFeature)
    .filter(f => {
      if (!addressQuery) return true;
      const haystack = addressSearchHaystack(f).toLowerCase();
      return haystack.includes(addressQuery) || normalizeAddressSearchKey(haystack).includes(normalizeAddressSearchKey(addressQuery));
    })
    , coordinateMode).slice(0, 30);
  const selectedFeature = features.find(f => f.id === selectedFeatureId);
  const selectedFeatureCoord = featureCoord(selectedFeature);
  const selectedFeatureRule = selectedFeature ? mergeRuleBucket(selectedFeature) : null;
  const primaryAddressMatch = addressMatches[0];
  const isLightTheme = theme === 'light';
  const panelClasses = isLightTheme
    ? 'bg-white/95 backdrop-blur border border-slate-300 text-slate-900'
    : 'bg-dark-900/95 backdrop-blur border border-dark-600 text-white';
  const subtlePanelClasses = isLightTheme
    ? 'bg-slate-50 border-slate-200 text-slate-900'
    : 'bg-dark-800/80 border-dark-600 text-gray-200';
  const buttonClasses = isLightTheme
    ? 'bg-white hover:bg-slate-100 border border-slate-300 text-slate-800 shadow-sm'
    : 'bg-dark-700 hover:bg-dark-600 border border-dark-600 text-white';
  const mutedTextClasses = isLightTheme ? 'text-slate-500' : 'text-gray-400';
  const softTextClasses = isLightTheme ? 'text-slate-700' : 'text-gray-300';
  const dividerClasses = isLightTheme ? 'border-slate-200' : 'border-dark-600';
  const segmentedClasses = isLightTheme
    ? 'flex items-center bg-slate-100 border border-slate-300 rounded-lg overflow-hidden text-xs shadow-sm'
    : 'flex items-center bg-dark-700 border border-dark-600 rounded-lg overflow-hidden text-xs';
  const segmentIdleClasses = isLightTheme
    ? 'text-slate-600 hover:text-slate-950 hover:bg-white'
    : 'text-gray-400 hover:text-white hover:bg-dark-600';
  const searchResultBaseClasses = isLightTheme
    ? 'bg-white border-slate-200 hover:border-cyan-500/70 hover:bg-slate-50 text-slate-900'
    : 'bg-dark-800/80 border-dark-600 hover:border-cyan-500/70 text-white';
  const searchResultSelectedClasses = isLightTheme
    ? 'bg-yellow-50 border-yellow-400/80 text-slate-950'
    : 'bg-yellow-500/15 border-yellow-400/70 text-white';
  const streetPanelClasses = isLightTheme
    ? 'flex flex-col bg-white border-l border-slate-300 transition-all duration-300 overflow-hidden'
    : 'flex flex-col bg-black border-l border-dark-600 transition-all duration-300 overflow-hidden';
  const streetHeaderClasses = isLightTheme
    ? 'flex items-center gap-2 bg-white px-3 py-2 border-b border-slate-200 flex-shrink-0 text-slate-900 shadow-sm'
    : 'flex items-center gap-2 bg-dark-800 px-3 py-2 border-b border-dark-600 flex-shrink-0 text-white';
  const iconButtonClasses = isLightTheme
    ? 'w-7 h-7 flex items-center justify-center hover:bg-slate-100 rounded text-slate-500 hover:text-slate-950 transition'
    : 'w-7 h-7 flex items-center justify-center hover:bg-dark-600 rounded text-gray-400 hover:text-white transition';

  return (
    <div className={isFullscreen ? `fixed inset-0 z-50 flex flex-col overflow-hidden ${isLightTheme ? 'bg-white text-slate-900' : 'bg-dark-900 text-white'} p-0` : `${isLightTheme ? 'bg-slate-50 text-slate-900 rounded-xl' : 'text-white'} h-full flex flex-col overflow-hidden p-0`}>
      <div className="flex-shrink-0 mb-3 space-y-2">
        {!isFullscreen && (
          <Breadcrumbs items={[
            { label: 'Projects', onClick: () => navigate('/') },
            { label: job?.source_file || 'Project', onClick: () => navigate(`/projects/${jobId}`) },
            { label: 'Map' },
          ]} />
        )}
        <div className={`map-toolbar app-card rounded-xl ${panelClasses}`}>
          <div className="map-toolbar-primary">
            <button type="button" onClick={() => navigate(`/projects/${jobId}`)} className={`h-9 flex items-center gap-1 rounded-lg px-3 text-xs font-semibold ${buttonClasses}`} title="Back to project">
              <Icon name="arrowLeft" size={14} /> <span className="hidden sm:inline">Back</span>
            </button>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-bold truncate max-w-[12rem] sm:max-w-xs md:max-w-md">{job?.source_file || 'Map'}</div>
              <div className={`text-[10px] font-mono ${mutedTextClasses}`}>{totalWithCoords} addresses - {filteredCount} visible</div>
            </div>
            {isProcessing && (
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400 px-2 py-1 rounded-full bg-amber-500/10 border border-amber-500/30">
                <Spinner size={12} /> {agentStatus?.overall_progress ?? 0}%
              </span>
            )}
            {!isProcessing && job?.status && <StatusBadge status={job.status} />}
            <button type="button" className="map-toolbar-toggle" onClick={() => setToolbarExpanded(v => !v)} aria-expanded={toolbarExpanded} aria-label={toolbarExpanded ? 'Hide map tools' : 'Show map tools'}>
              {toolbarExpanded ? 'Less' : 'Tools'}
              <Icon name={toolbarExpanded ? 'chevronUp' : 'chevronDown'} size={14} />
            </button>
          </div>
          <div className={`map-toolbar-secondary ${toolbarExpanded ? 'expanded' : ''}`}>
            <div className="map-toolbar-group">
              <span className="map-toolbar-group-label">Pipeline</span>
              <button type="button" onClick={handleMapProcess} disabled={isProcessing}
                className={`h-9 flex items-center gap-1 rounded-lg px-3 text-xs font-semibold ${isProcessing ? 'opacity-60 cursor-not-allowed ' : ''}${buttonClasses}`}>
                {isProcessing ? <><Spinner size={14} /> Running</> : <><Icon name="play" size={14} /> Process</>}
              </button>
              <button type="button" onClick={loadGeoData} className={`h-9 text-xs px-3 rounded-lg ${buttonClasses} inline-flex items-center gap-1`}><Icon name="refresh" size={14} /> Refresh</button>
            </div>
            <div className="map-toolbar-divider" aria-hidden="true" />
            <div className="map-toolbar-group">
              <span className="map-toolbar-group-label">View</span>
              <button type="button" onClick={() => setIsFullscreen(!isFullscreen)} className={`h-9 text-xs px-3 rounded-lg ${buttonClasses} inline-flex items-center gap-1`}>
                <Icon name={isFullscreen ? 'collapse' : 'expand'} size={14} /> {isFullscreen ? 'Exit' : 'Full'}
              </button>
              <button type="button" onClick={toggleTheme} className={`h-9 text-xs px-3 rounded-lg ${buttonClasses}`}>{isLightTheme ? 'Dark' : 'Light'}</button>
              <button type="button" onClick={() => setSvFullscreen(f => !f)} disabled={!selectedPoint} className={`h-9 text-xs px-3 rounded-lg ${buttonClasses} disabled:opacity-40 inline-flex items-center gap-1`}>
                <Icon name="pin" size={14} /> Street
              </button>
            </div>
            <div className="map-toolbar-divider" aria-hidden="true" />
            <div className="map-toolbar-group">
              <span className="map-toolbar-group-label">Explore</span>
              <button type="button" onClick={() => setLayersOpen(v => !v)} className={`h-9 text-xs px-3 rounded-lg ${buttonClasses} inline-flex items-center gap-1 ${layersOpen ? 'ring-2 ring-cyan-400/60' : ''}`}>
                <Icon name="layers" size={14} /> Layers
              </button>
              {measureResult && measureResult.label && (
                <span className="map-measure-badge tabular-nums" title="Active measurement">
                  <Icon name="ruler" size={12} /> {measureResult.label}
                </span>
              )}
            </div>
            <div className="map-toolbar-divider" aria-hidden="true" />
            <div className="map-toolbar-group">
              <span className="map-toolbar-group-label">Coords</span>
              <div className={segmentedClasses}>
                <button type="button" onClick={() => setCoordinateMode('updated')} className={`px-2.5 py-1.5 transition ${coordinateMode==='updated' ? 'bg-green-600 text-white' : segmentIdleClasses}`}>Updated</button>
                <button type="button" onClick={() => setCoordinateMode('raw')} className={`px-2.5 py-1.5 transition ${coordinateMode==='raw' ? 'bg-red-600 text-white' : segmentIdleClasses}`}>Stored</button>
              </div>
              {changedCoordsCount > 0 && (
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded inline-flex items-center gap-1" style={{color:'#f59e0b',background:'rgba(245,158,11,0.12)',border:'1px solid rgba(245,158,11,0.35)'}} title={`${changedCoordsCount} records have updated coordinates`}>
                  <Icon name="bolt" size={11} /> {changedCoordsCount}
                </span>
              )}
            </div>
            <div className="map-toolbar-divider" aria-hidden="true" />
            <div className="map-toolbar-group">
              <span className="map-toolbar-group-label">Colors</span>
              <div className={segmentedClasses}>
                <button type="button" onClick={() => setColorMode('rule')} className={`px-2.5 py-1.5 transition ${colorCodingEnabled && colorMode==='rule' ? 'bg-emerald-600 text-white' : segmentIdleClasses}`}>Rule</button>
                <button type="button" onClick={() => setColorMode('quality')} className={`px-2.5 py-1.5 transition ${colorCodingEnabled && colorMode==='quality' ? 'bg-blue-600 text-white' : segmentIdleClasses}`}>Quality</button>
                <button type="button" onClick={() => setColorMode('kmz')} className={`px-2.5 py-1.5 transition ${colorMode==='kmz' ? 'bg-indigo-600 text-white' : segmentIdleClasses}`}>KMZ</button>
                <button type="button" onClick={() => setColorMode('agent')} className={`px-2.5 py-1.5 transition ${colorCodingEnabled && colorMode==='agent' ? 'bg-green-600 text-white' : segmentIdleClasses}`}>Agent{hasAgentOverlay ? ` (${overlay.legend.length})` : ''}</button>
              </div>
            </div>
            {(() => {
              const catMap = {};
              features.forEach(f => {
                const cat = f.category || f.folder_path;
                if (!cat) return;
                catMap[cat] = (catMap[cat] || 0) + 1;
              });
              const cats = Object.entries(catMap).map(([label, count]) => ({ label, count })).sort((a, b) => a.label.localeCompare(b.label));
              if (cats.length === 0) return null;
              return (
                <>
                  <div className="map-toolbar-divider" aria-hidden="true" />
                  <div className="map-toolbar-group">
                    <span className="map-toolbar-group-label">Category</span>
                    <select value={activeCategory || ''} onChange={e => setActiveCategory(e.target.value || null)}
                      className={`h-9 text-xs rounded-lg px-2 outline-none cursor-pointer max-w-[10rem] ${isLightTheme ? 'bg-white border border-slate-300 text-slate-900' : 'bg-dark-700 border border-dark-600 text-white'}`}>
                      <option value="">All</option>
                      {cats.map(({ label, count }) => <option key={label} value={label}>{label} ({count})</option>)}
                    </select>
                    {activeCategory && (
                      <button type="button" onClick={() => setActiveCategory(null)} className={`h-9 text-xs rounded-lg px-2 ${buttonClasses}`}>Clear</button>
                    )}
                  </div>
                </>
              );
            })()}
            <div className="map-toolbar-divider hidden md:block" aria-hidden="true" />
            <div className="map-toolbar-group ml-auto">
              <span className="map-toolbar-group-label hidden md:inline">Export</span>
              <a href={`${API}/export/csv?job_id=${jobId}`} onClick={e => { e.preventDefault(); window.location = `${API}/export/csv?job_id=${jobId}&_t=${getToken()}`; }} className={`text-center text-xs px-3 py-2 rounded-lg ${buttonClasses}`}>CSV</a>
              <a href={`${API}/export/excel?job_id=${jobId}`} onClick={e => { e.preventDefault(); window.location = `${API}/export/excel?job_id=${jobId}&_t=${getToken()}`; }} className={`text-center text-xs px-3 py-2 rounded-lg ${isLightTheme ? 'bg-emerald-600 hover:bg-emerald-700 border border-emerald-600 text-white' : 'bg-emerald-700 hover:bg-emerald-600 border border-emerald-600 text-white'}`}>Excel</a>
              <a href={`${API}/export/kml?job_id=${jobId}`} onClick={e => { e.preventDefault(); window.location = `${API}/export/kml?job_id=${jobId}&_t=${getToken()}`; }} className={`text-center text-xs px-3 py-2 rounded-lg ${buttonClasses}`}>KML</a>
              <a href={`${API}/export/kmz?job_id=${jobId}`} onClick={e => { e.preventDefault(); window.location = `${API}/export/kmz?job_id=${jobId}&_t=${getToken()}`; }} className={`text-center text-xs px-3 py-2 rounded-lg ${buttonClasses}`}>KMZ</a>
            </div>
          </div>
        </div>
      </div>

      {isFullscreen && (
        <button onClick={() => setIsFullscreen(false)}
          className={`fixed top-3 right-3 z-[1400] flex items-center gap-2 text-xs px-4 py-2 rounded-lg shadow-xl ${buttonClasses}`}>
          <Icon name="collapse" size={14} /> Exit Fullscreen
        </button>
      )}

      <div className={`relative flex gap-0 ${isFullscreen ? 'flex-1 min-h-0 w-full ftth-map-fullscreen' : `flex-1 min-h-0 rounded-xl overflow-hidden border ${isLightTheme ? 'border-slate-300' : 'border-dark-600'}`}`}>

        {/* Map canvas - shrinks when street view is open */}
        <div
          className="map-canvas relative"
          style={{
            height: '100%',
            flex: isFullscreen ? '1 1 100%' : ((selectedPoint && !svFullscreen) ? '0 0 55%' : '1 1 100%'),
            transition: 'flex 0.3s ease',
            minWidth: 0,
          }}>
          <div ref={mapRef} className="map-canvas-leaflet" style={{ height: '100%', width: '100%' }} />

          <MapLayersPanel
            isOpen={layersOpen}
            onToggle={() => setLayersOpen(v => !v)}
            baseMaps={baseMapCatalog}
            activeBasemap={activeBasemap}
            onBasemapChange={handleBasemapChange}
            overlays={{ 'Data Points': markersLayerRef.current, 'Boundary Lines': boundaryLayerRef.current }}
            overlayVisibility={overlayVisibility}
            onOverlayToggle={handleOverlayToggle}
            isLightTheme={isLightTheme}
            panelClasses={panelClasses}
            buttonClasses={buttonClasses}
            Icon={Icon}
          />
          {totalWithCoords === 0 && (
            <div className="map-empty-overlay">
              <div className="map-empty-card">
                <div className="empty-state-icon mx-auto mb-3"><Icon name="pin" size={26} /></div>
                <h3 className="empty-state-title">No mappable coordinates</h3>
                <p className="empty-state-desc">
                  {features.length > 0
                    ? `${features.length} records loaded but no address points have lat/lon yet. Run the pipeline or upload address coordinates.`
                    : 'Upload a CSV with coordinates or a KMZ/KML file, then open the map again.'}
                </p>
                {isProcessing ? (
                  <span className="inline-flex items-center gap-2 text-sm text-amber-600 dark:text-amber-400 mt-2">
                    <Spinner size={14} /> Running pipeline…
                  </span>
                ) : (
                  <button type="button" onClick={handleMapProcess} className="app-button app-button-primary text-white px-4 py-2 rounded-lg text-sm font-semibold mt-2 inline-flex items-center gap-2">
                    <Icon name="play" size={14} /> Run pipeline
                  </button>
                )}
              </div>
            </div>
          )}

          <MapSearchPanel
            totalWithCoords={totalWithCoords}
            searchInputRef={searchInputRef}
            addressSearch={addressSearch}
            setAddressSearch={setAddressSearch}
            setShowAddressMatches={setShowAddressMatches}
            showAddressMatches={showAddressMatches}
            addressMatches={addressMatches}
            selectedFeatureId={selectedFeatureId}
            primaryAddressMatch={primaryAddressMatch}
            selectFeature={selectFeature}
            featureCoord={featureCoord}
            featureLabel={featureLabel}
            coordinateMode={coordinateMode}
            panelClasses={panelClasses}
            isLightTheme={isLightTheme}
            searchResultBaseClasses={searchResultBaseClasses}
            searchResultSelectedClasses={searchResultSelectedClasses}
          />

          <MapSelectedPanel
            selectedFeature={selectedFeature}
            selectedFeatureCoord={selectedFeatureCoord}
            selectedFeatureRule={selectedFeatureRule}
            svFullscreen={svFullscreen}
            panelClasses={panelClasses}
            buttonClasses={buttonClasses}
            featureLabel={featureLabel}
            coordLabel={coordLabel}
            hasCoordChange={hasCoordChange}
            coordDeltaMeters={coordDeltaMeters}
            recenterSelectedFeature={recenterSelectedFeature}
            onClose={() => { setSelectedFeatureId(null); setSelectedPoint(null); setSvFullscreen(false); }}
            Icon={Icon}
          />
          {/* Legend - floating bottom-left, click any item to filter the map */}
          <div className={`map-legend-panel rounded-lg p-3 ${panelClasses}`} style={{ minWidth: legendMinimized ? '42px' : '250px', maxWidth: '320px' }}>
          <div className="flex items-center justify-between gap-2 mb-2">
            {!legendMinimized && (
              <div className="text-xs font-bold text-blue-400 uppercase tracking-wide">
                {!colorCodingEnabled && colorMode !== 'kmz' ? 'Map Legend' : colorMode === 'rule' ? 'Rule Legend' : colorMode === 'quality' ? 'Confidence Legend' : colorMode === 'agent' && hasAgentOverlay ? 'Agent Legend' : 'Legend'}
              </div>
            )}
            <button
              onClick={() => setLegendMinimized(!legendMinimized)}
              className={`w-6 h-6 flex items-center justify-center rounded text-xs ${buttonClasses}`}
              title={legendMinimized ? 'Show legend' : 'Minimize legend'}>
              {legendMinimized ? '+' : '-'}
            </button>
          </div>
          {!legendMinimized && (
          <>
          {!colorCodingEnabled && colorMode !== 'kmz' ? (
            <div className={`rounded border p-2 text-xs ${subtlePanelClasses}`}>
              <div className={`font-semibold ${isLightTheme ? 'text-slate-800' : 'text-gray-200'}`}>No color coding yet</div>
              <div className={`mt-1 ${mutedTextClasses}`}>Run one or more agents to enable Rule, Quality, and Agent colors.</div>
              <div className={`mt-2 font-mono ${mutedTextClasses}`}>Showing neutral uploaded addresses: {totalWithCoords}</div>
            </div>
          ) : colorMode === 'rule' ? (
            <>
              <div className="flex items-center justify-end mb-2">
                {activeFilter && <span className="text-xs text-blue-400 font-semibold">filtered</span>}
              </div>
              <div className="space-y-1">
                {mergeRuleLegend
                  .filter(item => item.count > 0 || ['verified', 'duplicate', 'invalid'].includes(item.key))
                  .map(item => (
                  <div key={item.label}
                    className={`flex items-center gap-2 cursor-pointer rounded px-1 -mx-1 py-0.5 transition-colors select-none ${
                      activeFilter === item.label ? (isLightTheme ? 'bg-blue-50 ring-1 ring-blue-400/50' : 'bg-dark-600 ring-1 ring-blue-500/50') : (isLightTheme ? 'hover:bg-slate-100' : 'hover:bg-dark-700/70')
                    }`}
                    title={item.desc}
                    onClick={() => setActiveFilter(activeFilter === item.label ? null : item.label)}>
                    <div style={{width:'11px',height:'11px',borderRadius:'50%',background:item.color,border:item.key === 'duplicate' ? '1.5px solid #94a3b8' : '2px solid rgba(255,255,255,0.8)',flexShrink:0}} />
                    <span className={`text-xs flex-1 ${activeFilter === item.label ? (isLightTheme ? 'text-slate-950 font-bold' : 'text-white font-bold') : (isLightTheme ? 'text-slate-700' : 'text-gray-300')}`}>{item.label}</span>
                    <span className={`text-xs font-mono ${isLightTheme ? 'text-slate-500' : 'text-gray-500'}`}>{item.count} ({item.percent}%)</span>
                    {activeFilter === item.label && <span className="text-blue-400 text-xs flex-shrink-0"><Icon name="check" size={13} /></span>}
                  </div>
                ))}
              </div>
              <hr className={`${dividerClasses} my-2`} />
              {agentWisePercentages.length > 0 && (
                <>
                  <div className={`text-[10px] font-bold uppercase tracking-wide mb-1 ${isLightTheme ? 'text-slate-500' : 'text-gray-400'}`}>Agent Wise Percentage</div>
                  <div className={`text-xs ${mutedTextClasses} space-y-0.5 mb-2`}>
                    <div className={`grid grid-cols-[1fr_44px_58px] items-center gap-2 text-[10px] font-semibold uppercase tracking-wide ${isLightTheme ? 'text-slate-500' : 'text-gray-400'}`}>
                      <span>Agent / Status</span>
                      <span className="text-right">Count</span>
                      <span className="text-right">Percentage</span>
                    </div>
                    {agentWisePercentages.map(item => (
                      <div key={item.label} className="grid grid-cols-[1fr_44px_58px] items-center gap-2">
                        <span className={softTextClasses}>{item.label}</span>
                        <span className="font-mono text-right">{item.count}</span>
                        <span className="font-mono text-right">{item.percent.toFixed(2)}%</span>
                      </div>
                    ))}
                    <div className={`grid grid-cols-[1fr_44px_58px] items-center gap-2 pt-1 mt-1 border-t ${dividerClasses} font-semibold ${isLightTheme ? 'text-slate-700' : 'text-gray-200'}`}>
                      <span>Total</span>
                      <span className="font-mono text-right">{ruleTotal}</span>
                      <span className="font-mono text-right">{ruleTotal ? '100.00%' : '0.00%'}</span>
                    </div>
                  </div>
                  <hr className={`${dividerClasses} my-2`} />
                </>
              )}
              <div className={`text-xs ${mutedTextClasses} space-y-0.5`}>
                <div>
                  Valid: <span className="font-mono">{ruleCounts.verified || 0}</span>
                  <span className="mx-1">-</span>
                  Duplicate: <span className="font-mono">{ruleCounts.duplicate || 0}</span>
                </div>
                <div>Total visible: {(activeFilter || activeCategory) ? filteredCount : ruleTotal}</div>
                <div>Applies to raw and updated coordinates</div>
              </div>
            </>
          ) : colorMode === 'quality' ? (
            <>
              <div className="flex items-center justify-end mb-2">
                {activeFilter && <span className="text-xs text-blue-400 font-semibold">filtered</span>}
              </div>
              <div className="space-y-1">
                {confidenceLegend
                  .filter(item => item.key !== 'invalid' || item.count > 0)
                  .map(item => (
                  <div key={item.label}
                    className={`flex items-center gap-2 cursor-pointer rounded px-1 -mx-1 py-0.5 transition-colors select-none ${
                      activeFilter === item.label ? (isLightTheme ? 'bg-blue-50 ring-1 ring-blue-400/50' : 'bg-dark-600 ring-1 ring-blue-500/50') : (isLightTheme ? 'hover:bg-slate-100' : 'hover:bg-dark-700/70')
                    }`}
                    title={item.desc}
                    onClick={() => setActiveFilter(activeFilter === item.label ? null : item.label)}>
                    <div style={{width:'11px',height:'11px',borderRadius:'50%',background:item.color,border:'2px solid rgba(255,255,255,0.8)',flexShrink:0,
                      boxShadow: item.color === '#ef4444' ? '0 0 5px rgba(239,68,68,0.6)' : 'none'}} />
                    <span className={`text-xs flex-1 ${activeFilter === item.label ? (isLightTheme ? 'text-slate-950 font-bold' : 'text-white font-bold') : (isLightTheme ? 'text-slate-700' : 'text-gray-300')}`}>{item.label}</span>
                    <span className={`text-xs font-mono ${isLightTheme ? 'text-slate-500' : 'text-gray-500'}`}>{item.count} ({item.percent}%)</span>
                    {activeFilter === item.label && <span className="text-blue-400 text-xs flex-shrink-0"><Icon name="check" size={13} /></span>}
                  </div>
                ))}
              </div>
              <hr className={`${dividerClasses} my-2`} />
              <div className={`text-xs ${mutedTextClasses} space-y-0.5`}>
                <div>Total visible: {(activeFilter || activeCategory) ? filteredCount : totalWithCoords}</div>
                {agent1Done > 0 && <div className={mutedTextClasses}>Agent 2 processed: {agent1Done}</div>}
              </div>
            </>
          ) : colorMode === 'agent' && hasAgentOverlay ? (
            <>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-bold text-green-400 uppercase tracking-wide">Agent Legend</h3>
                {activeFilter && <span className="text-xs text-blue-400 font-semibold">filtered</span>}
              </div>
              {Object.entries(agentLegend).map(([agentDisplayName, rules]) => {
                const visRules = rules.filter(rule => !activeFilter || rule.label === activeFilter);
                if (visRules.length === 0) return null;
                const agentTotal = processedColorFeatures.filter(f => (overlay.overlay || {})[f.id]?.agent === agentDisplayName).length;
                return (
                  <div key={agentDisplayName} className="mb-2">
                    <div className={`text-xs font-semibold mb-1 ${mutedTextClasses}`}>{agentDisplayName} <span className="font-normal">({agentTotal} - {totalWithCoords ? Math.round(agentTotal / totalWithCoords * 100) : 0}%)</span></div>
                    {visRules.map((rule, i) => {
                      const ruleCount = processedColorFeatures.filter(f => (overlay.overlay || {})[f.id]?.label === rule.label).length;
                      const rulePct = processedColorFeatures.length ? Math.round(ruleCount / processedColorFeatures.length * 100) : 0;
                      return (
                        <div key={i}
                          className={`flex items-center gap-2 mb-0.5 cursor-pointer rounded px-1 -mx-1 py-0.5 transition-colors select-none ${
                            activeFilter === rule.label ? (isLightTheme ? 'bg-blue-50 ring-1 ring-blue-400/50' : 'bg-dark-600 ring-1 ring-blue-500/50') : (isLightTheme ? 'hover:bg-slate-100' : 'hover:bg-dark-700/70')
                          }`}
                          onClick={() => setActiveFilter(activeFilter === rule.label ? null : rule.label)}>
                          <div style={{width:'10px',height:'10px',borderRadius:'50%',background:rule.color,border:'1.5px solid #fff',flexShrink:0}} />
                          <span className={`text-xs flex-1 ${activeFilter === rule.label ? (isLightTheme ? 'text-slate-950 font-bold' : 'text-white font-bold') : softTextClasses}`}>{rule.label}</span>
                          <span className={`text-xs font-mono ${mutedTextClasses}`}>{ruleCount} ({rulePct}%)</span>
                          {activeFilter === rule.label && <span className="text-blue-400 text-xs flex-shrink-0"><Icon name="check" size={13} /></span>}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
              <hr className={`${dividerClasses} my-2`} />
              <div className={`text-xs ${mutedTextClasses} space-y-0.5`}>
                <div>{(activeFilter || activeCategory) ? `Showing: ${filteredCount} addresses` : `${Object.keys(overlay.overlay || {}).length} of ${totalWithCoords} addresses have agent data`}</div>
                <div>Percentages are of all map points</div>
              </div>
            </>
          ) : useAgentConfFallback ? (
            <>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-bold text-green-400 uppercase tracking-wide">Agent Confidence</h3>
                {activeFilter && <span className="text-xs text-blue-400 font-semibold">filtered</span>}
              </div>
              <div className={`text-[10px] ${mutedTextClasses} mb-2`}>Based on best available agent score</div>
              <div className="space-y-1">
                {confidenceLegend.filter(item => item.key !== 'invalid' || item.count > 0).map(item => (
                  <div key={item.label}
                    className={`flex items-center gap-2 cursor-pointer rounded px-1 -mx-1 py-0.5 transition-colors select-none ${
                      activeFilter === item.label ? (isLightTheme ? 'bg-blue-50 ring-1 ring-blue-400/50' : 'bg-dark-600 ring-1 ring-blue-500/50') : (isLightTheme ? 'hover:bg-slate-100' : 'hover:bg-dark-700/70')
                    }`}
                    title={item.desc}
                    onClick={() => setActiveFilter(activeFilter === item.label ? null : item.label)}>
                    <div style={{width:'11px',height:'11px',borderRadius:'50%',background:item.color,border:'2px solid rgba(255,255,255,0.8)',flexShrink:0,
                      boxShadow: item.color === '#ef4444' ? '0 0 5px rgba(239,68,68,0.6)' : 'none'}} />
                    <span className={`text-xs flex-1 ${activeFilter === item.label ? (isLightTheme ? 'text-slate-950 font-bold' : 'text-white font-bold') : (isLightTheme ? 'text-slate-700' : 'text-gray-300')}`}>{item.label}</span>
                    <span className={`text-xs font-mono ${isLightTheme ? 'text-slate-500' : 'text-gray-500'}`}>{item.count} ({item.percent}%)</span>
                    {activeFilter === item.label && <span className="text-blue-400 text-xs flex-shrink-0"><Icon name="check" size={13} /></span>}
                  </div>
                ))}
              </div>
              <hr className={`${dividerClasses} my-2`} />
              <div className={`text-xs ${mutedTextClasses} space-y-0.5`}>
                <div>Total visible: {(activeFilter || activeCategory) ? filteredCount : totalWithCoords}</div>
                {agent1Done > 0 && <div>Agent 2 processed: {agent1Done}</div>}
                <div className={`text-[10px] italic ${mutedTextClasses}`}>Run agents to enable per-agent overlay colors</div>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2">
                <h3 className={`text-xs font-bold uppercase tracking-wide ${isLightTheme ? 'text-slate-700' : 'text-gray-300'}`}>Legend</h3>
                {activeFilter && <span className="text-xs text-blue-400 font-semibold">filtered</span>}
              </div>
              <div className="space-y-0.5">
                {Object.entries(kmzLegend)
                  .filter(([cat]) => !activeFilter || cat === activeFilter)
                  .map(([cat, info]) => (
                  <div key={cat}
                    className={`flex items-center gap-2 cursor-pointer rounded px-1 -mx-1 py-0.5 transition-colors select-none ${
                      activeFilter === cat ? (isLightTheme ? 'bg-blue-50 ring-1 ring-blue-400/50' : 'bg-dark-600 ring-1 ring-blue-500/50') : (isLightTheme ? 'hover:bg-slate-100' : 'hover:bg-dark-700/70')
                    }`}
                    onClick={() => setActiveFilter(activeFilter === cat ? null : cat)}>
                    {info.type === 'LineString' ? (
                      <div style={{width:'16px',height:'3px',background:info.color,borderRadius:'2px',flexShrink:0}} />
                    ) : info.type === 'Polygon' ? (
                      <div style={{width:'12px',height:'12px',background:info.color,opacity:0.4,border:'2px solid '+info.color,borderRadius:'2px',flexShrink:0}} />
                    ) : (
                      <div style={{width:'10px',height:'10px',borderRadius:'50%',background:info.color,border:'1.5px solid #fff',flexShrink:0}} />
                    )}
                    <span className={`text-xs flex-1 ${activeFilter === cat ? (isLightTheme ? 'text-slate-950 font-bold' : 'text-white font-bold') : softTextClasses}`}>{cat}</span>
                    <span className={`text-xs ${mutedTextClasses}`}>{info.count}</span>
                    {activeFilter === cat && <span className="text-blue-400 text-xs flex-shrink-0"><Icon name="check" size={13} /></span>}
                  </div>
                ))}
              </div>
              <hr className={`${dividerClasses} my-2`} />
              <div className={`text-xs ${mutedTextClasses} space-y-0.5`}>
                <div>{(activeFilter || activeCategory) ? `Showing: ${filteredCount}` : `Total: ${totalWithCoords}`}</div>
                {!activeFilter && <div>Points: {features.filter(f=>f.geometry_type==='Point').length}</div>}
                {!activeFilter && <div>Lines: {features.filter(f=>f.geometry_type==='LineString').length}</div>}
                {!activeFilter && <div>Polygons: {features.filter(f=>f.geometry_type==='Polygon').length}</div>}
                {agent1Done > 0 && (
                  <>
                    <hr className={`${dividerClasses} my-1`} />
                    <div className={`font-semibold ${softTextClasses}`}>Agent 2 Validation</div>
                    <div className="flex items-center gap-1"><span className="w-2 h-2 rounded-full flex-shrink-0" style={{background:'#34d399'}} /> Accepted: {agent1Accepted}</div>
                    {agent1Review > 0 && <div className="flex items-center gap-1"><span className="w-2 h-2 rounded-full flex-shrink-0" style={{background:'#fbbf24'}} /> Review: {agent1Review}</div>}
                    <div className="flex items-center gap-1"><span className="w-2 h-2 rounded-full flex-shrink-0" style={{background:'#f87171'}} /> Rejected: {agent1Rejected}</div>
                  </>
                )}
              </div>
            </>
          )}
          {activeFilter && (
            <div className={`mt-2 pt-2 border-t ${dividerClasses}`}>
              <button onClick={() => setActiveFilter(null)}
                className="w-full text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1">
                <Icon name="x" size={13} /><span className="truncate"> Clear: "{activeFilter}"</span>
              </button>
            </div>
          )}
          </>
          )}
          </div>

          <div className="map-mobile-controls" aria-label="Map quick controls">
            <button type="button" onClick={refitMapToPoints} disabled={totalWithCoords === 0} className={`map-mobile-control-btn ${buttonClasses}`} title="Fit all addresses">
              <Icon name="globe" size={15} />
              <span>Fit</span>
            </button>
            <button type="button" onClick={() => setLegendMinimized(v => !v)} className={`map-mobile-control-btn ${buttonClasses}`} title={legendMinimized ? 'Show legend' : 'Hide legend'}>
              <Icon name="grid" size={15} />
              <span>Legend</span>
            </button>
            {totalWithCoords > 0 && (
              <button type="button" onClick={() => { searchInputRef.current?.focus(); setShowAddressMatches(true); }} className={`map-mobile-control-btn ${buttonClasses}`} title="Search addresses">
                <Icon name="search" size={15} />
                <span>Search</span>
              </button>
            )}
            <button type="button" onClick={() => setLayersOpen(v => !v)} className={`map-mobile-control-btn ${buttonClasses}`} title="Map layers">
              <Icon name="layers" size={15} />
              <span>Layers</span>
            </button>
          </div>
        </div>

        <StreetViewPanel
          selectedPoint={selectedPoint}
          gmapsKey={gmapsKey}
          isLightTheme={isLightTheme}
          svFullscreen={svFullscreen}
          showKeyInput={showKeyInput}
          keyDraft={keyDraft}
          setKeyDraft={setKeyDraft}
          setShowKeyInput={setShowKeyInput}
          setGmapsKey={setGmapsKey}
          setSvFullscreen={setSvFullscreen}
          onClose={() => { setSelectedPoint(null); setSvFullscreen(false); }}
          recordId={selectedFeatureId}
          fetchStreetViewMeta={fetchStreetViewMeta}
          Icon={Icon}
          mutedTextClasses={mutedTextClasses}
          streetHeaderClasses={streetHeaderClasses}
          streetPanelClasses={streetPanelClasses}
          iconButtonClasses={iconButtonClasses}
          style={svFullscreen
            ? {}
            : isFullscreen
              ? { flex: '0 0 0%', minWidth: 0, borderLeft: 0 }
              : selectedPoint
                ? { flex: '0 0 45%', minWidth: 0 }
                : { flex: '0 0 0%', minWidth: 0 }}
        />
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.MapPage = MapPage;
