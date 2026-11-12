"""
Almacenamiento de datos con las mismas rutas en local y en Google Cloud Storage:

  spain/                 catálogo nacional (secciones por provincia, población, municipios)
  cities/{ine}/          bundle de cada ciudad preparada
  runs/{ine}/{hash}.json resultados cacheados (fase 3)
  jobs/{id}.json         estado de las preparaciones (fase 3)

QM_STORAGE=local (por defecto, carpeta QM_DATA_DIR o ./data) | gcs (bucket QM_BUCKET).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path


class Storage:
    def read_bytes(self, key: str) -> bytes: ...
    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> list[str]: ...
    def create_if_absent(self, key: str, data: bytes) -> bool:
        """Escritura atómica solo si no existe (lock). True si la ha creado."""

    def local_path(self, key: str) -> Path:
        """Ruta local del objeto (en GCS se descarga a una caché temporal)."""

    def upload_file(self, key: str, path: str | Path) -> None:
        self.write_bytes(key, Path(path).read_bytes())

    # Atajos
    def read_json(self, key: str):
        return json.loads(self.read_bytes(key).decode("utf-8"))

    def write_json(self, key: str, obj, **kw) -> None:
        self.write_bytes(key, json.dumps(obj, ensure_ascii=False, **kw).encode("utf-8"), "application/json")


class LocalStorage(Storage):
    def __init__(self, root: str | Path = "data"):
        self.root = Path(root)

    def _p(self, key: str) -> Path:
        return self.root / key

    def read_bytes(self, key):
        return self._p(key).read_bytes()

    def write_bytes(self, key, data, content_type=None):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def upload_file(self, key, path):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if Path(path).resolve() != p.resolve():
            shutil.copyfile(path, p)

    def exists(self, key):
        return self._p(key).exists()

    def list(self, prefix):
        base = self._p(prefix)
        if base.is_file():
            return [prefix]
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file())

    def create_if_absent(self, key, data):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "xb") as f:
                f.write(data)
            return True
        except FileExistsError:
            return False

    def local_path(self, key):
        return self._p(key)


class GCSStorage(Storage):
    def __init__(self, bucket: str, cache_dir: str | Path | None = None):
        from google.cloud import storage

        self._bucket = storage.Client().bucket(bucket)
        self._cache = Path(cache_dir or Path(tempfile.gettempdir()) / "quinzemin-cache")

    def read_bytes(self, key):
        return self._bucket.blob(key).download_as_bytes()

    def write_bytes(self, key, data, content_type=None):
        self._bucket.blob(key).upload_from_string(data, content_type=content_type)

    def upload_file(self, key, path):
        self._bucket.blob(key).upload_from_filename(str(path))

    def exists(self, key):
        return self._bucket.blob(key).exists()

    def list(self, prefix):
        return sorted(b.name for b in self._bucket.client.list_blobs(self._bucket, prefix=prefix))

    def create_if_absent(self, key, data):
        from google.api_core.exceptions import PreconditionFailed

        try:
            self._bucket.blob(key).upload_from_string(data, if_generation_match=0)
            return True
        except PreconditionFailed:
            return False

    def local_path(self, key):
        p = self._cache / key
        blob = self._bucket.blob(key)
        blob.reload()
        stamp = p.with_name(p.name + ".gen")
        if not p.exists() or not stamp.exists() or stamp.read_text() != str(blob.generation):
            p.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(p))
            stamp.write_text(str(blob.generation))
        return p


def from_env() -> Storage:
    if os.environ.get("QM_STORAGE", "local") == "gcs":
        return GCSStorage(os.environ["QM_BUCKET"])
    return LocalStorage(os.environ.get("QM_DATA_DIR", "data"))
