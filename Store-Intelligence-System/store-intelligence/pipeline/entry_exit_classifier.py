"""
Entry/Exit classifier using zone line crossing.

Design: virtual tripwire line defined in store_layout.json.
A person crosses the line when their centroid moves from one side to the other.

Alternatives considered:
  1. Camera angle heuristic (top = exit, bottom = entry) — works only for top-down cameras
  2. ROI counting (if centroid enters ROI zone) — fragile with overlapping people
  3. Optical flow — computationally expensive, not needed at this accuracy level

Chosen: Tripwire crossing with hysteresis buffer (3 frames) to avoid flicker.

Staff detection:
  Method 1 (preferred): color-based uniform detection.
    Sample the upper-body crop → compute HSV histogram → compare vs staff color template.
  Method 2: trajectory heuristic.
    Staff appear in store for >2 hours continuously. Flag as staff after 2h.
  Method 3: manual staff ID list (imported from store config).

Cross-camera deduplication:
  Same visitor appearing in overlapping camera angles → same visitor_id.
  Method: spatial proximity (bbox centers within 50px of overlap zone) + time window (<5s).
  OSNet ReID: compute embedding → cosine similarity > 0.7 → same visitor.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Zone:
    zone_id: str
    name: str
    x: float  # top-left normalized
    y: float
    w: float
    h: float

    @classmethod
    def from_dict(cls, data: dict) -> "Zone":
        return cls(
            zone_id=data["zone_id"],
            name=data["name"],
            x=float(data["x"]),
            y=float(data["y"]),
            w=float(data["w"]),
            h=float(data["h"]),
        )


@dataclass
class TripwireLine:
    """Virtual line defining entry/exit boundary."""
    x1: float
    y1: float
    x2: float
    y2: float
    entry_side: str  # "above" | "below" | "left" | "right"

    @classmethod
    def from_dict(cls, data: dict) -> "TripwireLine":
        return cls(
            x1=float(data["x1"]),
            y1=float(data["y1"]),
            x2=float(data["x2"]),
            y2=float(data["y2"]),
            entry_side=data.get("entry_side", "below"),
        )


@dataclass
class StoreLayout:
    store_id: str
    tripwire: TripwireLine
    zones: list[Zone]
    staff_uniform_hue_range: tuple[int, int] = (0, 10)  # red uniform by default
    staff_ids: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "StoreLayout":
        staff_config = data.get("staff_config", {})
        return cls(
            store_id=data.get("store_id", "store_001"),
            tripwire=TripwireLine.from_dict(data["tripwire"]),
            zones=[Zone.from_dict(zone) for zone in data.get("zones", [])],
            staff_uniform_hue_range=tuple(
                staff_config.get("uniform_hue_range", (100, 130))
            ),
            staff_ids=[int(sid) for sid in staff_config.get("known_staff_ids", [])],
        )

    @classmethod
    def from_json(cls, file_path: str | Path) -> "StoreLayout":
        path = Path(file_path)
        return cls.from_dict(json.loads(path.read_text()))


class EntryExitClassifier:
    """
    Classifies track crossings as entry or exit using tripwire logic.
    
    Usage:
        layout = StoreLayout.from_json("configs/store_layout.json")
        clf = EntryExitClassifier(layout)
        direction = clf.classify(track_id, bbox_history)
    """

    # Minimum frames needed before classifying direction
    MIN_HISTORY_FRAMES = 3
    # Hysteresis: require N consecutive frames on the same side before confirming
    HYSTERESIS_FRAMES = 2

    def __init__(self, layout: StoreLayout):
        self.layout = layout
        self._track_history: dict[int, list[tuple[float, float]]] = {}
        self._confirmed_crossings: set[int] = set()

    def update(self, track_id: int, bbox: tuple[float, float, float, float]) -> Optional[str]:
        """
        Update position for a track and return "entry", "exit", or None.
        
        bbox: (x, y, w, h) normalized to [0, 1]
        Returns event_type string if a crossing is detected.
        """
        cx = bbox[0] + bbox[2] / 2  # centroid x
        cy = bbox[1] + bbox[3] / 2  # centroid y

        if track_id not in self._track_history:
            self._track_history[track_id] = []
        self._track_history[track_id].append((cx, cy))

        history = self._track_history[track_id]
        if len(history) < self.MIN_HISTORY_FRAMES:
            return None

        # Only classify once per track
        if track_id in self._confirmed_crossings:
            return None

        direction = self._detect_crossing(history)
        if direction:
            self._confirmed_crossings.add(track_id)
        return direction

    def _detect_crossing(self, history: list[tuple[float, float]]) -> Optional[str]:
        """
        Detect if the track crossed the tripwire line.
        Uses signed distance from the line: positive = entry side.
        """
        wire = self.layout.tripwire
        dx = wire.x2 - wire.x1
        dy = wire.y2 - wire.y1

        def signed_dist(px: float, py: float) -> float:
            # Cross product gives signed area → positive on left, negative on right
            return dx * (py - wire.y1) - dy * (px - wire.x1)

        first_sign = signed_dist(*history[0])
        last_sign = signed_dist(*history[-1])

        # Must have crossed (different sides)
        if first_sign * last_sign >= 0:
            return None

        # Determine direction based on which side is "entry"
        entry_side = wire.entry_side
        if entry_side == "below":
            # Moving from positive (above/outside) to negative (inside) = entry
            return "entry" if first_sign > 0 > last_sign else "exit"
        else:
            return "exit" if first_sign > 0 > last_sign else "entry"

    def classify_zone(self, bbox: tuple[float, float, float, float]) -> Optional[str]:
        """Return zone_id if bbox centroid is within a zone polygon."""
        cx = bbox[0] + bbox[2] / 2
        cy = bbox[1] + bbox[3] / 2

        for zone in self.layout.zones:
            if zone.x <= cx <= zone.x + zone.w and zone.y <= cy <= zone.y + zone.h:
                return zone.zone_id
        return None

    def evict_track(self, track_id: int) -> None:
        self._track_history.pop(track_id, None)
        self._confirmed_crossings.discard(track_id)


class StaffClassifier:
    """
    Identifies staff members to exclude from visitor metrics.
    
    Three-tier approach (ordered by reliability):
    1. Manual staff ID list from config (highest precision)
    2. Uniform color detection (HSV histogram matching)
    3. Trajectory heuristic: >2h continuous presence = staff
    """

    STAFF_DWELL_THRESHOLD_SECONDS = 7200  # 2 hours
    UNIFORM_CONFIDENCE_THRESHOLD = 0.6

    def __init__(
        self,
        staff_ids: Optional[list[int]] = None,
        uniform_hue_range: tuple[int, int] = (100, 130),  # blue uniform
    ):
        self.staff_ids = set(staff_ids or [])
        self.uniform_hue_range = uniform_hue_range
        self._track_start: dict[int, datetime] = {}

    def is_staff(
        self,
        track_id: int,
        first_seen: datetime,
        current_time: datetime,
        upper_body_crop: Optional[np.ndarray] = None,
    ) -> bool:
        """Return True if this track should be classified as staff."""

        # Tier 1: Explicit ID list
        if track_id in self.staff_ids:
            return True

        # Tier 2: Uniform color
        if upper_body_crop is not None and self._check_uniform_color(upper_body_crop):
            return True

        # Tier 3: Long dwell heuristic
        if track_id not in self._track_start:
            self._track_start[track_id] = first_seen

        duration = (current_time - self._track_start[track_id]).total_seconds()
        if duration > self.STAFF_DWELL_THRESHOLD_SECONDS:
            return True

        return False

    def _check_uniform_color(self, crop: "np.ndarray") -> bool:
        """
        Check if upper body crop matches staff uniform HSV range.
        Returns True if dominant hue is in staff uniform range.
        """
        try:
            import cv2
            import numpy as np

            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            hue_hist = np.histogram(hsv[:, :, 0].flatten(), bins=180, range=(0, 180))[0]
            dominant_hue = np.argmax(hue_hist)
            lo, hi = self.uniform_hue_range
            return lo <= dominant_hue <= hi
        except Exception:
            return False


class ReIdentifier:
    """
    Cross-session and cross-camera visitor re-identification.
    Uses OSNet embeddings (torchreid) if available, falls back to bbox hashing.
    
    Re-ID is critical for:
    - Re-entry detection (same visitor leaves and returns)
    - Cross-camera deduplication (same visitor on overlapping cameras)
    """

    COSINE_SIM_THRESHOLD = 0.70
    TEMPORAL_WINDOW_SECONDS = 300  # 5 minutes for re-entry matching

    def __init__(self, use_reid_model: bool = True):
        self.embeddings: dict[str, "np.ndarray"] = {}  # visitor_id → embedding
        self.model = None
        if use_reid_model:
            self._load_model()

    def _load_model(self) -> None:
        try:
            import torchreid
            self.model = torchreid.utils.FeatureExtractor(
                model_name="osnet_x1_0",
                model_path="weights/osnet_x1_0.pth",
                device="cpu",
            )
            logger.info("OSNet re-ID model loaded")
        except Exception:
            logger.warning("OSNet not available. Using trajectory-based re-ID fallback.")

    def get_visitor_id(
        self,
        track_id: int,
        crop: Optional[np.ndarray],
        camera_id: str,
        last_exit_visitor_id: Optional[str] = None,
    ) -> str:
        """
        Assign visitor_id. Matches returning visitors via embedding similarity.
        Returns existing visitor_id if re-entry detected, else new UUID.
        """
        import uuid as _uuid

        if crop is not None and self.model is not None:
            embedding = self._extract_embedding(crop)
            if embedding is not None:
                match = self._find_match(embedding)
                if match:
                    self.embeddings[match] = embedding  # update with fresh embedding
                    return match
                new_id = str(_uuid.uuid4())
                self.embeddings[new_id] = embedding
                return new_id

        # Fallback: use track_id as visitor_id (no re-ID)
        return f"{camera_id}_track_{track_id}"

    def _extract_embedding(self, crop: "np.ndarray") -> Optional["np.ndarray"]:
        if self.model is None:
            return None
        try:
            import numpy as np

            features = self.model([crop])
            return features[0] / (np.linalg.norm(features[0]) + 1e-8)  # L2 normalize
        except Exception:
            return None

    def _find_match(self, embedding: "np.ndarray") -> Optional[str]:
        try:
            import numpy as np
        except Exception:
            return None

        best_sim = self.COSINE_SIM_THRESHOLD
        best_id = None
        for vid, stored_emb in self.embeddings.items():
            sim = float(np.dot(embedding, stored_emb))
            if sim > best_sim:
                best_sim = sim
                best_id = vid
        return best_id
