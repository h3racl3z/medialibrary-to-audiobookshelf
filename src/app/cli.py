from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CONFIG_OUTPUT_DIR = Path("/downloads")
CONFIG_DOWNLOAD_LOG = Path("/config/download.log")
CONFIG_DIR = Path("/config")
CONFIG_FILENAME_ENV = "CONFIG_FILENAME"
DEFAULT_CONFIG_FILENAME = "config.yaml"
LOGGER = logging.getLogger("app")


@dataclass(frozen=True)
class Episode:
    title: str
    url: str
    timestamp: datetime

    @property
    def date_text(self) -> str:
        return self.timestamp.astimezone().strftime("%Y-%m-%d")


@dataclass(frozen=True)
class MetadataConfig:
    show: str | None
    publisher: str | None
    language: str | None
    genre: str | None


@dataclass(frozen=True)
class ShowConfig:
    name: str
    url: str
    download_period: str
    download_path: str
    metadata: MetadataConfig


@dataclass(frozen=True)
class AudiobookshelfConfig:
    enabled: bool
    url: str
    library_id: str
    api_token: str


@dataclass(frozen=True)
class AppConfig:
    schedule: str
    audio_quality: str
    audiobookshelf: AudiobookshelfConfig
    shows: list[ShowConfig]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        configure_logging()
        configure_yt_dlp_version()
        ensure_yt_dlp()
        if args.command in {"download", "run", "serve"}:
            ensure_tool("ffmpeg")

        if args.command == "list":
            return list_command(args)
        if args.command == "download":
            return download_command(args)
        if args.command == "run":
            return run_command(args)
        if args.command == "serve":
            return serve_command(args)
    except AppError as exc:
        LOGGER.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.error("aborted")
        return 130

    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medialibrary-to-audiobookshelf",
        description="List and download recent media library episodes as MP3 files.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List recent episodes")
    add_common_args(list_parser)

    download_parser = subparsers.add_parser("download", help="Download recent episodes as MP3")
    add_common_args(download_parser)
    download_parser.add_argument(
        "--output",
        type=Path,
        default=Path("downloads"),
        help="Directory for downloaded MP3 files. Default: downloads",
    )
    download_parser.add_argument(
        "--archive",
        type=Path,
        default=Path("config/download.log"),
        help="yt-dlp archive file used to skip already downloaded episodes. Default: config/download.log",
    )
    download_parser.add_argument(
        "--audio-quality",
        default="0",
        help="yt-dlp MP3 quality. 0 is best VBR, 10 is worst. Default: 0",
    )
    download_parser.add_argument(
        "--show-name",
        default=None,
        help="Show name used in output filenames, e.g. Markus Lanz.",
    )

    download_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the yt-dlp commands without downloading.",
    )

    run_parser = subparsers.add_parser("run", help="Download shows from a YAML config file")
    run_parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="Path to YAML config file. Default: /config/$CONFIG_FILENAME or /config/config.yaml",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the yt-dlp commands without downloading.",
    )

    serve_parser = subparsers.add_parser("serve", help="Run scheduled downloads from a YAML config file")
    serve_parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="Path to YAML config file. Default: /config/$CONFIG_FILENAME or /config/config.yaml",
    )
    serve_parser.add_argument(
        "--run-on-start",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run once immediately before waiting for the next scheduled run. Default: true",
    )

    return parser


def default_config_path() -> Path:
    config_filename = os.environ.get(CONFIG_FILENAME_ENV, DEFAULT_CONFIG_FILENAME).strip()
    return CONFIG_DIR / (config_filename or DEFAULT_CONFIG_FILENAME)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", required=True, help="Series/channel URL to inspect")
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Only include episodes not older than this many days. Default: 14",
    )


def list_command(args: argparse.Namespace) -> int:
    episodes = recent_episodes(args.url, args.days)
    for episode in episodes:
        print(f"{episode.date_text}\t{episode.title}\t{episode.url}")
    return 0


def download_command(args: argparse.Namespace) -> int:
    episodes = recent_episodes(args.url, args.days)
    if not episodes:
        LOGGER.info("No matching episodes found.")
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    args.archive.parent.mkdir(parents=True, exist_ok=True)

    for episode in episodes:
        command = build_download_command(
            episode=episode,
            output_dir=args.output,
            archive_file=args.archive,
            audio_quality=args.audio_quality,
            show_name=args.show_name,
        )
        LOGGER.debug("yt-dlp command: %s", format_command(command))
        LOGGER.info("Downloading: %s %s", episode.date_text, episode.title)
        if args.dry_run:
            print(format_command(command))
            continue

        subprocess.run(command, check=True)

    return 0


