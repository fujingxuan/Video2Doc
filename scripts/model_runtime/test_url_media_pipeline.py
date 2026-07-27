from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.providers.media import FfprobeProvider, MediaSourceResolver  # noqa: E402
from app.storage.workspace import WorkspacePaths, write_json  # noqa: E402


async def run(args: argparse.Namespace) -> None:
    paths = WorkspacePaths.from_root(args.output.resolve())
    paths.create()
    progress_events: list[dict[str, object]] = []

    def progress(value: float | None, message: str) -> None:
        progress_events.append({"progress": value, "message": message[:300]})

    acquired = await MediaSourceResolver(args.ytdlp).acquire(
        args.url,
        "URL",
        paths,
        cancel_check=lambda: False,
        progress=progress,
    )
    media = await FfprobeProvider(args.ffprobe).probe(
        Path(acquired.local_media_path), paths, cancel_check=lambda: False
    )
    write_json(
        paths.metadata / "url_acceptance.json",
        {
            "acquisition": acquired.to_dict(),
            "media": media.to_dict(),
            "progress_events": progress_events,
        },
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "provider": acquired.provider,
                "local_media_path": acquired.local_media_path,
                "bytes": Path(acquired.local_media_path).stat().st_size,
                "duration": media.duration,
                "audio_streams": len(media.audio_streams),
                "video_streams": len(media.video_streams),
                "metadata": str(paths.metadata / "url_acceptance.json"),
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ytdlp", default="yt-dlp")
    parser.add_argument("--ffprobe", default="ffprobe")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
