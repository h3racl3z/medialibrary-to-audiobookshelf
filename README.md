# medialibrary-to-audiobookshelf

<p align="center">
  <img src="src/images/logo.png" alt="medialibrary-to-audiobookshelf logo" width="220">
</p>
<div align="center">
<a href="https://github.com/h3racl3z/medialibrary-to-audiobookshelf/pkgs/container/medialibrary-to-audiobookshelf"><img src="https://img.shields.io/badge/GHCR-medialibrary--to--audiobookshelf-blue?logo=github" alt="GitHub Container Registry"></a>
<a href="https://github.com/h3racl3z/medialibrary-to-audiobookshelf/actions/workflows/docker-image.yml"><img src="https://img.shields.io/github/actions/workflow/status/h3racl3z/medialibrary-to-audiobookshelf/docker-image.yml?label=Docker%20Build" alt="Docker Build"></a>
<a href="https://github.com/h3racl3z/medialibrary-to-audiobookshelf"><img src="https://img.shields.io/github/languages/code-size/h3racl3z/medialibrary-to-audiobookshelf" alt="Code Size"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License"></a>
</div><br>

**Docker-based application** for scheduled downloading recent episodes from media libraries without official podcast support, converting them to **MP3**, and writing podcast-friendly **ID3-metadata** for [`Audiobookshelf`](https://www.audiobookshelf.org/). The application is based on [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) for accessing media libraries and [`ffmpeg`](https://ffmpeg.org/) for audio conversion.<br><br>
## Supported popular media libraries

<details>
<summary>Germany 🇩🇪</summary>

- ARD Mediathek
- ZDF Mediathek
- Arte
- 3sat
- RTL+
- Joyn
- Deutsche Welle

</details>

<details>
<summary>Austria 🇦🇹</summary>

- ORF TVthek
- ATV
- ServusTV

</details>

<details>
<summary>Switzerland 🇨🇭</summary>

- SRF
- RTS
- RSI

</details>

<details>
<summary>France 🇫🇷</summary>

- Arte
- France.tv
- TF1
- Canal+

</details>

<details>
<summary>United Kingdom 🇬🇧</summary>

- BBC iPlayer
- Channel 4
- ITVX
- Sky

</details>

<details>
<summary>Italy 🇮🇹</summary>

- RaiPlay
- Mediaset

</details>

<details>
<summary>Spain 🇪🇸</summary>

- RTVE
- AtresPlayer
- Mitele

</details>

<details>
<summary>Netherlands 🇳🇱</summary>

- NPO Start
- Videoland

</details>

<details>
<summary>Belgium 🇧🇪</summary>

- VRT MAX
- RTBF Auvio

</details>

<details>
<summary>Scandinavia 🇸🇪 🇳🇴 🇩🇰 🇫🇮</summary>

- SVT Play
- NRK TV
- DR TV
- YLE Areena
</details>

<details>
<summary>United States 🇺🇸</summary>

- PBS
- NBC
- CBS
- ABC
- FOX
- Comedy Central

</details>

<details>
<summary>Australia 🇦🇺</summary>

- ABC iview
- SBS On Demand
- 7plus
- 9Now
- 10play

</details>

## Directory Structure

```text
medialibrary-to-audiobookshelf
├── .github
│   └── workflows
│       └── docker-image.yml
├── config
│   └── config.yaml
├── src
│   ├── app
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   └── cli.py
│   └── images
│       └── logo.png
├── .dockerignore
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── LICENSE
├── pyproject.toml
└── README.md
```

## Installation

### Docker Compose with GitHub Container Registry

1. Place `docker-compose.yml` and create `/config` folder on your server. Create a `/config/config.yaml` file or download the example configuration. `download.log is` automatically generated and keeps track of all downloaded episodes to prevent them from being processed again. Delete this file to reset the application.
2. Adjust the mount paths in `docker-compose.yml`:
   ```yaml
   volumes:
     - ./path_to_downloads:/downloads   # Path to your Audiobookshelf podcast folder, e. g. /audiobookshelf/podcasts
     - ./path_to_config:/config         # Persistent folder for config.yaml and download.log
   environment:
     CONFIG_FILENAME: config.yaml        # Name of the YAML config file inside /config
   ```
3. Pull and start the container:
   ```bash
   docker compose pull
   docker compose up -d
   ```

## Configuration

The main configuration file is located at `/config/config.yaml` by default. Set `CONFIG_FILENAME` in `docker-compose.yml` to use another file name inside `/config`, for example `CONFIG_FILENAME: config.prod.yaml`. With the included Compose file, this path is mounted to a host folder. Example:

```yaml
defaults:
  # Cron schedule for automatic checks in service mode.
  # This example checks every 6 hours.
  schedule: "0 */6 * * *"
  # Default lookback period for episodes to include.
  # Supported units are days (d), weeks (w), and months (m), e.g. 14d, 2w, or 1m.
  download_period: 14d
  # MP3 quality passed to yt-dlp/ffmpeg.
  # 0 is the best variable quality, 10 is the lowest variable quality.
  # Fixed bitrates like "128K" or "192K" are also supported.
  audio_quality: "128K"

# Optional Audiobookshelf integration.
# When enabled, the configured library is scanned after each successful run.
audiobookshelf:
  enabled: false
  # Base URL of your Audiobookshelf instance, e.g. http://audiobookshelf:13378
  url: http://audiobookshelf:13378
  # Library ID to scan after finished downloads; not the library display name.
  # You can get it from the Audiobookshelf API: GET /api/libraries or look up URL of your library.
  library_id: ""
  # Audiobookshelf API token used to trigger the library scan.
  # Leave this empty while the audiobookshelf scanner integration is disabled.
  api_token: ""

# Shows to download.
shows:
  # Display name used in logs and output.
  - name: Markus Lanz
    # URL of the series/show page in the media library.
    url: https://www.zdf.de/talk/markus-lanz-114
    # Subfolder inside /downloads.
    # This example writes to /downloads/Markus Lanz.
    download_path: Markus Lanz
    # Set metadata for mp3-files. The episode description and release date are extracted automatically.
    metadata:
      show: Markus Lanz
      publisher: ZDF
      language: de
      genre: Talk

```

- `download_period` accepts days (`d`), weeks (`w`), and months (`m`), e. g. `14d`, `2w`, or `1m`.
- `download_path` is the subfolder inside `/downloads`. Finished files use the format `(dd.mm.yyyy) show – title.mp3`.

## Usage

1. **Create the configuration:** Edit `/config/config.yaml` or the file name set via `CONFIG_FILENAME` and add the shows you want to download.
2. **Start the container:** Start the service with `docker compose up -d`.
3. **Check logs:** Monitor the run with `docker logs medialibrary-to-audiobookshelf`.
4. **Scan Audiobookshelf:** Optionally enable the Audiobookshelf integration in the YAML file.

## Audiobookshelf scanner integration

When enabled, the application triggers an Audiobookshelf library scan after a run, e. g.:

```yaml
audiobookshelf:
  enabled: true
  url: http://192.168.2.100:13378 # or https://audiobookshelf.yourdomain.com
  library_id: 5912dt2d-fz4b-3b88-6d7o-25fe4699qec4
  api_token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJrZXlJZCI6ImM4M2UzZmE0LTlhZDQtNDg1MS1hYTY0LTdiNDQ5NzUxMDVlMCIsIm5hbWUiOiJ0ZXN0IiwidHlwZSI6ImFwaSIsImlhdCI6MTc3ODkxODU1MX0.KcSbw1R5jjSKEDgiJ9mM1qmz0uai6t2JjnkeD94a1GU
```

`library_id` is the technical library ID, not the display name. It can be retrieved through the
Audiobookshelf API with `GET /api/libraries` or look up URL of your library.

## Environment Variables

- **`TZ`**: Container timezone, for example `Europe/Berlin`.
- **`CONFIG_FILENAME`**: Name of the YAML config file inside `/config`. Default: `config.yaml`.
- **`YT_DLP_VERSION`**: `latest` to update on container start or a fixed version such as `2026.3.17`.
- **`LOG_LEVEL`**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`.
- **`PYTHONUNBUFFERED`**: Makes logs appear immediately in Docker output.
- **`PYTHONPYCACHEPREFIX`**: Stores Python cache files in a temporary path.

## Libraries & Credits

- **yt-dlp**: Downloading and extracting media-library content ([yt-dlp](https://github.com/yt-dlp/yt-dlp)).
- **ffmpeg**: Audio conversion and MP3 creation ([ffmpeg](https://ffmpeg.org/)).
- **mutagen**: Writing ID3 metadata ([mutagen](https://mutagen.readthedocs.io/)).
- **croniter**: Calculating cron schedule times ([croniter](https://pypi.org/project/croniter/)).
- **PyYAML**: Reading YAML configuration ([PyYAML](https://pypi.org/project/PyYAML/)).

## License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.