def run_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    process_config(config, dry_run=args.dry_run)
    return 0


def serve_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    validate_schedule(config.schedule)

    LOGGER.info("Scheduler started with cron schedule: %s", config.schedule)
    if args.run_on_start:
        LOGGER.info("Running once on start.")
        process_config(config, dry_run=False)

    while True:
        config = load_config(args.config)
        next_run = next_scheduled_run(config.schedule)
        sleep_seconds = max(0.0, (next_run - datetime.now().astimezone()).total_seconds())
        LOGGER.info("Next run: %s", next_run.isoformat())
        time.sleep(sleep_seconds)
        process_config(config, dry_run=False)


def process_config(config: AppConfig, *, dry_run: bool) -> None:
    CONFIG_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DOWNLOAD_LOG.parent.mkdir(parents=True, exist_ok=True)

    for show in config.shows:
        show_output_dir = CONFIG_OUTPUT_DIR / show.download_path
        LOGGER.info("Show: %s (%s)", show.name, show.download_period)
        episodes = recent_episodes_since(show.url, cutoff_for_period(show.download_period))
        if not episodes:
            LOGGER.info("No matching episodes found.")
            continue

        show_output_dir.mkdir(parents=True, exist_ok=True)
        for episode in episodes:
            command = build_download_command(
                episode=episode,
                output_dir=show_output_dir,
                archive_file=CONFIG_DOWNLOAD_LOG,
                audio_quality=config.audio_quality,
                show_name=show.metadata.show or show.name,
            )
            LOGGER.debug("yt-dlp command: %s", format_command(command))
            LOGGER.info("Downloading: %s %s", episode.date_text, episode.title)
            if dry_run:
                print(format_command(command))
                continue

            info = fetch_video_info(episode.url)
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            if result.stderr:
                LOGGER.debug("yt-dlp stderr: %s", result.stderr.strip())

            for line in result.stdout.splitlines():
                downloaded_path = parse_downloaded_filepath(line)
                if downloaded_path is None:
                    LOGGER.debug("yt-dlp output: %s", line)
                    continue
                apply_mp3_metadata(downloaded_path, show, info)
                LOGGER.info("Tagged: %s", downloaded_path)

    if config.audiobookshelf.enabled:
        if dry_run:
            LOGGER.info("Audiobookshelf scan skipped in dry-run mode.")
        else:
            trigger_audiobookshelf_scan(config.audiobookshelf)


