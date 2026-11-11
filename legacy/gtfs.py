"""
Motor de accesibilidad por transporte público usando datos GTFS.

Modelo frequency-based (sin dependencia de horario): el tiempo de espera en
cada parada de origen se estima como headway/2, donde headway = 16h / nº de
expediciones/día. Una vez embarcado, los trasbordos se contabilizan solo como
tiempo de viaje entre paradas (sin espera adicional). Esto da un tiempo de
viaje "típico" independiente de la hora de salida.
"""
import heapq
import zipfile
from collections import defaultdict

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPoint, Point
from shapely import STRtree


def _parse_hms(t: str) -> int:
    """HH:MM:SS → segundos. Acepta valores >24:00 (viajes nocturnos GTFS)."""
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


class TransitGraph:
    def __init__(self, graph, wait_s, geoms, ids, tree, names, sid_idx, utm_crs):
        self._graph   = graph     # {stop_id: [(neighbor_id, travel_s)]}
        self._wait    = wait_s    # {stop_id: espera_media_s}
        self._geoms   = geoms     # [shapely.Point] en UTM
        self._ids     = ids       # [stop_id str]
        self._tree    = tree      # STRtree sobre _geoms
        self._names   = names     # {stop_id: stop_name}
        self._sid_idx = sid_idx   # {stop_id: índice en _geoms/_ids}
        self._utm     = utm_crs

    @classmethod
    def load(cls, sources: list[dict], utm_crs) -> "TransitGraph":
        """
        Carga y combina uno o varios feeds GTFS en un único grafo.

        sources: lista de dicts con claves:
            id    — prefijo corto sin espacios (ej. "metro", "emt")
            label — nombre legible para logs
            path  — ruta local al ZIP GTFS

        Cada stop_id y trip_id se prefiján con «id_» para evitar colisiones
        entre redes distintas (p. ej. la parada "1" de metro ≠ la parada "1" de EMT).
        """
        all_stops_dfs      = []
        all_stop_times_dfs = []

        for src in sources:
            src_id = src["id"]
            label  = src.get("label", src_id)
            path   = src["path"]
            prefix = src_id + "_"

            print(f"  Leyendo {label}: {path}")
            with zipfile.ZipFile(path) as z:
                stops      = pd.read_csv(z.open("stops.txt"),      dtype=str).fillna("")
                stop_times = pd.read_csv(z.open("stop_times.txt"), dtype=str).fillna("")

            stops["stop_id"]      = prefix + stops["stop_id"]
            stop_times["stop_id"] = prefix + stop_times["stop_id"]
            stop_times["trip_id"] = prefix + stop_times["trip_id"]

            all_stops_dfs.append(stops)
            all_stop_times_dfs.append(stop_times)

        stops      = pd.concat(all_stops_dfs,      ignore_index=True)
        stop_times = pd.concat(all_stop_times_dfs, ignore_index=True)

        # Paradas → geometría UTM
        stops["_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
        stops["_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
        stops = stops.dropna(subset=["_lat", "_lon"]).reset_index(drop=True)

        stops_gdf = gpd.GeoDataFrame(
            stops,
            geometry=[Point(r["_lon"], r["_lat"]) for _, r in stops.iterrows()],
            crs="EPSG:4326",
        ).to_crs(utm_crs)

        ids     = stops_gdf["stop_id"].tolist()
        geoms   = list(stops_gdf.geometry)
        sid_idx = {sid: i for i, sid in enumerate(ids)}
        names   = {}
        if "stop_name" in stops_gdf.columns:
            names = dict(zip(stops_gdf["stop_id"], stops_gdf["stop_name"].fillna("")))
        tree = STRtree(geoms)

        # Grafo de tránsito y conteo de expediciones por parada
        print("  Construyendo grafo de paradas...")
        graph   = defaultdict(list)
        n_trips = defaultdict(int)

        stop_times["_dep"] = stop_times["departure_time"].apply(
            lambda t: _parse_hms(t) if t else None
        )
        stop_times["_arr"] = stop_times["arrival_time"].apply(
            lambda t: _parse_hms(t) if t else None
        )
        stop_times["_seq"] = pd.to_numeric(stop_times["stop_sequence"], errors="coerce")
        stop_times = stop_times.dropna(subset=["_dep", "_arr", "_seq"])

        for _, grp in stop_times.groupby("trip_id"):
            grp = grp.sort_values("_seq").reset_index(drop=True)
            for i in range(len(grp) - 1):
                sid_a = grp.at[i, "stop_id"]
                sid_b = grp.at[i + 1, "stop_id"]
                tt    = int(grp.at[i + 1, "_arr"] - grp.at[i, "_dep"])
                if 0 < tt <= 3600 and sid_a in sid_idx and sid_b in sid_idx:
                    graph[sid_a].append((sid_b, tt))
            for sid in grp["stop_id"]:
                n_trips[sid] += 1

        # Espera media = headway / 2, asumiendo 16h de servicio
        day_s  = 16 * 3600
        wait_s = {
            sid: max(30, day_s / max(1, 2 * n_trips[sid]))
            for sid in ids
        }

        n_edges = sum(len(v) for v in graph.values())
        print(f"  Grafo combinado: {len(ids)} paradas, {n_edges} aristas "
              f"({len(sources)} {'fuente' if len(sources)==1 else 'fuentes'})")
        return cls(dict(graph), wait_s, geoms, ids, tree, names, sid_idx, utm_crs)

    # ------------------------------------------------------------------
    # Consultas espaciales
    # ------------------------------------------------------------------

    def _nearby(self, point, max_m: float) -> list[int]:
        """Índices de paradas dentro de max_m metros del punto (STRtree + filtro exacto)."""
        candidates = self._tree.query(point.buffer(max_m))
        return [i for i in candidates if point.distance(self._geoms[i]) <= max_m]

    # ------------------------------------------------------------------
    # Algoritmo principal
    # ------------------------------------------------------------------

    def reachable_from(
        self, centroid, budget_s: float, walk_mps: float, max_walk_m: float
    ) -> dict:
        """
        Dijkstra desde un centroide censal.
        Devuelve {stop_id: tiempo_gastado_s} para todas las paradas
        alcanzables dentro del presupuesto.
        """
        heap = []
        for i in self._nearby(centroid, max_walk_m):
            sid    = self._ids[i]
            walk_t = centroid.distance(self._geoms[i]) / walk_mps
            total  = walk_t + self._wait.get(sid, 120)   # espera al embarcar
            if total < budget_s:
                heapq.heappush(heap, (total, sid))

        best = {}
        while heap:
            spent, sid = heapq.heappop(heap)
            if sid in best:
                continue
            best[sid] = spent
            for neighbor, travel_t in self._graph.get(sid, []):
                new_spent = spent + travel_t   # sin espera adicional en tránsito
                if new_spent < budget_s and neighbor not in best:
                    heapq.heappush(heap, (new_spent, neighbor))
        return best

    def service_min_time(
        self,
        reachable: dict,
        service_gdf,
        name_col: str,
        walk_mps: float,
        budget_s: float,
        max_walk_m: float,
    ) -> tuple:
        """
        Dado el dict de paradas alcanzables desde un centroide, busca el
        servicio más cercano (tiempo total mínimo) dentro del presupuesto.
        Devuelve (tiempo_total_s, nombre_del_servicio) o (None, "—").
        """
        best_t, best_name = None, "—"
        for _, row in service_gdf.iterrows():
            geom = row.geometry
            pt   = geom.centroid if geom.geom_type != "Point" else geom
            for i in self._nearby(pt, max_walk_m):
                sid = self._ids[i]
                if sid not in reachable:
                    continue
                walk_t = geom.distance(self._geoms[i]) / walk_mps
                total  = reachable[sid] + walk_t
                if total <= budget_s and (best_t is None or total < best_t):
                    best_t    = total
                    best_name = (
                        str(row[name_col]) if name_col in service_gdf.columns else "—"
                    )
        return best_t, best_name

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def reachable_hull(self, reachable: dict, buffer_m: float = 150):
        """Casco convexo de las paradas alcanzables, ampliado buffer_m metros."""
        pts = [self._geoms[self._sid_idx[sid]] for sid in reachable if sid in self._sid_idx]
        if len(pts) < 3:
            return None
        return MultiPoint(pts).convex_hull.buffer(buffer_m)

    @property
    def stops_count(self) -> int:
        return len(self._ids)

    def stops_geodataframe(self) -> gpd.GeoDataFrame:
        """GeoDataFrame con todas las paradas en WGS84."""
        gdf = gpd.GeoDataFrame(
            {
                "stop_id":   self._ids,
                "stop_name": [self._names.get(s, "") for s in self._ids],
            },
            geometry=self._geoms,
            crs=self._utm,
        )
        return gdf.to_crs(epsg=4326)
