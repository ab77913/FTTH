# Agent 0 10-House Discovery KMZ Fixture

Upload `agent0_10_house_polygon.kmz` and run a flow with Agent 0 enabled.

Recommended Agent 0 settings:

```text
enabled: true
reverse_geocode: true
grid_step: 0.00025
max_candidates_per_polygon: 250
max_candidates_per_job: 250
dedup_distance_m: 20
multi_unit_suffixing: false
```

This fixture intentionally contains only one polygon and no household address points inside it.
Agent 0 should create new addresses from Google reverse geocoding.

Smoke-tested addresses returned by the Agent 0 reverse-geocode path include:

```text
553A Elizabeth St, San Francisco, CA 94114, USA
541 Elizabeth St, San Francisco, CA 94114, USA
527 Elizabeth St, San Francisco, CA 94114, USA
511 Elizabeth St, San Francisco, CA 94114, USA
1060 Noe St, San Francisco, CA 94114, USA
1065 Noe St, San Francisco, CA 94114, USA
477-479 Elizabeth St, San Francisco, CA 94114, USA
465 Elizabeth St, San Francisco, CA 94114, USA
560 Elizabeth St, San Francisco, CA 94114, USA
546 Elizabeth St, San Francisco, CA 94114, USA
```

