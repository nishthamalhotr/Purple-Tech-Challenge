"""
Detection layer: YOLOv8 + ByteTrack.

Architecture decision:
  YOLOv8n (nano) for detection — best speed/accuracy tradeoff at 30+ FPS on CPU.
  ByteTrack for tracking — handles occlusion better than DeepSORT at similar compute cost.
  OSNet (torchreid) for re-ID — lightweight ReID backbone for cross-camera visitor matching.

Why YOLOv8 over YOLOv9 / RT-DETR:
  - Mature ecosystem, well-documented API
  - Nano variant runs on CPU at 15–30 FPS (30 FPS with GPU)
  - ByteTrack integration via Ultralytics built-in tracking
  - YOLOv9 shows marginal mAP gain (~1%) but slower inference
  - RT-DETR is transformer-based — overkill for person detection at store scale

Why ByteTrack over DeepSORT:
  - ByteTrack uses ALL detections (not just high-confidence ones), recovering occluded persons
  - No ReID dependency in the tracker itself → simpler pipeline
  - DeepSORT's ReID step adds 20ms latency per frame on CPU
  - StrongSORT is slightly better but slower; ByteTrack is the sweet spot
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    """Single person detection from a CCTV frame."""
    track_id: int
    bbox: tuple[float, float, float, float]  # x, y, w, h (normalized 0–1)
    confidence: float
    frame_idx: int
    timestamp: datetime
    camera_id: str


@dataclass
class TrackedPerson:
    """Person being tracked across frames."""
    track_id: int
    camera_id: str
    first_seen: datetime
    last_seen: datetime
    bbox_history: list[tuple[float, float, float, float]] = field(default_factory=list)
    is_staff: bool = False
    visitor_id: Optional[str] = None  # assigned after re-ID matching


class PersonDetector:
    """
    YOLOv8 + ByteTrack person detection and tracking.
    
    Falls back to a lightweight mock mode when ultralytics is not installed
    (useful for testing the pipeline without GPU/model weights).
    """

    PERSON_CLASS_ID = 0  # COCO class 0 = person
    MIN_CONFIDENCE = 0.4   # below this, detection is discarded
    STAFF_UNIFORM_THRESHOLD = 0.7  # confidence threshold for uniform-based staff classification

    def __init__(
        self,
        model_size: str = "yolov8n.pt",
        device: str = "cpu",
        mock_mode: bool = False,
    ):
        self.model_size = model_size
        self.device = device
        self.mock_mode = mock_mode
        self.model = None
        self._active_tracks: dict[int, TrackedPerson] = {}

        if not mock_mode:
            self._load_model()

    def _load_model(self) -> None:
        try:
            from ultralytics import YOLO
            self.model = YOLO(self.model_size)
            logger.info(f"YOLOv8 model loaded: {self.model_size} on {self.device}")
        except ImportError:
            logger.warning("ultralytics not installed. Switching to mock mode.")
            self.mock_mode = True

    def detect_frame(
        self,
        frame: np.ndarray,
        camera_id: str,
        frame_idx: int,
        timestamp: datetime,
    ) -> list[Detection]:
        """
        Run YOLOv8 + ByteTrack on a single frame.
        Returns tracked person detections.
        """
        if self.mock_mode:
            return self._mock_detections(camera_id, frame_idx, timestamp)

        # Ultralytics tracking: handles ByteTrack internally
        results = self.model.track(
            frame,
            persist=True,
            classes=[self.PERSON_CLASS_ID],
            conf=self.MIN_CONFIDENCE,
            tracker="bytetrack.yaml",
            device=self.device,
            verbose=False,
        )

        detections = []
        h, w = frame.shape[:2]

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                if box.id is None:
                    continue  # untracked detection

                track_id = int(box.id.item())
                conf = float(box.conf.item())
                x1, y1, x2, y2 = box.xyxy[0].tolist()

                # Normalize to 0–1
                bbox = (x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h)

                det = Detection(
                    track_id=track_id,
                    bbox=bbox,
                    confidence=conf,
                    frame_idx=frame_idx,
                    timestamp=timestamp,
                    camera_id=camera_id,
                )
                detections.append(det)
                self._update_track(det)

        return detections

    def _update_track(self, det: Detection) -> None:
        if det.track_id not in self._active_tracks:
            self._active_tracks[det.track_id] = TrackedPerson(
                track_id=det.track_id,
                camera_id=det.camera_id,
                first_seen=det.timestamp,
                last_seen=det.timestamp,
            )
        track = self._active_tracks[det.track_id]
        track.last_seen = det.timestamp
        track.bbox_history.append(det.bbox)

    def _mock_detections(
        self,
        camera_id: str,
        frame_idx: int,
        timestamp: datetime,
    ) -> list[Detection]:
        """Generate synthetic detections for testing."""
        import random
        random.seed(frame_idx % 100)
        count = random.randint(0, 4)
        detections = []
        for i in range(count):
            detections.append(Detection(
                track_id=i + 1,
                bbox=(0.1 + i * 0.2, 0.1, 0.1, 0.5),
                confidence=0.85 + random.random() * 0.1,
                frame_idx=frame_idx,
                timestamp=timestamp,
                camera_id=camera_id,
            ))
        return detections

    def get_active_tracks(self) -> dict[int, TrackedPerson]:
        return dict(self._active_tracks)

    def evict_stale_tracks(self, current_time: datetime, timeout_seconds: float = 5.0) -> list[int]:
        """Remove tracks not seen for > timeout_seconds. Returns evicted IDs."""
        evicted = [
            tid for tid, t in self._active_tracks.items()
            if (current_time - t.last_seen).total_seconds() > timeout_seconds
        ]
        for tid in evicted:
            del self._active_tracks[tid]
        return evicted
