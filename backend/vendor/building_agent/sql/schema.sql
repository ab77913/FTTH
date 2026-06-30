CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS buildings (
    id BIGSERIAL PRIMARY KEY,
    geometry geometry(Polygon, 4326) NOT NULL,
    area_m2 double precision NOT NULL,
    source text NOT NULL CHECK (source IN ('microsoft', 'google')),
    centroid geometry(Point, 4326) NOT NULL,
    h3_index text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS buildings_geometry_gix
    ON buildings
    USING gist (geometry);

CREATE INDEX IF NOT EXISTS buildings_h3_index_idx
    ON buildings (h3_index)
    WHERE h3_index IS NOT NULL;

CREATE INDEX IF NOT EXISTS buildings_source_idx
    ON buildings (source);
