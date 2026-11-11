import yaml
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.yaml"   # quinzemin-web: copia congelada en legacy/


def load() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
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
