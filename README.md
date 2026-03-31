# Immich Auto-Tagger

A lightweight Dockerised background service that automatically applies
hierarchical tags to every asset in your [Immich](https://immich.app) library.

## Tags produced

| Strategy   | Example tag                                       |
|------------|---------------------------------------------------|
| Date       | `date/2024`, `date/2024/03`, `date/2024/03/15`    |
| Location   | `location/Europe/United Kingdom/Warwickshire/Warwick` |
| People     | `people/Jeff`, `people/Sarah`                     |

---

## Quick start

### 1. Clone / copy the `auto-tagger/` folder

```
your-immich-stack/
├── docker-compose.yml          ← your existing Immich compose
└── auto-tagger/
    ├── main.py
    ├── immich_api.py
    ├── geotag.py
    ├── tagger.py
    ├── scheduler.py
    ├── facemap.yaml            ← edit this with your face mappings
    ├── requirements.txt
    └── Dockerfile
```

### 2. Get your Immich API key

1. Open Immich → **Account Settings** → **API Keys** → **New API Key**
2. Copy the key.

### 3. Create `.env` in your stack root

```env
IMMICH_API_KEY=your_api_key_here
```

### 4. Merge the compose snippet

If you already have Immich running, copy the `immich-auto-tagger` service
block from `auto-tagger/docker-compose.yml` into your existing
`docker-compose.yml`.

Or use the provided complete `docker-compose.yml` in this folder.

### 5. Map people to names (optional)

Edit `auto-tagger/facemap.yaml`:

```yaml
# Get person IDs from the Immich UI: Explore → People → click a person → copy UUID from URL
3f2c8a1b-4d5e-6f7a-8b9c-0d1e2f3a4b5c: Jeff
a1b2c3d4-e5f6-7890-abcd-ef1234567890: Sarah
```

Changes to `facemap.yaml` take effect on the **next scheduled run** without
rebuilding the container (the file is bind-mounted).

### 6. Start the service

```bash
docker compose up -d immich-auto-tagger
```

Watch logs:

```bash
docker compose logs -f immich-auto-tagger
```

---

## Environment variables

| Variable                  | Default                                        | Description                                           |
|---------------------------|------------------------------------------------|-------------------------------------------------------|
| `IMMICH_URL`              | *(required)*                                   | Base URL of your Immich server                        |
| `IMMICH_API_KEY`          | *(required)*                                   | Immich API key                                        |
| `SCAN_INTERVAL_MINUTES`   | `15`                                           | How often to poll for new/updated assets              |
| `SCAN_PAGE_SIZE`          | `100`                                          | Assets per API page                                   |
| `INITIAL_SCAN_DAYS`       | `0`                                            | On first run, only scan assets updated in last N days (0 = all) |
| `NOMINATIM_USER_AGENT`    | `immich-auto-tagger/1.0 (self-hosted)`         | Sent with every Nominatim request                     |
| `NOMINATIM_URL`           | `https://nominatim.openstreetmap.org`          | Override with self-hosted Nominatim                   |
| `FACEMAP_PATH`            | `/app/facemap.yaml`                            | Path to the face-name mapping file inside container   |
| `STATE_FILE`              | `/app/state/last_run.json`                     | Persists last-run timestamp across restarts           |
| `IMMICH_TIMEOUT`          | `30`                                           | HTTP timeout (seconds) for Immich API calls           |

---

## Self-hosted Nominatim (recommended for large libraries)

If you have thousands of geotagged photos, the public Nominatim service may
rate-limit you.  Run your own:

```yaml
# add to docker-compose.yml
nominatim:
  image: mediagis/nominatim:4.4
  environment:
    PBF_URL: https://download.geofabrik.de/europe/great-britain-latest.osm.pbf
    REPLICATION_URL: https://planet.openstreetmap.org/replication/hour/
  volumes:
    - nominatim-data:/var/lib/postgresql/14/main
  networks:
    - immich-net
```

Then set:

```yaml
NOMINATIM_URL: "http://nominatim:8080"
```

---

## Adding custom tag strategies

1. Open `tagger.py`.
2. Implement a class with a `tags_for_asset(self, asset: dict) -> list[str]` method.
3. Add an instance to the `AssetTagger.__init__` strategy list.

```python
class SeasonTagStrategy:
    """Tags photos with the meteorological season."""

    _SEASONS = {
        (12, 1, 2): "Winter",
        (3, 4, 5): "Spring",
        (6, 7, 8): "Summer",
        (9, 10, 11): "Autumn",
    }

    def tags_for_asset(self, asset: dict) -> list[str]:
        raw = (asset.get("exifInfo") or {}).get("dateTimeOriginal")
        if not raw:
            return []
        month = datetime.fromisoformat(raw.replace("Z", "+00:00")).month
        for months, season in self._SEASONS.items():
            if month in months:
                return [f"season/{season}"]
        return []
```

---

## Architecture

```
main.py
  └─ scheduler.py          APScheduler job, runs every N minutes
       └─ tagger.py        AssetTagger — runs all strategies
            ├─ DateTagStrategy
            ├─ LocationTagStrategy
            │    └─ geotag.py  Nominatim reverse-geocoding + LRU cache
            └─ FaceTagStrategy
                 └─ facemap.yaml
       └─ immich_api.py    HTTP client for Immich REST API
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `KeyError: 'IMMICH_URL'` | Set the env variable in docker-compose or `.env` |
| Tags not appearing | Check that the API key has **write** permissions |
| Geocoding always empty | Ensure the container can reach `nominatim.openstreetmap.org` or your self-hosted instance |
| Face tags missing | Verify UUIDs in `facemap.yaml` match Immich person IDs |
| Service keeps re-scanning all assets | The `tagger-state` volume is not persisted — check volume mounts |
