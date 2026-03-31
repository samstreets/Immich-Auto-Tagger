# Immich Auto-Tagger

A lightweight Dockerised background service that automatically applies
hierarchical tags to every asset in your [Immich](https://immich.app) library.

[![Docker Hub](https://img.shields.io/docker/v/samuelstreets/immich-auto-tagger?label=Docker%20Hub&logo=docker)](https://hub.docker.com/r/samuelstreets/immich-auto-tagger)
[![Build & Push](https://github.com/samuelstreets/immich-auto-tagger/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/samuelstreets/immich-auto-tagger/actions/workflows/docker-publish.yml)

## Tags produced

| Strategy    | Example tag                                            |
|-------------|--------------------------------------------------------|
| Date        | `date/2024`, `date/2024/03`, `date/2024/03/15`         |
| Location    | `location/United Kingdom/England/Warwick`              |
| People      | `people/Jeff`, `people/Sarah`                          |
| Camera      | `camera/Sony/ILCE-7M4`, `camera/Apple/iPhone 15 Pro`   |
| Season      | `season/Autumn`, `season/Summer`                       |
| Day         | `day/Saturday`, `day/weekend`                          |
| Media type  | `type/photo`, `type/video`, `type/live-photo`          |
| File format | `format/JPEG`, `format/ARW`, `format/RAW`              |
| Objects     | `object/dog`, `object/aeroplane`, `object/sunset`      |

---

## Quick start

### 1. Create `.env` in your stack root

```env
IMMICH_API_KEY=your_api_key_here

# Optional — enables AI object/scene tagging
# ANTHROPIC_API_KEY=your_anthropic_key_here
```

### 2. Get your Immich API key

1. Open Immich → **Account Settings** → **API Keys** → **New API Key**
2. Copy the key into `.env`.

### 3. Add the service to your compose file

Copy the `immich-auto-tagger` service block below into your existing
`docker-compose.yml`, or use this file standalone.

The image is pulled automatically from Docker Hub — no build step required.

### 4. Start the service

```bash
docker compose up -d immich-auto-tagger
```

Watch logs:

```bash
docker compose logs -f immich-auto-tagger
```

---

## Environment variables

| Variable                  | Default                | Description                                                    |
|---------------------------|------------------------|----------------------------------------------------------------|
| `IMMICH_URL`              | *(required)*           | Base URL of your Immich server                                 |
| `IMMICH_API_KEY`          | *(required)*           | Immich API key                                                 |
| `SCAN_INTERVAL_MINUTES`   | `15`                   | How often to poll for new/updated assets                       |
| `SCAN_PAGE_SIZE`          | `100`                  | Assets per API page                                            |
| `INITIAL_SCAN_DAYS`       | `0`                    | On first run, only scan assets updated in last N days (0 = all)|
| `STATE_FILE`              | `/app/state/last_run.json` | Persists last-run timestamp across restarts               |
| `IMMICH_TIMEOUT`          | `30`                   | HTTP timeout (seconds) for Immich API calls                    |
| `ANTHROPIC_API_KEY`       | *(unset)*              | Enables AI object tagging — omit to disable                    |
| `OBJECT_TAG_MAX_LABELS`   | `5`                    | Max object/scene labels produced per photo                     |

---

## Object tagging (AI-powered)

When `ANTHROPIC_API_KEY` is set the service sends each photo's preview
thumbnail to the Claude Vision API and tags it with the subjects it detects
(e.g. `object/dog`, `object/beach`, `object/bicycle`).

No extra packages are required — the integration uses plain HTTP via the
`requests` library that is already installed.

To enable, add to your `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

And uncomment the line in `docker-compose.yml`:

```yaml
ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
```

Object tagging is silently skipped for video assets and if the key is absent,
so all other strategies continue to work regardless.

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
            ├─ FaceTagStrategy
            ├─ CameraTagStrategy
            ├─ SeasonTagStrategy
            ├─ DayOfWeekTagStrategy
            ├─ MediaTypeTagStrategy
            ├─ FileFormatTagStrategy
            └─ ObjectTagStrategy  (Claude Vision, plain HTTP)
       └─ immich_api.py    HTTP client for Immich REST API
```

---

## Docker Hub

The image is built and published automatically by GitHub Actions on every
push to `main` and on version tags.

```bash
# Latest
docker pull samuelstreets/immich-auto-tagger:latest

# Specific release
docker pull samuelstreets/immich-auto-tagger:v1.0.0
```

Images are built for both `linux/amd64` and `linux/arm64` (Raspberry Pi /
Apple Silicon).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `KeyError: 'IMMICH_URL'` | Set the env variable in docker-compose or `.env` |
| Tags not appearing | Check that the API key has **write** permissions |
| Geocoding always empty | Ensure the container can reach `nominatim.openstreetmap.org` or your self-hosted instance |
| Face tags missing | Verify person names are set in the Immich UI (Explore → People) |
| Object tags missing | Check `ANTHROPIC_API_KEY` is set and the container can reach `api.anthropic.com` |
| Service keeps re-scanning all assets | The `tagger-state` volume is not persisted — check volume mounts |