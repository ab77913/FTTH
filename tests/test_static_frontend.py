"""Frontend static asset contract tests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"
INDEX = ROOT / "frontend" / "public" / "index.html"

REQUIRED_ASSETS = (
    "api/theme.js",
    "api/http-client.js",
    "api/auth.js",
    "api/data-api.js",
    "config/pipeline.js",
    "components/ui.jsx",
    "components/map-layers-panel.jsx",
    "components/map-search-panel.jsx",
    "components/map-selected-panel.jsx",
    "pages/map-page.jsx",
    "app/globals.jsx",
    "app/app.jsx",
    "app/bootstrap.jsx",
)


def test_required_frontend_modules_exist():
    for rel in REQUIRED_ASSETS:
        assert (FRONTEND_SRC / rel).is_file(), f"missing frontend/src/{rel}"


def test_index_html_loads_frontend_modules():
    html = INDEX.read_text(encoding="utf-8")
    for rel in REQUIRED_ASSETS:
        assert f"/assets/{rel}" in html, f"index.html must load {rel}"


def test_index_html_uses_ftth_namespace():
    html = INDEX.read_text(encoding="utf-8")
    assert "window.FTTH" in html or "/assets/api/auth.js" in html
    assert "ui.jsx" in html

def test_map_styles_are_split_from_global_styles():
    app_css = (FRONTEND_SRC / "styles" / "app.css").read_text(encoding="utf-8")
    map_css = (FRONTEND_SRC / "styles" / "map.css").read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    assert "/assets/styles/map.css" in html
    assert ".map-search-float" not in app_css
    assert ".map-search-float" in map_css
    assert ".map-selected-panel" in map_css


def test_map_page_loads_after_its_components():
    html = INDEX.read_text(encoding="utf-8")
    order = [
        "/assets/components/map-layers-panel.jsx",
        "/assets/components/map-search-panel.jsx",
        "/assets/components/map-selected-panel.jsx",
        "/assets/pages/map-page.jsx",
    ]
    positions = [html.index(item) for item in order]
    assert positions == sorted(positions)


def test_records_table_uses_valid_google_maps_query_urls():
    records_table = (FRONTEND_SRC / "components" / "records-table.jsx").read_text(encoding="utf-8")
    assert "maps-q" not in records_table
    assert "https://www.google.com/maps?q=" in records_table
    assert "googleMapsCoordUrl" in records_table


def test_map_page_prefers_backend_maps_key_over_local_override():
    map_page = (FRONTEND_SRC / "pages" / "map-page.jsx").read_text(encoding="utf-8")
    assert "fetchMapsKey()" in map_page
    assert "localStorage.removeItem('ftth_gmaps_key_custom')" in map_page
    assert "if (localStorage.getItem('ftth_gmaps_key_custom') === '1')" not in map_page


def test_google_maps_loader_reports_auth_failure():
    loader = (FRONTEND_SRC / "lib" / "gmaps-loader.js").read_text(encoding="utf-8")
    assert "gm_authFailure" in loader
    assert "Google Maps rejected this API key" in loader

