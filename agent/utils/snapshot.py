"""CPU snapshot persistence for ServerPulse Agent – shared across all platforms."""

import json
import os

from models.limits import STATE_ENCODING


class CpuSnapStore:
    def __init__(self, snap_file_path: str):
        self.snap_file = snap_file_path

    def load(self):
        try:
            with open(self.snap_file, "r", encoding=STATE_ENCODING) as f:
                data = json.load(f)
            return data.get("fields"), data.get("ts")
        except Exception:
            return None

    def save(self, fields, ts):
        try:
            dir_path = os.path.dirname(self.snap_file)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path)
            with open(self.snap_file, "w", encoding=STATE_ENCODING) as f:
                json.dump({"fields": fields, "ts": ts}, f)
            return True
        except Exception:
            return False
