"""
Event emitter: translates pipeline detections into structured REST API events.

Design: fire-and-forget HTTP calls with retry. If the API is unreachable,
events are buffered in a local queue and retried with exponential backoff.

This ensures the pipeline doesn't drop events during transient API outages.
Max buffer: 10,000 events (configurable). If buffer fills → oldest events dropped.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from pipeline.detector import Detection, TrackedPerson

logger = logging.getLogger(__name__)


class EventEmitter:
    """
    Converts detection results into API events and sends them to the intelligence API.
    """

    MAX_BUFFER_SIZE = 10_000
    RETRY_ATTEMPTS = 3
    RETRY_BACKOFF_BASE = 0.5  # seconds

    def __init__(
        self,
        api_base_url: str,
        store_id: str,
        buffer_size: int = MAX_BUFFER_SIZE,
    ):
        self.api_base_url = api_base_url.rstrip("/")
        self.store_id = store_id
        self._buffer: deque[dict] = deque(maxlen=buffer_size)
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "EventEmitter":
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "Missing required package 'httpx'. Install dependencies with `pip install -r requirements.txt`"
            ) from exc

        self._client = httpx.AsyncClient(timeout=5.0)
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    def _build_event(
        self,
        event_type: str,
        track: TrackedPerson,
        det: Detection,
        zone_id: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": det.camera_id,
            "event_type": event_type,
            "timestamp": det.timestamp.isoformat(),
            "track_id": str(det.track_id),
            "visitor_id": track.visitor_id or str(det.track_id),
            "zone_id": zone_id,
            "is_staff": track.is_staff,
            "confidence": det.confidence,
            "group_id": group_id,
            "bbox": {
                "x": det.bbox[0],
                "y": det.bbox[1],
                "w": det.bbox[2],
                "h": det.bbox[3],
            },
        }

    async def emit_entry(
        self,
        track: TrackedPerson,
        det: Detection,
        group_id: Optional[str] = None,
    ) -> None:
        event = self._build_event("entry", track, det, group_id=group_id)
        await self._send(event)

    async def emit_exit(self, track: TrackedPerson, det: Detection) -> None:
        event = self._build_event("exit", track, det)
        await self._send(event)

    async def emit_zone_event(
        self,
        event_type: str,
        track: TrackedPerson,
        det: Detection,
        zone_id: str,
    ) -> None:
        event = self._build_event(event_type, track, det, zone_id=zone_id)
        await self._send(event)

    async def emit_purchase(self, track: TrackedPerson, det: Detection) -> None:
        event = self._build_event("purchase", track, det)
        await self._send(event)

    async def emit_staff(self, track: TrackedPerson, det: Detection) -> None:
        event = self._build_event("staff_detected", track, det)
        await self._send(event)

    async def _send(self, event: dict) -> None:
        """Send with retry + buffer fallback."""
        for attempt in range(self.RETRY_ATTEMPTS):
            try:
                if self._client is None:
                    self._buffer.append(event)
                    return
                resp = await self._client.post(
                    f"{self.api_base_url}/events/ingest",
                    json=event,
                )
                if resp.status_code in (201, 409):
                    return  # success or duplicate
                resp.raise_for_status()
            except Exception as e:
                try:
                    import httpx
                except ImportError:
                    logger.error(
                        "Missing required package 'httpx'. Install dependencies with `pip install -r requirements.txt`."
                    )
                    self._buffer.append(event)
                    return

                if isinstance(e, (httpx.HTTPError, httpx.TimeoutException)):
                    if attempt < self.RETRY_ATTEMPTS - 1:
                        await asyncio.sleep(self.RETRY_BACKOFF_BASE * (2 ** attempt))
                        continue
                logger.warning(f"Failed to send event {event.get('event_id')}: {e}. Buffering.")
                self._buffer.append(event)
                return

    async def flush_buffer(self) -> int:
        """Retry buffered events. Returns number successfully sent."""
        sent = 0
        while self._buffer:
            event = self._buffer.popleft()
            try:
                if self._client:
                    resp = await self._client.post(
                        f"{self.api_base_url}/events/ingest",
                        json=event,
                    )
                    if resp.status_code in (201, 409):
                        sent += 1
                    else:
                        self._buffer.appendleft(event)
                        break
            except Exception:
                self._buffer.appendleft(event)
                break
        return sent

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)
