/** @jsx React.createElement */

function MapSearchPanel({
  totalWithCoords,
  searchInputRef,
  addressSearch,
  setAddressSearch,
  setShowAddressMatches,
  showAddressMatches,
  addressMatches,
  selectedFeatureId,
  primaryAddressMatch,
  selectFeature,
  featureCoord,
  featureLabel,
  coordinateMode,
  panelClasses,
  isLightTheme,
  searchResultBaseClasses,
  searchResultSelectedClasses,
}) {
  if (totalWithCoords <= 0) return null;

  return (
    <div className="map-search-float">
      <input
        ref={searchInputRef}
        value={addressSearch}
        onChange={function (e) { setAddressSearch(e.target.value); setShowAddressMatches(true); }}
        onFocus={function () { setShowAddressMatches(true); }}
        onKeyDown={function (e) {
          if (e.key === 'Enter' && primaryAddressMatch) selectFeature(primaryAddressMatch);
          if (e.key === 'Escape') setShowAddressMatches(false);
        }}
        placeholder={coordinateMode === 'raw' ? 'Search stored address...' : 'Search final updated address...'}
        aria-label="Search addresses on map"
        className={'w-full backdrop-blur rounded-lg px-3 py-2.5 text-xs outline-none focus:border-cyan-400 shadow-lg ' + (isLightTheme ? 'bg-white/95 border border-slate-300 text-slate-900 placeholder-slate-500' : 'bg-dark-900/95 border border-dark-600 text-white placeholder-gray-500')}
      />
      {showAddressMatches && addressSearch.trim() && (
        <div className={'mt-2 max-h-56 overflow-y-auto space-y-1 rounded-lg p-2 shadow-xl ' + panelClasses}>
          {addressMatches.length === 0 ? (
            <div className="text-xs text-gray-500 py-2">No matching address found</div>
          ) : addressMatches.map(function (feat) {
            const isSelected = selectedFeatureId === feat.id;
            const coord = featureCoord(feat);
            if (!coord) return null;
            return (
              <button
                key={feat.id}
                type="button"
                onClick={function () { selectFeature(feat); }}
                className={'w-full text-left rounded border px-2 py-1.5 transition ' + (isSelected ? searchResultSelectedClasses : searchResultBaseClasses)}
              >
                <div className={'text-xs font-semibold truncate ' + (isSelected ? (isLightTheme ? 'text-slate-950' : 'text-yellow-200') : (isLightTheme ? 'text-slate-900' : 'text-gray-100'))}>
                  {featureLabel(feat)}
                </div>
                <div className="text-sm text-cyan-300 font-mono font-bold">
                  {coord.lat.toFixed(6)}, {coord.lon.toFixed(6)}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

window.FTTH_APP = window.FTTH_APP || {};
window.FTTH_APP.MapSearchPanel = MapSearchPanel;