/** @jsx React.createElement */

function MapSelectedPanel({
  selectedFeature,
  selectedFeatureCoord,
  selectedFeatureRule,
  svFullscreen,
  panelClasses,
  buttonClasses,
  featureLabel,
  coordLabel,
  hasCoordChange,
  coordDeltaMeters,
  recenterSelectedFeature,
  onClose,
  Icon,
}) {
  if (!selectedFeature || !selectedFeatureCoord || svFullscreen) return null;

  const city = selectedFeature.city || selectedFeature.raw_city || selectedFeature.validated_city;
  const state = selectedFeature.state || selectedFeature.raw_state || selectedFeature.validated_state;
  const zip = selectedFeature.zip_code || selectedFeature.zip || selectedFeature.postal_code || selectedFeature.validated_postcode;
  const hasLocation = city || state || zip;

  return (
    <div className={'map-selected-panel ' + panelClasses}>
      <div className="map-selected-accent" style={{ background: (selectedFeatureRule && selectedFeatureRule.color) || '#0891b2' }} />
      <div className="map-selected-head">
        <div className="map-selected-title-wrap">
          <span className="map-selected-icon"><Icon name="pin" size={15} /></span>
          <div className="min-w-0">
            <div className="map-selected-eyebrow">Selected address</div>
            <div className="map-selected-title">{featureLabel(selectedFeature)}</div>
          </div>
        </div>
        <button type="button" onClick={onClose} className={'map-selected-close ' + buttonClasses} title="Close selected address">
          <Icon name="x" size={13} />
        </button>
      </div>
      <div className="map-selected-body">
        <div className="map-selected-coord-row">
          <span>{coordLabel()}</span>
          <strong>{selectedFeatureCoord.lat.toFixed(6)}, {selectedFeatureCoord.lon.toFixed(6)}</strong>
        </div>
        {hasLocation && (
          <div className="map-selected-meta-grid">
            {city && <div><span>City</span><strong>{city}</strong></div>}
            {state && <div><span>State</span><strong>{state}</strong></div>}
            {zip && <div><span>ZIP</span><strong>{zip}</strong></div>}
          </div>
        )}
      </div>
      <div className="map-selected-actions">
        <button type="button" onClick={function () { recenterSelectedFeature(20); }} className={'map-selected-action ' + buttonClasses}>
          <Icon name="globe" size={14} /> Focus
        </button>
        <button type="button" onClick={function () { recenterSelectedFeature(18); }} className="map-selected-action map-selected-action-primary">
          <Icon name="map" size={14} /> Street View
        </button>
      </div>
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.MapSelectedPanel = MapSelectedPanel;