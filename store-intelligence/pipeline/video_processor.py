"""
Video processor: orchestrates the full pipeline for a single camera stream.

Pipeline per frame:
  1. Read frame from CCTV (cv2.VideoCapture)
  2. Run YOLOv8 + ByteTrack → list of Detection
  3. Update EntryExitClassifier with each detection
  4. Run StaffClassifier to flag staff
  5. Run ReIdentifier to assign visitor_id
  6. Emit events to the intelligence API
  7. Detect queue depth from billing zone ROI

Group entry detection:
  If 2+ people cross the tripwire within 2 seconds and within 100px of each other,
  they are assigned the same group_id. This avoids counting a family as separate funnel entries.

Queue depth:
  Count people whose centroids are within the billing zone polygon.
  Emit queue_join event when a new person enters the billing zone.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from pipeline.detector import PersonDetector, Detection, TrackedPerson
from pipeline.entry_exit_classifier import EntryExitClassifier, StaffClassifier, ReIdentifier, StoreLayout
from pipeline.event_emitter import EventEmitter

logger = logging.getLogger(__name__)

GROUP_ENTRY_WINDOW_SECONDS = 2.0
GROUP_PROXIMITY_PIXELS = 100  # normalized: ~0.1 * frame width


class VideoProcessor:
    """
    Processes a video file or live camera stream through the full detection pipeline.
    """

    FRAME_SAMPLE_RATE = 2  # process every Nth frame (30fps → 15fps effective)

    def __init__(
        self,
        camera_id: str,
        store_layout: StoreLayout,
        api_base_url: str,
        store_id: str,
        detector: Optional[PersonDetector] = None,
        mock_mode: bool = False,
    ):
        self.camera_id = camera_id
        self.store_layout = store_layout
        self.api_base_url = api_base_url
        self.store_id = store_id

        self.detector = detector or PersonDetector(mock_mode=mock_mode)
        self.classifier = EntryExitClassifier(store_layout)
        self.staff_clf = StaffClassifier(
            staff_ids=getattr(store_layout, "staff_ids", []),
            uniform_hue_range=store_layout.staff_uniform_hue_range,
        )
        self.reid = ReIdentifier(use_reid_model=False)  # set True when weights available
        self.emitter = EventEmitter(api_base_url, store_id)

        # Group entry detection state
        self._recent_entries: list[tuple[datetime, tuple]] = []
        # Queue state
        self._in_billing_zone: set[int] = set()

    async def process_video(self, video_path: str, fps_limit: float = 15.0) -> dict:
        """
        Process a video file end-to-end.
        Returns a summary of events emitted.
        """
        if self.detector.mock_mode or video_path == "mock" or video_path.startswith("mock://"):
            if not self.detector.mock_mode:
                logger.info("Mock stream requested; forcing mock pipeline for %s", video_path)
            return await self._mock_pipeline()

        try:
            import cv2
        except ImportError:
            logger.warning("OpenCV not available. Running mock pipeline.")
            return await self._mock_pipeline()

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {video_path}")

        video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_skip = max(1, int(video_fps / fps_limit))

        stats = {"entries": 0, "exits": 0, "staff": 0, "zones": 0, "frames": 0}
        frame_idx = 0

        async with self.emitter:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                if frame_idx % frame_skip != 0:
                    continue

                timestamp = datetime.utcnow()
                detections = self.detector.detect_frame(frame, self.camera_id, frame_idx, timestamp)

                for det in detections:
                    track = self.detector.get_active_tracks().get(det.track_id)
                    if not track:
                        continue

                    # Staff classification
                    h, w = frame.shape[:2]
                    x, y, bw, bh = det.bbox
                    x1, y1 = int(x * w), int(y * h)
                    x2, y2 = int((x + bw) * w), int((y + bh) * h)
                    crop = frame[y1:y2, x1:x2] if y2 > y1 and x2 > x1 else None

                    is_staff = self.staff_clf.is_staff(
                        det.track_id, track.first_seen, timestamp, crop
                    )
                    track.is_staff = is_staff

                    if is_staff:
                        await self.emitter.emit_staff(track, det)
                        stats["staff"] += 1
                        continue

                    # Re-identification
                    track.visitor_id = self.reid.get_visitor_id(
                        det.track_id, crop, self.camera_id
                    )

                    # Entry/exit classification
                    direction = self.classifier.update(det.track_id, det.bbox)
                    if direction == "entry":
                        group_id = self._check_group(timestamp, det.bbox)
                        await self.emitter.emit_entry(track, det, group_id)
                        stats["entries"] += 1
                    elif direction == "exit":
                        await self.emitter.emit_exit(track, det)
                        stats["exits"] += 1

                    # Zone tracking
                    zone_id = self.classifier.classify_zone(det.bbox)
                    if zone_id:
                        await self.emitter.emit_zone_event("zone_enter", track, det, zone_id)
                        stats["zones"] += 1

                    # Queue depth
                    await self._handle_billing_zone(track, det)

                stats["frames"] += 1

                # Flush buffer periodically
                if frame_idx % 100 == 0 and self.emitter.buffer_size > 0:
                    flushed = await self.emitter.flush_buffer()
                    logger.debug(f"Flushed {flushed} buffered events")

            # Evict stale tracks on video end
            evicted = self.detector.evict_stale_tracks(datetime.utcnow())
            logger.info(f"Pipeline complete. Stats: {stats}. Evicted tracks: {len(evicted)}")

        cap.release()
        return stats

    def _check_group(self, ts: datetime, bbox: tuple) -> Optional[str]:
        """Return group_id if this entry is close in time/space to a recent entry."""
        import uuid
        cx = bbox[0] + bbox[2] / 2
        cy = bbox[1] + bbox[3] / 2

        for prev_ts, prev_bbox in self._recent_entries:
            dt = (ts - prev_ts).total_seconds()
            if dt > GROUP_ENTRY_WINDOW_SECONDS:
                continue
            pcx = prev_bbox[0] + prev_bbox[2] / 2
            pcy = prev_bbox[1] + prev_bbox[3] / 2
            dist = ((cx - pcx) ** 2 + (cy - pcy) ** 2) ** 0.5
            if dist < GROUP_PROXIMITY_PIXELS / 1000:  # normalized
                gid = str(uuid.uuid4())
                self._recent_entries.clear()
                return gid

        self._recent_entries.append((ts, bbox))
        # Keep only recent entries
        cutoff = ts.timestamp() - GROUP_ENTRY_WINDOW_SECONDS
        self._recent_entries = [(t, b) for t, b in self._recent_entries
                                 if t.timestamp() >= cutoff]
        return None

    async def _handle_billing_zone(self, track: TrackedPerson, det: Detection) -> None:
        """Emit queue events for billing zone."""
        billing_zone = next((z for z in self.store_layout.zones if "billing" in z.zone_id.lower()), None)
        if not billing_zone:
            return

        cx = det.bbox[0] + det.bbox[2] / 2
        cy = det.bbox[1] + det.bbox[3] / 2
        in_zone = (billing_zone.x <= cx <= billing_zone.x + billing_zone.w and
                   billing_zone.y <= cy <= billing_zone.y + billing_zone.h)

        if in_zone and det.track_id not in self._in_billing_zone:
            self._in_billing_zone.add(det.track_id)
            await self.emitter.emit_zone_event("queue_join", track, det, billing_zone.zone_id)
        elif not in_zone and det.track_id in self._in_billing_zone:
            self._in_billing_zone.discard(det.track_id)

    async def _mock_pipeline(self) -> dict:
        """Simulate pipeline without real video (for testing)."""
        from datetime import timedelta
        import uuid
        logger.info("Running mock pipeline")
        async with self.emitter:
            for i in range(10):
                det = Detection(
                    track_id=i,
                    bbox=(0.1, 0.1, 0.1, 0.3),
                    confidence=0.9,
                    frame_idx=i * 30,
                    timestamp=datetime.utcnow(),
                    camera_id=self.camera_id,
                )
                track = TrackedPerson(
                    track_id=i,
                    camera_id=self.camera_id,
                    first_seen=det.timestamp,
                    last_seen=det.timestamp,
                    visitor_id=str(uuid.uuid4()),
                )
                await self.emitter.emit_entry(track, det)
        return {"entries": 10, "exits": 0, "staff": 0, "zones": 0, "frames": 300}
