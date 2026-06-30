/** @jsx React.createElement */

function MapLayersPanel({
  isOpen,
  onToggle,
  baseMaps,
  activeBasemap,
  onBasemapChange,
  overlays,
  overlayVisibility,
  onOverlayToggle,
  isLightTheme,
  panelClasses,
  buttonClasses,
  Icon,
}) {
  if (!isOpen) {
    return (
      <button type="button" className="map-layers-fab" onClick={onToggle} title="Layers">
        <Icon name="layers" size={16} />
        <span>Layers</span>
      </button>
    );
  }

  return (
    <div className={'map-layers-panel ' + panelClasses}>
      <div className="map-layers-panel-head">
        <div className="map-layers-panel-title">
          <Icon name="layers" size={15} />
          <span>Map Layers</span>
        </div>
        <button type="button" onClick={onToggle} className={'map-layers-close ' + buttonClasses} title="Close layers">
          <Icon name="x" size={14} />
        </button>
      </div>

      <div className="map-layers-section">
        <div className="map-layers-section-label">Basemap</div>
        <div className="map-layers-grid">
          {Object.keys(baseMaps || {}).map(function (name) {
            const active = activeBasemap === name;
            return (
              <button
                key={name}
                type="button"
                className={'map-layers-basemap ' + (active ? 'map-layers-basemap--active' : '')}
                onClick={function () { onBasemapChange(name); }}
              >
                <span className={'map-layers-basemap-thumb map-layers-basemap-thumb--' + name.toLowerCase().replace(/\s+/g, '-')} />
                <span className="map-layers-basemap-name">{name}</span>
                {active && <Icon name="check" size={12} />}
              </button>
            );
          })}
        </div>
      </div>

      <div className="map-layers-section">
        <div className="map-layers-section-label">Overlays</div>
        <div className="map-layers-overlays">
          {Object.entries(overlays || {}).map(function (entry) {
            const key = entry[0];
            const visible = overlayVisibility[key] !== false;
            return (
              <label key={key} className="map-layers-overlay-row">
                <input
                  type="checkbox"
                  checked={visible}
                  onChange={function () { onOverlayToggle(key, !visible); }}
                />
                <span>{key}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className={'map-layers-foot ' + (isLightTheme ? 'text-slate-500' : 'text-gray-500')}>
        Tip: use measure tools (top-left) for distance &amp; area
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.MapLayersPanel = MapLayersPanel;
