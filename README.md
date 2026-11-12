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

## Preparar un municipio

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.catalog              # una vez: catálogo nacional
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.prepare 46078        # Burjassot (OSM + red)
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.prepare 46078 --gtfs-list     # feeds disponibles
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.prepare 46078 --only-transit --gtfs-auto
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m quinzemin.prepare 46250 --gtfs # València (fuentes propias)
```

## Fuentes de datos y licencias

Cada mapa muestra las atribuciones de los datos que usa y la fecha en que se obtuvieron
(`attributions` en el `meta.json` de cada ciudad). Obligaciones de cada licencia:

| Fuente | Datos | Licencia | Qué exige |
|---|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | servicios, red peatonal, teselas del mapa | ODbL 1.0 | "© OpenStreetMap contributors" con enlace a la página de copyright; la base de datos derivada (servicios y red de cada bundle) se comparte con la misma licencia. Las teselas de tile.openstreetmap.org siguen su [política de uso](https://operations.osmfoundation.org/policies/tiles/) (uso ligero, con atribución). |
| [INE](https://www.ine.es/dyngs/AYU/es/index.htm?cid=125) | secciones censales, población (ADRH) | CC BY 4.0 | citar al INE como fuente ("elaboración propia" si se transforma), indicar la fecha de actualización y no sugerir que el INE respalda el uso |
| [Ajuntament de València](https://www.valencia.es/web/gobierno-abierto/cas/datos-abiertos/reutilizacion/politica-de-uso-datos-abiertos/politica-de-uso) | educación, zonas verdes, metro (geoportal), GTFS de la EMT | CC BY 4.0 | "Fuente de los datos: Ajuntament de València – Dades Obertes" y la fecha de obtención |
| [ICV/GVA](https://icv.gva.es) | centros de salud (WFS) | datos abiertos de la Generalitat | citar al Institut Cartogràfic Valencià (conviene confirmar la licencia exacta del servicio WFS) |
| [NAP – MITRAMS](https://nap.transportes.gob.es/licencia-datos) | la mayoría de feeds GTFS de España | licencia de datos abiertos del Ministerio (Ley 37/2007) | citar al MITRAMS como fuente indicando que son datos explotados, conservar la fecha de actualización (vigencia del feed) y no sugerir patrocinio; además, cada conjunto mantiene la licencia de su fuente original |
| Otros feeds GTFS | según el operador (`license_url` de cada feed) | la de cada operador | se enlaza su licencia en las atribuciones |
| [Mobility Database](https://mobilitydatabase.org) | catálogo de feeds GTFS | CC0 (metadatos) | nada; se cita por cortesía |

Esta aplicación no está respaldada por ninguna de las entidades anteriores.
