/** @jsx React.createElement */

const { useState, useEffect, useRef, useCallback } = React;

function StreetViewPanel({
  selectedPoint,
  gmapsKey,
  isLightTheme,
  svFullscreen,
  showKeyInput,
  keyDraft,
  setKeyDraft,
  setShowKeyInput,
  setGmapsKey,
  setSvFullscreen,
  onClose,
  recordId,
  fetchStreetViewMeta,
  Icon,
  mutedTextClasses,
  streetHeaderClasses,
  streetPanelClasses,
  iconButtonClasses,
  style,
}) {
  const panoRef = useRef(null);
  const panoramaRef = useRef(null);
  const miniMapRef = useRef(null);
  const miniMapInstanceRef = useRef(null);
  const [svMeta, setSvMeta] = useState(null);
  const [svLoading, setSvLoading] = useState(false);
  const [svError, setSvError] = useState('');
  const [pov, setPov] = useState({ heading: 0, pitch: 0 });
  const [useJsApi, setUseJsApi] = useState(false);

  const loadGoogleMaps = window.FTTH_MAPS && window.FTTH_MAPS.loadGoogleMaps;

  useEffect(function () {
    if (!selectedPoint || !gmapsKey || showKeyInput) {
      setSvMeta(null);
      setSvError('');
      setUseJsApi(false);
      return undefined;
    }

    let active = true;
    setSvLoading(true);
    setSvError('');

    const metaPromise = fetchStreetViewMeta
      ? fetchStreetViewMeta(selectedPoint.lat, selectedPoint.lon, recordId)
      : Promise.resolve(null);

    metaPromise
      .then(function (meta) {
        if (!active) return null;
        setSvMeta(meta);
        if (!loadGoogleMaps) return Promise.reject(new Error('Maps loader unavailable'));
        return loadGoogleMaps(gmapsKey).then(function (maps) {
          return { maps: maps, meta: meta };
        });
      })
      .then(function (payload) {
        if (!active || !payload || !panoRef.current) return;
        setUseJsApi(true);

        const meta = payload.meta || {};
        const maps = payload.maps;
        const heading = meta.heading != null
          ? Number(meta.heading)
          : (meta.suggested_heading != null ? Number(meta.suggested_heading) : 0);
        const pitch = meta.pitch != null ? Number(meta.pitch) : 0;
        const position = {
          lat: meta.lat != null ? Number(meta.lat) : selectedPoint.lat,
          lng: meta.lon != null ? Number(meta.lon) : selectedPoint.lon,
        };
        setPov({ heading: Math.round(heading), pitch: Math.round(pitch) });

        if (panoramaRef.current) {
          panoramaRef.current.setPosition(position);
          panoramaRef.current.setPov({ heading: heading, pitch: pitch });
          return;
        }

        panoramaRef.current = new maps.StreetViewPanorama(panoRef.current, {
          position: position,
          pov: { heading: heading, pitch: pitch },
          zoom: 1,
          addressControl: true,
          linksControl: true,
          panControl: true,
          zoomControl: true,
          fullscreenControl: false,
          motionTracking: true,
          motionTrackingControl: true,
          showRoadLabels: true,
        });

        panoramaRef.current.addListener('position_changed', function () {
          const pos = panoramaRef.current.getPosition();
          if (pos && miniMapInstanceRef.current) {
            miniMapInstanceRef.current.setView([pos.lat(), pos.lng()], 18);
          }
        });

        panoramaRef.current.addListener('pov_changed', function () {
          const next = panoramaRef.current.getPov();
          setPov({ heading: Math.round(next.heading), pitch: Math.round(next.pitch) });
        });

        if (miniMapRef.current && !miniMapInstanceRef.current) {
          miniMapInstanceRef.current = L.map(miniMapRef.current, {
            zoomControl: false,
            attributionControl: false,
            dragging: false,
            scrollWheelZoom: false,
            doubleClickZoom: false,
          }).setView([position.lat, position.lng], 18);
          L.tileLayer('https://{s}.google.com/vt/lyrs=s,h&x={x}&y={y}&z={z}', {
            maxZoom: 20,
            subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
          }).addTo(miniMapInstanceRef.current);
          L.circleMarker([position.lat, position.lng], {
            radius: 6,
            color: '#fbbf24',
            fillColor: '#fbbf24',
            fillOpacity: 0.9,
            weight: 2,
          }).addTo(miniMapInstanceRef.current);
        }
      })
      .catch(function (err) {
        if (active) {
          setSvError(err.message || 'Street View failed to load');
          setUseJsApi(false);
          panoramaRef.current = null;
          if (panoRef.current) panoRef.current.innerHTML = '';
        }
      })
      .finally(function () {
        if (active) setSvLoading(false);
      });

    return function () {
      active = false;
    };
  }, [selectedPoint?.lat, selectedPoint?.lon, gmapsKey, showKeyInput, recordId]);

  useEffect(function () {
    return function () {
      if (miniMapInstanceRef.current) {
        miniMapInstanceRef.current.remove();
        miniMapInstanceRef.current = null;
      }
      panoramaRef.current = null;
    };
  }, []);

  useEffect(function () {
    if (panoramaRef.current && miniMapInstanceRef.current) {
      setTimeout(function () {
        if (miniMapInstanceRef.current) miniMapInstanceRef.current.invalidateSize();
      }, 200);
    }
  }, [svFullscreen, selectedPoint?.lat, selectedPoint?.lon]);

  const saveKey = useCallback(function () {
    const k = keyDraft.trim();
    localStorage.setItem('ftth_gmaps_key', k);
    localStorage.setItem('ftth_gmaps_key_custom', '1');
    setGmapsKey(k);
    setShowKeyInput(false);
  }, [keyDraft, setGmapsKey, setShowKeyInput]);

  const clearKey = useCallback(function () {
    localStorage.removeItem('ftth_gmaps_key');
    localStorage.removeItem('ftth_gmaps_key_custom');
    setGmapsKey('');
    setKeyDraft('');
    setShowKeyInput(false);
    window.dispatchEvent(new CustomEvent('ftth:maps-key-updated'));
  }, [setGmapsKey, setKeyDraft, setShowKeyInput]);

  const openInGoogleMaps = useCallback(function () {
    if (!selectedPoint) return;
    const q = selectedPoint.lat + ',' + selectedPoint.lon;
    window.open('https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=' + q, '_blank', 'noopener,noreferrer');
  }, [selectedPoint]);

  return (
    <div
      className={'street-view-panel ' + streetPanelClasses + (svFullscreen ? ' street-view-panel--fullscreen absolute inset-0 z-[2000] border-0' : '')}
      style={style}
    >
      <div className={streetHeaderClasses}>
        <span className="text-yellow-400 flex-shrink-0"><Icon name="map" size={17} /></span>
        <div className="flex-1 min-w-0">
          {selectedPoint ? (
            <>
              <div className={'text-xs font-semibold truncate ' + (isLightTheme ? 'text-slate-950' : 'text-white')}>
                {selectedPoint.name}
              </div>
              <div className="text-sm font-mono font-bold text-cyan-300">
                {selectedPoint.lat.toFixed(6)}, {selectedPoint.lon.toFixed(6)}
              </div>
            </>
          ) : (
            <div className={'text-xs font-semibold ' + mutedTextClasses}>
              Street View
              {gmapsKey ? (
                <span className="street-view-status street-view-status--live">Live</span>
              ) : (
                <span className="street-view-status street-view-status--setup">Setup needed</span>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button
            type="button"
            onClick={function () { setKeyDraft(gmapsKey); setShowKeyInput(function (s) { return !s; }); setSvFullscreen(false); }}
            title={gmapsKey ? 'Google Maps API key active — click to change' : 'Setup Google Maps API key'}
            className={iconButtonClasses + ' ' + (gmapsKey ? 'text-emerald-400' : 'text-amber-500')}
          >
            <Icon name="settings" size={14} />
          </button>
          {selectedPoint && gmapsKey && (
            <button type="button" onClick={openInGoogleMaps} title="Open in Google Maps" className={iconButtonClasses}>
              <Icon name="externalLink" size={14} />
            </button>
          )}
          <button type="button" onClick={function () { setSvFullscreen(function (f) { return !f; }); }} title={svFullscreen ? 'Exit fullscreen' : 'Fullscreen'} className={iconButtonClasses}>
            <Icon name={svFullscreen ? 'collapse' : 'expand'} size={14} />
          </button>
          {selectedPoint && (
            <button type="button" onClick={onClose} title="Close" className={iconButtonClasses + ' hover:text-red-500 font-bold text-base'}>&times;</button>
          )}
        </div>
      </div>

      <div className="street-view-body">
        {showKeyInput ? (
          <div className={'street-view-key-setup ' + (isLightTheme ? 'street-view-key-setup--light' : '')}>
            <div className="street-view-key-setup-head">
              <Icon name="map" size={36} />
              <p className="street-view-key-setup-title">Google Maps API Key</p>
              <p className="street-view-key-setup-desc">
                Enables <strong>interactive Street View</strong> with pan, rotate, zoom, compass, and pegman navigation.
              </p>
            </div>
            <div className="street-view-key-setup-steps">
              <strong>Enable in Google Cloud Console:</strong>
              <ol>
                <li>Enable <strong>Maps JavaScript API</strong></li>
                <li>Enable <strong>Street View Static API</strong> (metadata)</li>
                <li>Enable billing and allow this app origin in HTTP referrer restrictions</li>
              </ol>
            </div>
            <input
              type="text"
              value={keyDraft}
              onChange={function (e) { setKeyDraft(e.target.value); }}
              placeholder="AIzaSy..."
              className="street-view-key-input"
            />
            <div className="street-view-key-actions">
              <button type="button" onClick={saveKey} className="street-view-key-save">
                <Icon name="check" size={13} /> Save &amp; Enable Street View
              </button>
              {gmapsKey && (
                <button type="button" onClick={clearKey} className="street-view-key-clear">
                  <Icon name="trash" size={13} /> Clear
                </button>
              )}
            </div>
            <button type="button" onClick={function () { setShowKeyInput(false); }} className="street-view-key-cancel">Cancel</button>
          </div>
        ) : selectedPoint ? (
          gmapsKey ? (
            <div className="street-view-stage">
              <div ref={panoRef} className="street-view-pano" />
              {svLoading && (
                <div className="street-view-overlay street-view-overlay--loading">
                  <span className="street-view-spinner" /> Loading Street View…
                </div>
              )}
              {svError && !useJsApi && (
                <div className="street-view-overlay street-view-overlay--error">
                  <strong>Street View unavailable</strong>
                  <p>{svError}</p>
                  <button type="button" onClick={openInGoogleMaps} className="street-view-fallback-btn">Open in Google Maps</button>
                </div>
              )}
              {useJsApi && (
                <>
                  <div ref={miniMapRef} className="street-view-minimap" title="Street View location" />
                  <div className="street-view-hud">
                    <div className="street-view-compass" style={{ transform: 'rotate(' + (-pov.heading) + 'deg)' }}>
                      <span className="street-view-compass-n">N</span>
                    </div>
                    <div className="street-view-pov tabular-nums">
                      <span>H {pov.heading}°</span>
                      <span>P {pov.pitch}°</span>
                      {svMeta && svMeta.elevation_m != null && <span>↑ {Math.round(svMeta.elevation_m)} m</span>}
                      {svMeta && svMeta.date && <span>{svMeta.date}</span>}
                    </div>
                  </div>
                </>
              )}
            </div>
          ) : (
            <StreetViewFallback
              selectedPoint={selectedPoint}
              isLightTheme={isLightTheme}
              onSetup={function () { setKeyDraft(''); setShowKeyInput(true); }}
            />
          )
        ) : (
          <div className={'street-view-empty ' + (isLightTheme ? 'street-view-empty--light' : '')}>
            <span className="street-view-empty-icon"><Icon name="map" size={48} /></span>
            <p className="street-view-empty-title">Street View Explorer</p>
            <p className="street-view-empty-desc">Click any map point or use the pegman tool to explore</p>
            {!gmapsKey && (
              <button type="button" onClick={function () { setKeyDraft(''); setShowKeyInput(true); }} className="street-view-fallback-btn">
                Setup Google Street View
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function StreetViewFallback({ selectedPoint, isLightTheme, onSetup }) {
  const lat = selectedPoint.lat;
  const lon = selectedPoint.lon;
  const pad = 0.003;
  const esriUrl = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?bbox='
    + (lon - pad) + ',' + (lat - pad) + ',' + (lon + pad) + ',' + (lat + pad)
    + '&bboxSR=4326&imageSR=4326&size=700,500&format=png&f=image';

  return (
    <div className="street-view-fallback">
      <div className="street-view-fallback-img-wrap">
        <img src={esriUrl} alt="Satellite preview" className="street-view-fallback-img" />
        <div className="street-view-fallback-crosshair" />
        <div className="street-view-fallback-coords">{lat.toFixed(5)}, {lon.toFixed(5)}</div>
      </div>
      <div className={'street-view-fallback-foot ' + (isLightTheme ? 'street-view-fallback-foot--light' : '')}>
        <div className="street-view-fallback-alert">
          <strong>Street View not active.</strong> Add your Google Maps API key for live interactive panoramas with compass and navigation.
        </div>
        <button type="button" onClick={onSetup} className="street-view-fallback-btn">Setup Google Street View</button>
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.StreetViewPanel = StreetViewPanel;
