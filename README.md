# quinzemin-web

Aplicación web de **quinzemin**: para cada sección censal de un municipio español calcula el acceso a
8 servicios básicos (ciudad de los 15 minutos) a pie, por la red peatonal o en transporte público, y lo
muestra en un mapa coroplético con puntuación 0–100 y prioridad (habitantes × déficit).

> En construcción. Plan y decisiones: [HANDOFF_WEB.md](HANDOFF_WEB.md).

## Desarrollo

```bash
python -m venv .venv                       # Python 3.13
.venv/Scripts/python -m pip install -e ".[prepare,api,viz,dev]"
.venv/Scripts/python -m pytest
```

## Atribuciones

Datos © OpenStreetMap contributors (ODbL) · Población y secciones censales © INE · Horarios de
transporte: cada fuente GTFS indicada en el mapa.
