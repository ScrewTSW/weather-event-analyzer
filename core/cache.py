import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

APP_NAME = "weather-event-analyzer"


def default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / APP_NAME


class Cache:
    def __init__(self, cache_dir: str, max_size_gb: float = 5.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = int(max_size_gb * 1024 * 1024 * 1024)
        self.manifest_path = self.cache_dir / "manifest.json"

    def load_manifest(self) -> dict:
        if self.manifest_path.exists():
            with open(self.manifest_path) as f:
                return json.load(f)
        return {}

    def _save_manifest(self, manifest: dict):
        tmp = self.manifest_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(manifest, f)
        tmp.replace(self.manifest_path)

    def store(self, key: str, data: bytes, source_url: str = "") -> Path:
        path = self.cache_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        manifest = self.load_manifest()
        manifest[key] = {
            "source_url": source_url,
            "size": len(data),
            "acquired": time.time(),
        }
        self._save_manifest(manifest)
        return path

    def get(self, key: str) -> Optional[Path]:
        path = self.cache_dir / key
        if path.exists():
            return path
        manifest = self.load_manifest()
        if key in manifest:
            del manifest[key]
            self._save_manifest(manifest)
        return None

    def total_size(self) -> int:
        manifest = self.load_manifest()
        return sum(entry["size"] for entry in manifest.values())

    def enforce_limit(self):
        if self.total_size() <= self.max_size_bytes:
            return
        manifest = self.load_manifest()
        sorted_entries = sorted(manifest.items(), key=lambda x: x[1]["acquired"])
        while self.total_size() > self.max_size_bytes and sorted_entries:
            key, _entry = sorted_entries.pop(0)
            path = self.cache_dir / key
            if path.exists():
                path.unlink()
            del manifest[key]
            self._save_manifest(manifest)
