from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from pipeline.entry_exit_classifier import StoreLayout
from pipeline.video_processor import VideoProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the CCTV detection pipeline against live camera streams."
    )
    parser.add_argument(
        "--config",
        default="configs/store_layout.json",
        help="Path to store layout JSON config.",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://localhost:8000",
        help="Base URL of the intelligence API.",
    )
    parser.add_argument(
        "--store-id",
        default=None,
        help="Store ID to include in ingested events. Falls back to the config value.",
    )
    parser.add_argument(
        "--stream",
        action="append",
        metavar="CAMERA_ID=URL",
        required=True,
        help="Live camera stream URL for a camera ID (repeatable).",
    )
    parser.add_argument(
        "--fps-limit",
        type=float,
        default=15.0,
        help="Maximum effective frames per second to process.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force mock detection mode when models or OpenCV are unavailable.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level for the pipeline runner.",
    )
    parser.add_argument(
        "--repeat",
        action="store_true",
        help="Keep the pipeline running by repeating stream processing cycles.",
    )
    parser.add_argument(
        "--repeat-delay",
        type=float,
        default=10.0,
        help="Seconds to sleep between repeated pipeline cycles.",
    )
    return parser.parse_args()


def load_store_layout(config_path: Path) -> StoreLayout:
    config_data = json.loads(config_path.read_text())
    return StoreLayout.from_dict(config_data)


def parse_stream_argument(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(
            "Stream arguments must use CAMERA_ID=URL format, e.g. cam_entrance=rtsp://..."
        )
    camera_id, url = value.split("=", 1)
    return camera_id.strip(), url.strip()


async def check_api_health(api_base_url: str) -> None:
    try:
        import httpx
    except ImportError as exc:
        raise ImportError(
            "Missing required package 'httpx'. Install dependencies with `pip install -r requirements.txt`"
        ) from exc

    health_url = api_base_url.rstrip("/") + "/health"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(health_url)
        resp.raise_for_status()


async def process_camera(
    camera_id: str,
    stream_url: str,
    layout: StoreLayout,
    api_base_url: str,
    store_id: str,
    fps_limit: float,
    mock_mode: bool,
) -> dict[str, Any]:
    logger.info("Starting pipeline for camera '%s' -> %s", camera_id, stream_url)
    processor = VideoProcessor(
        camera_id=camera_id,
        store_layout=layout,
        api_base_url=api_base_url,
        store_id=store_id,
        mock_mode=mock_mode,
    )

    try:
        stats = await processor.process_video(stream_url, fps_limit=fps_limit)
        logger.info("Finished camera '%s' pipeline: %s", camera_id, stats)
        return {"camera_id": camera_id, "url": stream_url, "stats": stats}
    except Exception as exc:
        logger.exception("Pipeline failed for camera '%s': %s", camera_id, exc)
        return {"camera_id": camera_id, "url": stream_url, "error": str(exc)}


async def main() -> int:
    args = parse_args()
    logging.getLogger().setLevel(args.log_level.upper())

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Store layout config not found: %s", config_path)
        return 1

    try:
        await check_api_health(args.api_base_url)
    except Exception as exc:
        logger.error(
            "API health check failed for %s: %s",
            args.api_base_url,
            exc,
        )
        return 1

    layout = load_store_layout(config_path)
    store_id = args.store_id or layout.store_id

    while True:
        tasks = []
        for stream in args.stream:
            camera_id, url = parse_stream_argument(stream)
            tasks.append(
                process_camera(
                    camera_id=camera_id,
                    stream_url=url,
                    layout=layout,
                    api_base_url=args.api_base_url,
                    store_id=store_id,
                    fps_limit=args.fps_limit,
                    mock_mode=args.mock,
                )
            )

        results = await asyncio.gather(*tasks)
        failed = [res for res in results if res.get("error")]
        if failed:
            logger.error("Pipeline completed with %d failure(s).", len(failed))
        else:
            logger.info("Pipeline completed successfully for %d camera(s).", len(results))

        if not args.repeat:
            return 2 if failed else 0

        logger.info("Sleeping %.1f seconds before next pipeline cycle.", args.repeat_delay)
        await asyncio.sleep(args.repeat_delay)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
