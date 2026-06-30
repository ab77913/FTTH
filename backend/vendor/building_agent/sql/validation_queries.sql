-- Count total buildings
SELECT count(*) AS total_buildings
FROM buildings;

-- Count buildings by provider
SELECT source, count(*) AS building_count
FROM buildings
GROUP BY source
ORDER BY source;

-- Find buildings within 15 meters of a latitude/longitude.
-- Replace :lon and :lat with numeric values for psql, application code, or your SQL client.
SELECT
    id,
    source,
    area_m2,
    ST_Distance(
        geometry::geography,
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
    ) AS distance_m
FROM buildings
WHERE ST_DWithin(
    geometry::geography,
    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
    15
)
ORDER BY distance_m
LIMIT 10;