def load_config(path: Path) -> AppConfig:
    try:
        import yaml
    except ImportError as exc:
        raise AppError("PyYAML is required for config file support") from exc

    try:
        with path.open("r", encoding="utf-8") as file:
            payload = yaml.safe_load(file)
    except FileNotFoundError as exc:
        raise AppError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise AppError(f"config file is not valid YAML: {exc}") from exc

    if not isinstance(payload, dict):
        raise AppError("config file must contain a YAML object")

    defaults = payload.get("defaults", {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise AppError("config defaults must be a YAML object")

    default_download_period = read_str(defaults, "download_period", "14d", context="defaults")
    parse_download_period(default_download_period, context="defaults.download_period")
    schedule = read_str(defaults, "schedule", "0 */6 * * *", context="defaults")
    validate_schedule(schedule)
    audio_quality = read_str(defaults, "audio_quality", "0", context="defaults")
    audiobookshelf = read_audiobookshelf_config(payload.get("audiobookshelf", {}))

    raw_shows = payload.get("shows")
    if not isinstance(raw_shows, list) or not raw_shows:
        raise AppError("config must contain at least one show in `shows`")

    shows: list[ShowConfig] = []
    for index, raw_show in enumerate(raw_shows, start=1):
        context = f"shows[{index}]"
        if not isinstance(raw_show, dict):
            raise AppError(f"{context} must be a YAML object")

        name = read_str(raw_show, "name", None, context=context)
        url = read_str(raw_show, "url", None, context=context)
        download_period = read_str(raw_show, "download_period", default_download_period, context=context)
        parse_download_period(download_period, context=f"{context}.download_period")
        download_path = read_str(raw_show, "download_path", name, context=context)
        metadata = read_optional_metadata(raw_show.get("metadata", {}), context=f"{context}.metadata")

        shows.append(
            ShowConfig(
                name=name,
                url=url,
                download_period=download_period,
                download_path=download_path,
                metadata=metadata,
            )
        )

    return AppConfig(
        schedule=schedule,
        audio_quality=audio_quality,
        audiobookshelf=audiobookshelf,
        shows=shows,
    )


def read_str(data: dict[str, Any], key: str, default: str | None, *, context: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise AppError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def read_bool(data: dict[str, Any], key: str, default: bool, *, context: str) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise AppError(f"{context}.{key} must be true or false")
    return value


def read_audiobookshelf_config(data: Any) -> AudiobookshelfConfig:
    context = "audiobookshelf"
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise AppError(f"{context} must be a YAML object")

    enabled = read_bool(data, "enabled", False, context=context)
    url = read_str(data, "url", "http://audiobookshelf:13378", context=context)
    raw_library_id = data.get("library_id", "")
    raw_api_token = data.get("api_token", "")
    if not isinstance(raw_library_id, str):
        raise AppError("audiobookshelf.library_id must be a string")
    if not isinstance(raw_api_token, str):
        raise AppError("audiobookshelf.api_token must be a string")

    library_id = raw_library_id.strip()
    api_token = raw_api_token.strip()
    if enabled and not library_id:
        raise AppError("audiobookshelf.library_id must be set when Audiobookshelf integration is enabled")
    if enabled and not api_token:
        raise AppError("audiobookshelf.api_token must be set when Audiobookshelf integration is enabled")

    return AudiobookshelfConfig(
        enabled=enabled,
        url=url,
        library_id=library_id,
        api_token=api_token,
    )


def read_optional_metadata(data: Any, *, context: str) -> MetadataConfig:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise AppError(f"{context} must be a YAML object")

    return MetadataConfig(
        show=read_optional_str(data, "show", context=context),
        publisher=read_optional_str(data, "publisher", context=context),
        language=read_optional_str(data, "language", context=context),
        genre=read_optional_str(data, "genre", context=context),
    )


def read_optional_str(data: dict[str, Any], key: str, *, context: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AppError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def validate_schedule(schedule: str) -> None:
    try:
        from croniter import croniter
    except ImportError as exc:
        raise AppError("croniter is required for scheduled service mode") from exc

    if not croniter.is_valid(schedule):
        raise AppError(f"defaults.schedule must be a valid cron expression: {schedule}")


def next_scheduled_run(schedule: str) -> datetime:
    try:
        from croniter import croniter
    except ImportError as exc:
        raise AppError("croniter is required for scheduled service mode") from exc

    return croniter(schedule, datetime.now().astimezone()).get_next(datetime)


def trigger_audiobookshelf_scan(config: AudiobookshelfConfig) -> None:
    token = config.api_token.strip()
    if not token:
        raise AppError("audiobookshelf.api_token must be set when Audiobookshelf integration is enabled")

    endpoint = f"{config.url.rstrip('/')}/api/libraries/{config.library_id}/scan"
    request = Request(
        endpoint,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )

    LOGGER.info("Triggering Audiobookshelf library scan: %s", config.library_id)
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace").strip()
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace").strip()
        raise AppError(f"Audiobookshelf scan failed with HTTP {exc.code}: {details}") from exc
    except URLError as exc:
        raise AppError(f"Audiobookshelf scan failed: {exc.reason}") from exc

    if body:
        LOGGER.info("Audiobookshelf response: %s", body)
    else:
        LOGGER.info("Audiobookshelf scan request accepted.")


def recent_episodes(url: str, days: int) -> list[Episode]:
    if days < 0:
        raise AppError("--days must be zero or greater")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return recent_episodes_since(url, cutoff)


def recent_episodes_since(url: str, cutoff: datetime) -> list[Episode]:
    episodes = fetch_flat_playlist(url)
    return sorted(
        (episode for episode in episodes if episode.timestamp >= cutoff),
        key=lambda episode: episode.timestamp,
    )


def cutoff_for_period(period: str, *, now: datetime | None = None) -> datetime:
    amount, unit = parse_download_period(period, context="download_period")
    now = now or datetime.now(timezone.utc)
    if unit == "d":
        return now - timedelta(days=amount)
    if unit == "w":
        return now - timedelta(weeks=amount)
    return subtract_months(now, amount)


def parse_download_period(period: str, *, context: str) -> tuple[int, str]:
    value = period.strip().lower()
    if len(value) < 2:
        raise AppError(f"{context} must use a value like 14d, 2w, or 1m")

    amount_text = value[:-1]
    unit = value[-1]
    if unit not in {"d", "w", "m"}:
        raise AppError(f"{context} unit must be one of d, w, or m")
    if not amount_text.isdecimal():
        raise AppError(f"{context} amount must be a positive integer")

    amount = int(amount_text)
    if amount <= 0:
        raise AppError(f"{context} amount must be greater than zero")

    return amount, unit


def subtract_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 - months
    year = month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def fetch_flat_playlist(url: str) -> list[Episode]:
    command = yt_dlp_command("--flat-playlist", "-J", url)
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise AppError(f"yt-dlp failed while reading playlist: {details}") from exc

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppError("yt-dlp returned invalid JSON") from exc

    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise AppError("yt-dlp JSON did not contain a playlist entries list")

    episodes: list[Episode] = []
    for entry in entries:
        episode = episode_from_entry(entry)
        if episode is not None:
            episodes.append(episode)

    return episodes


def episode_from_entry(entry: Any) -> Episode | None:
    if not isinstance(entry, dict):
        return None

    title = entry.get("title")
    url = entry.get("url") or entry.get("webpage_url")
    timestamp = entry.get("timestamp")
    if not isinstance(title, str) or not isinstance(url, str):
        return None
    if not isinstance(timestamp, int | float):
        return None

    return Episode(
        title=title.strip(),
        url=url,
        timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
    )


def build_download_command(
    *,
    episode: Episode,
    output_dir: Path,
    archive_file: Path,
    audio_quality: str,
    show_name: str | None = None,
) -> list[str]:
    date_template = "%(upload_date>%d.%m.%Y)s"
    if show_name:
        filename_template = f"({date_template}) {filename_template_literal(show_name)} – %(title).200B.%(ext)s"
    else:
        filename_template = f"({date_template}) %(title).200B.%(ext)s"
    output_template = str(output_dir / filename_template)
    return yt_dlp_command(
        "-f",
        "worst[acodec!=none][vcodec!=none]/worst[acodec!=none]/worst",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        audio_quality,
        "--download-archive",
        str(archive_file),
        "--print",
        "after_move:%(filepath)j",
        "-o",
        output_template,
        episode.url,
    )


def filename_template_literal(value: str) -> str:
    cleaned = value.strip().replace("/", "-").replace("\\", "-")
    return cleaned.replace("%", "%%") or "Show"


def fetch_video_info(url: str) -> dict[str, Any]:
    command = yt_dlp_command("-J", url)
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise AppError(f"yt-dlp failed while reading video metadata: {details}") from exc

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AppError("yt-dlp returned invalid video metadata JSON") from exc

    if not isinstance(payload, dict):
        raise AppError("yt-dlp video metadata JSON was not an object")
    return payload


def parse_downloaded_filepath(line: str) -> Path | None:
    line = line.strip()
    if not line:
        return None

    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(value, str) or not value.lower().endswith(".mp3"):
        return None
    return Path(value)


def apply_mp3_metadata(path: Path, show: ShowConfig, info: dict[str, Any]) -> None:
    try:
        from mutagen.id3 import (
            ID3,
            TALB,
            TCON,
            TDRC,
            TIT2,
            TLAN,
            TPE1,
            TPUB,
            TPOS,
            TRCK,
            TSOT,
        )
        from mutagen.mp3 import MP3
    except ImportError as exc:
        raise AppError("mutagen is required for MP3 metadata tagging") from exc

    audio = MP3(path, ID3=ID3)
    if audio.tags is None:
        audio.add_tags()
    tags = cast(ID3, audio.tags)

    title = string_from_info(info, "title", path.stem)
    description = string_from_info(info, "description", "")
    upload_date = string_from_info(info, "upload_date", "")
    timestamp = int_from_info(info, "timestamp")
    episode_number = int_from_info(info, "episode_number")
    season_number = season_number_from_info(info, upload_date, timestamp)
    album = show.metadata.show or first_string_from_info(info, "series", "playlist_title")
    publisher = show.metadata.publisher or first_string_from_info(info, "uploader", "channel")
    language = show.metadata.language or string_from_info(info, "language", "")
    genre = show.metadata.genre or joined_list_from_info(info, "categories") or joined_list_from_info(info, "tags")

    replace_frames(tags, "TIT2", TIT2(encoding=3, text=title))
    replace_frames(tags, "TSOT", TSOT(encoding=3, text=title))
    if album:
        replace_frames(tags, "TALB", TALB(encoding=3, text=album))
    if publisher:
        replace_frames(tags, "TPE1", TPE1(encoding=3, text=publisher))
        replace_frames(tags, "TPUB", TPUB(encoding=3, text=publisher))
    if language:
        replace_frames(tags, "TLAN", TLAN(encoding=3, text=language))
    if genre:
        replace_frames(tags, "TCON", TCON(encoding=3, text=genre))

    date_text = id3_date_from_upload_date(upload_date)
    if date_text:
        replace_frames(tags, "TDRC", TDRC(encoding=3, text=date_text))

    release_date = rfc2822_date_from_timestamp(timestamp)
    if release_date:
        replace_txxx(tags, "releasedate", release_date)

    if season_number:
        replace_frames(tags, "TPOS", TPOS(encoding=3, text=season_number))
        replace_txxx(tags, "season", season_number)

    if episode_number is not None:
        episode_text = str(episode_number)
        replace_frames(tags, "TRCK", TRCK(encoding=3, text=episode_text))
        replace_txxx(tags, "episode", episode_text)

    if description:
        replace_txxx(tags, "subtitle", short_text(description, 160))
        replace_txxx(tags, "comment", build_comment(description, string_from_info(info, "webpage_url", "")))

    replace_txxx(tags, "podcast", "1")
    replace_txxx(tags, "podcast-type", "episodic")
    replace_txxx(tags, "episode-type", "full")

    tags.save(path, v2_version=4)


def replace_frames(tags: Any, frame_id: str, frame: Any) -> None:
    tags.delall(frame_id)
    tags.add(frame)


def replace_txxx(tags: Any, description: str, text: str) -> None:
    from mutagen.id3 import TXXX

    for key in list(tags.keys()):
        frame = tags[key]
        if isinstance(frame, TXXX) and frame.desc == description:
            del tags[key]
    tags.add(TXXX(encoding=3, desc=description, text=text))


def string_from_info(info: dict[str, Any], key: str, default: str) -> str:
    value = info.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def first_string_from_info(info: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = string_from_info(info, key, "")
        if value:
            return value
    return ""


def joined_list_from_info(info: dict[str, Any], key: str) -> str:
    value = info.get(key)
    if not isinstance(value, list):
        return ""
    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return ";".join(items)


def int_from_info(info: dict[str, Any], key: str) -> int | None:
    value = info.get(key)
    if isinstance(value, int):
        return value
    return None


def season_number_from_info(info: dict[str, Any], upload_date: str, timestamp: int | None) -> str:
    season_number = int_from_info(info, "season_number")
    if season_number is not None:
        return str(season_number)
    if len(upload_date) >= 4 and upload_date[:4].isdecimal():
        return upload_date[:4]
    if timestamp is not None:
        return str(datetime.fromtimestamp(timestamp, tz=timezone.utc).year)
    return ""


def id3_date_from_upload_date(upload_date: str) -> str:
    if len(upload_date) != 8 or not upload_date.isdecimal():
        return ""
    return f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"


def rfc2822_date_from_timestamp(timestamp: int | None) -> str:
    if timestamp is None:
        return ""
    return format_datetime(datetime.fromtimestamp(timestamp, tz=timezone.utc))


def short_text(value: str, max_length: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."


def build_comment(description: str, webpage_url: str) -> str:
    if not webpage_url:
        return description
    return f"{description}\n\nSource: {webpage_url}"


def configure_logging() -> None:
    raw_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = logging.getLevelName(raw_level)
    if not isinstance(level, int):
        raise AppError(f"LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, or CRITICAL: {raw_level}")

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def yt_dlp_command(*args: str) -> list[str]:
    if has_yt_dlp_package():
        return [sys.executable, "-m", "yt_dlp", *args]
    return ["yt-dlp", *args]


def configure_yt_dlp_version() -> None:
    requested_version = os.environ.get("YT_DLP_VERSION", "").strip()
    if not requested_version:
        return

    if requested_version.lower() == "latest":
        package_spec = "yt-dlp"
        LOGGER.info("Updating yt-dlp to the latest version.")
    else:
        package_spec = f"yt-dlp=={requested_version}"
        LOGGER.info("Installing yt-dlp version %s.", requested_version)

    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--upgrade",
        package_spec,
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise AppError(f"failed to install requested yt-dlp version: {requested_version}") from exc


def ensure_yt_dlp() -> None:
    if has_yt_dlp_package() or shutil.which("yt-dlp") is not None:
        return
    raise AppError(
        "yt-dlp is required. Install this project with `python -m pip install -e .` "
        "or install the yt-dlp command line tool."
    )


def has_yt_dlp_package() -> bool:
    return importlib.util.find_spec("yt_dlp") is not None


def ensure_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise AppError(f"{name} is required but was not found in PATH")


def format_command(command: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) for part in command)


class AppError(Exception):
    pass
