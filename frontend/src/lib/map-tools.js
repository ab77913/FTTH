/**
 * Leaflet map utilities — basemaps, compass, measure, coordinate HUD.
 */
(function (global) {
  function createBasemaps() {
    const googleStreets = L.tileLayer('https://{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', {
      maxZoom: 20,
      subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
      attribution: '&copy; Google',
    });
    const googleSatellite = L.tileLayer('https://{s}.google.com/vt/lyrs=s,h&x={x}&y={y}&z={z}', {
      maxZoom: 20,
      subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
      attribution: '&copy; Google',
    });
    const googleTerrain = L.tileLayer('https://{s}.google.com/vt/lyrs=p&x={x}&y={y}&z={z}', {
      maxZoom: 20,
      subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
      attribution: '&copy; Google',
    });
    const esriSatellite = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { attribution: '&copy; Esri, Maxar', maxZoom: 19 }
    );
    const esriTopo = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
      { attribution: '&copy; Esri', maxZoom: 19 }
    );
    const esriHybrid = L.layerGroup([
      L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        { maxZoom: 19 }
      ),
      L.tileLayer('https://stamen-tiles.a.ssl.fastly.net/toner-labels/{z}/{x}/{y}.png', {
        maxZoom: 19,
        opacity: 0.75,
      }),
    ]);
    const openTopo = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
      maxZoom: 17,
      attribution: '&copy; OpenTopoMap',
    });
    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap',
      maxZoom: 19,
    });
    const cartoDark = L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      { attribution: '&copy; CARTO', maxZoom: 20 }
    );
    const cartoLight = L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      { attribution: '&copy; CARTO', maxZoom: 20 }
    );

    return {
      defaultKey: 'Google Streets',
      baseMaps: {
        'Google Streets': googleStreets,
        'Google Satellite': googleSatellite,
        'Google Terrain': googleTerrain,
        'Esri Satellite': esriSatellite,
        'Esri Topo': esriTopo,
        'Esri Hybrid': esriHybrid,
        'OpenTopoMap': openTopo,
        'OpenStreetMap': osm,
        'Carto Dark': cartoDark,
        'Carto Light': cartoLight,
      },
    };
  }

  function formatCoord(lat, lng) {
    const latH = lat >= 0 ? 'N' : 'S';
    const lngH = lng >= 0 ? 'E' : 'W';
    return Math.abs(lat).toFixed(5) + '° ' + latH + ', ' + Math.abs(lng).toFixed(5) + '° ' + lngH;
  }

  function haversineMeters(a, b) {
    const R = 6371000;
    const dLat = (b.lat - a.lat) * Math.PI / 180;
    const dLng = (b.lng - a.lng) * Math.PI / 180;
    const lat1 = a.lat * Math.PI / 180;
    const lat2 = b.lat * Math.PI / 180;
    const h = Math.sin(dLat / 2) ** 2
      + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  function formatDistance(meters) {
    if (meters < 1000) return meters.toFixed(1) + ' m';
    return (meters / 1000).toFixed(2) + ' km';
  }

  function polygonAreaSqMeters(latlngs) {
    if (latlngs.length < 3) return 0;
    const R = 6378137;
    let area = 0;
    for (let i = 0; i < latlngs.length; i++) {
      const p1 = latlngs[i];
      const p2 = latlngs[(i + 1) % latlngs.length];
      area += (p2.lng - p1.lng) * Math.PI / 180
        * (2 + Math.sin(p1.lat * Math.PI / 180) + Math.sin(p2.lat * Math.PI / 180));
    }
    return Math.abs(area * R * R / 2);
  }

  function formatArea(sqMeters) {
    if (sqMeters < 10000) return sqMeters.toFixed(0) + ' m²';
    return (sqMeters / 10000).toFixed(2) + ' ha';
  }

  function createCompassControl(map) {
    const Compass = L.Control.extend({
      options: { position: 'bottomright' },
      onAdd: function () {
        const wrap = L.DomUtil.create('div', 'leaflet-bar map-compass-control');
        const btn = L.DomUtil.create('button', 'map-compass-btn', wrap);
        btn.type = 'button';
        btn.title = 'Reset north-up view';
        btn.innerHTML = '<span class="map-compass-needle" aria-hidden="true">N</span>';
        btn.setAttribute('aria-label', 'Reset map to north-up');
        L.DomEvent.disableClickPropagation(wrap);
        L.DomEvent.on(btn, 'click', function (e) {
          L.DomEvent.preventDefault(e);
          map.setView(map.getCenter(), map.getZoom(), { animate: true });
        });
        return wrap;
      },
    });
    return new Compass();
  }

  function createCoordHud(map, onUpdate) {
    const Hud = L.Control.extend({
      options: { position: 'bottomleft' },
      onAdd: function () {
        const el = L.DomUtil.create('div', 'map-coord-hud');
        el.innerHTML = '<span class="map-coord-hud-label">Cursor</span><span class="map-coord-hud-value">—</span>';
        return el;
      },
    });
    const hud = new Hud();
    map.addControl(hud);
    const valueEl = hud.getContainer().querySelector('.map-coord-hud-value');

    map.on('mousemove', function (e) {
      const text = formatCoord(e.latlng.lat, e.latlng.lng);
      valueEl.textContent = text;
      if (onUpdate) onUpdate({ lat: e.latlng.lat, lng: e.latlng.lng, text: text });
    });
    map.on('mouseout', function () {
      valueEl.textContent = '—';
    });

    return hud;
  }

  function createMeasureControl(map, onMeasureChange) {
    let active = false;
    let mode = 'line';
    let points = [];
    let layerGroup = L.layerGroup().addTo(map);
    let tempLine = null;

    function clearMeasure() {
      points = [];
      layerGroup.clearLayers();
      tempLine = null;
      if (onMeasureChange) onMeasureChange(null);
    }

    function finishMeasure() {
      if (points.length < 2) return;
      let result = { mode: mode };
      if (mode === 'line') {
        let total = 0;
        for (let i = 1; i < points.length; i++) {
          total += haversineMeters(points[i - 1], points[i]);
        }
        result.distance_m = total;
        result.label = formatDistance(total);
      } else if (mode === 'area' && points.length >= 3) {
        const area = polygonAreaSqMeters(points);
        result.area_sq_m = area;
        result.label = formatArea(area);
        L.polygon(points, {
          color: '#0ea5e9',
          weight: 2,
          fillColor: '#0ea5e9',
          fillOpacity: 0.15,
        }).addTo(layerGroup);
      }
      if (onMeasureChange) onMeasureChange(result);
      active = false;
      map.getContainer().style.cursor = '';
    }

    function clickHandler(e) {
      if (!active) return;
      points.push(e.latlng);
      L.circleMarker(e.latlng, {
        radius: 4,
        color: '#0ea5e9',
        fillColor: '#fff',
        fillOpacity: 1,
        weight: 2,
      }).addTo(layerGroup);

      if (points.length >= 2) {
        if (tempLine) layerGroup.removeLayer(tempLine);
        tempLine = L.polyline(points, { color: '#0ea5e9', weight: 3, dashArray: '6 4' }).addTo(layerGroup);
      }
      if (mode === 'line' && points.length >= 2) {
        let total = 0;
        for (let i = 1; i < points.length; i++) {
          total += haversineMeters(points[i - 1], points[i]);
        }
        if (onMeasureChange) onMeasureChange({ mode: 'line', distance_m: total, label: formatDistance(total), live: true });
      }
    }

    function dblClickHandler(e) {
      if (!active) return;
      L.DomEvent.preventDefault(e);
      finishMeasure();
    }

    map.on('click', clickHandler);
    map.on('dblclick', dblClickHandler);

    const Measure = L.Control.extend({
      options: { position: 'topleft' },
      onAdd: function () {
        const wrap = L.DomUtil.create('div', 'leaflet-bar map-measure-control');
        const lineBtn = L.DomUtil.create('button', 'map-measure-btn', wrap);
        lineBtn.type = 'button';
        lineBtn.title = 'Measure distance (double-click to finish)';
        lineBtn.innerHTML = '↔';
        const areaBtn = L.DomUtil.create('button', 'map-measure-btn', wrap);
        areaBtn.type = 'button';
        areaBtn.title = 'Measure area (double-click to finish)';
        areaBtn.innerHTML = '⬠';
        const clearBtn = L.DomUtil.create('button', 'map-measure-btn map-measure-btn--clear', wrap);
        clearBtn.type = 'button';
        clearBtn.title = 'Clear measurement';
        clearBtn.innerHTML = '×';

        L.DomEvent.disableClickPropagation(wrap);

        function activate(nextMode, btn) {
          if (active && mode === nextMode) {
            active = false;
            map.getContainer().style.cursor = '';
            wrap.querySelectorAll('.map-measure-btn').forEach(function (b) { b.classList.remove('active'); });
            return;
          }
          clearMeasure();
          active = true;
          mode = nextMode;
          map.getContainer().style.cursor = 'crosshair';
          wrap.querySelectorAll('.map-measure-btn').forEach(function (b) { b.classList.remove('active'); });
          btn.classList.add('active');
        }

        L.DomEvent.on(lineBtn, 'click', function (e) {
          L.DomEvent.preventDefault(e);
          activate('line', lineBtn);
        });
        L.DomEvent.on(areaBtn, 'click', function (e) {
          L.DomEvent.preventDefault(e);
          activate('area', areaBtn);
        });
        L.DomEvent.on(clearBtn, 'click', function (e) {
          L.DomEvent.preventDefault(e);
          active = false;
          map.getContainer().style.cursor = '';
          wrap.querySelectorAll('.map-measure-btn').forEach(function (b) { b.classList.remove('active'); });
          clearMeasure();
        });

        return wrap;
      },
    });

    const control = new Measure();
    control.destroy = function () {
      map.off('click', clickHandler);
      map.off('dblclick', dblClickHandler);
      map.removeLayer(layerGroup);
    };
    return control;
  }

  function attachMapTools(map, options) {
    options = options || {};
    const basemaps = createBasemaps();
    const defaultLayer = basemaps.baseMaps[options.defaultBasemap || basemaps.defaultKey];
    if (defaultLayer && !map.hasLayer(defaultLayer)) {
      defaultLayer.addTo(map);
    }
    const compass = createCompassControl(map);
    map.addControl(compass);
    const measure = createMeasureControl(map, options.onMeasureChange);
    map.addControl(measure);
    const coordHud = createCoordHud(map, options.onCoordUpdate);
    return {
      basemaps: basemaps.baseMaps,
      defaultBasemap: options.defaultBasemap || basemaps.defaultKey,
      compass: compass,
      measure: measure,
      coordHud: coordHud,
      formatCoord: formatCoord,
      formatDistance: formatDistance,
      formatArea: formatArea,
    };
  }

  global.FTTH_MAP_TOOLS = {
    createBasemaps: createBasemaps,
    attachMapTools: attachMapTools,
    createCompassControl: createCompassControl,
    createMeasureControl: createMeasureControl,
    createCoordHud: createCoordHud,
    formatCoord: formatCoord,
    formatDistance: formatDistance,
    formatArea: formatArea,
  };
})(window);
