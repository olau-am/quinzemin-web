import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _config_dir() -> Path:
    """
    Carpeta config/ (YAML de ciudades y plantilla OSM). No va dentro del paquete instalado:
    QM_CONFIG_DIR si está definida; si no, junto al código fuente (desarrollo) o en el directorio
    de trabajo (contenedor: /app/config).
    """
    if os.environ.get("QM_CONFIG_DIR"):
        return Path(os.environ["QM_CONFIG_DIR"])
    for cand in (ROOT / "config", Path.cwd() / "config", Path("/app/config")):
        if (cand / "osm_services.yaml").exists():
            return cand
    return ROOT / "config"


CONFIG_DIR = _config_dir()
CITIES_DIR = CONFIG_DIR / "cities"
DEFAULT_CITY = "46250"   # València


def city_config_path(ine: str = DEFAULT_CITY) -> Path:
    return CITIES_DIR / f"{ine}.yaml"


def load(path: str | Path | None = None) -> dict:
    """Carga un YAML de configuración (por defecto, el de València)."""
    with open(path or city_config_path(), encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_attr(cfg: dict) -> str:
    """Construye el string HTML de atribución para el control de Leaflet."""
    entries = cfg.get("city", {}).get("attributions", [])
    if not entries:
        return "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors"
    parts = []
    for a in entries:
        label = a.get("label", "")
        url   = a.get("url")
        parts.append(f"<a href='{url}'>{label}</a>" if url else label)
    return " | ".join(parts)
