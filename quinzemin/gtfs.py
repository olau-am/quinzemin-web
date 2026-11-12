"""
Motor de accesibilidad por transporte público usando datos GTFS.

Modelo frequency-based (sin dependencia de horario): el tiempo de espera en
cada parada de origen se estima como headway/2, donde headway = 16h / nº de
expediciones/día. Una vez embarcado, los trasbordos se contabilizan solo como
tiempo de viaje entre paradas (sin espera adicional). Esto da un tiempo de
viaje "típico" independiente de la hora de salida.

Mismo modelo que quinzemin/src/gtfs.py (legacy/gtfs.py), reescrito para que sea rápido:
  - el grafo se construye con operaciones vectorizadas y se queda con la arista más
    rápida entre cada par de paradas (las demás no cambian ningún camino mínimo);
  - reachable_matrix(): un Dijkstra de scipy para todos los centroides a la vez, con un
    nodo virtual por centroide unido a sus paradas (peso = andar + esperar);
  - service_min_times(): los pares (elemento del servicio, parada a ≤ max_walk_m) se
    calculan una sola vez por servicio y, por área, se toma el mínimo con numpy.
    Antes era iterrows de todos los elementos × búsqueda espacial para cada área (16–47 min).
"""
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely import STRtree

DAY_S = 16 * 3600        # horas de servicio supuestas para el headway
MAX_HOP_S = 3600         # tramos entre paradas consecutivas más largos se descartan


def _strip(df: pd.DataFrame) -> pd.DataFrame:
    """Quita espacios al principio y al final de todos los valores de texto."""
    for c in df.columns:
        if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
            df[c] = df[c].str.strip()
    return df


def _hms_seconds(s: pd.Series) -> pd.Series:
    """'HH:MM:SS' → segundos (admite > 24:00); vacío → NaN."""
    s = s.str.strip()
    parts = s[s != ""].str.split(":", expand=True).astype(int)
    out = pd.Series(np.nan, index=s.index)
    out[parts.index] = parts[0] * 3600 + parts[1] * 60 + parts[2]
    return out


class TransitGraph:
    def __init__(self, stop_ids, stop_names, stop_xy, wait_s, edge_a, edge_b, edge_t, utm_crs):
        self.stop_ids   = np.asarray(stop_ids, dtype=object)   # stop_id con prefijo de feed
        self.stop_names = np.asarray(stop_names, dtype=object)
        self.stop_xy    = np.asarray(stop_xy, dtype=float)      # (n, 2) UTM
        self.wait_s     = np.asarray(wait_s, dtype=float)       # espera media al embarcar
        self.edge_a     = np.asarray(edge_a, dtype=np.int64)    # aristas dirigidas a → b
        self.edge_b     = np.asarray(edge_b, dtype=np.int64)
        self.edge_t     = np.asarray(edge_t, dtype=float)       # segundos
        self.utm_crs    = utm_crs
        self._init_index()

    def _init_index(self):
        self._geoms = shapely.points(self.stop_xy)
        self._tree  = STRtree(self._geoms)

    def __getstate__(self):          # pickle (transit.pkl del bundle) sin el STRtree
        st = self.__dict__.copy()
        st.pop("_geoms", None)
        st.pop("_tree", None)
        return st

    def __setstate__(self, st):
        self.__dict__.update(st)
        self._init_index()

    @property
    def n_stops(self) -> int:
        return len(self.stop_ids)

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, sources: list[dict], utm_crs, clip=None) -> "TransitGraph":
        """
        Carga y combina uno o varios feeds GTFS en un único grafo.

        sources: lista de dicts con claves id (prefijo sin espacios, p. ej. "metro"),
        label (para logs) y path (ZIP GTFS). Los stop_id y trip_id se prefijan con «id_»
        para evitar colisiones entre redes.
        clip: polígono en EPSG:4326; si se da, solo se conservan las paradas de dentro (feeds
        regionales o nacionales). Un viaje que sale y vuelve a entrar se une por sus paradas
        interiores con el tiempo real entre ellas; solo se pierden trasbordos fuera de la zona.
        """
        stops_l, times_l = [], []
        for src in sources:
            prefix = src["id"] + "_"
            print(f"  Leyendo {src.get('label', src['id'])}: {src['path']}")
            with zipfile.ZipFile(src["path"]) as z:
                def member(name, z=z):
                    return next(n for n in z.namelist() if n.split("/")[-1] == name)
                stops = _strip(pd.read_csv(z.open(member("stops.txt")), dtype=str,
                                           encoding="utf-8-sig").rename(columns=str.strip)).fillna("")
                if clip is not None:
                    lat = pd.to_numeric(stops["stop_lat"], errors="coerce")
                    lon = pd.to_numeric(stops["stop_lon"], errors="coerce")
                    inside = shapely.contains_xy(clip, lon.fillna(0).to_numpy(), lat.fillna(0).to_numpy())
                    stops = stops[inside & lat.notna().to_numpy()]
                keep = set(stops["stop_id"]) if clip is not None else None
                cols = ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]
                parts = []
                # Por trozos (los feeds grandes no caben enteros en memoria). Algunos feeds (Renfe)
                # rellenan con espacios las cabeceras y los valores: se recortan.
                with z.open(member("stop_times.txt")) as fh:
                    for chunk in pd.read_csv(fh, dtype=str, encoding="utf-8-sig", chunksize=1_000_000,
                                             usecols=lambda c: c.strip() in cols):
                        chunk = _strip(chunk.rename(columns=str.strip))
                        if keep is not None:
                            chunk = chunk[chunk["stop_id"].isin(keep)]
                        parts.append(chunk.fillna(""))
                times = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
            if clip is not None:
                print(f"    recorte: {len(stops)} paradas y {len(times)} horarios dentro de la zona")
            stops["stop_id"] = prefix + stops["stop_id"]
            times["stop_id"] = prefix + times["stop_id"]
            times["trip_id"] = prefix + times["trip_id"]
            stops_l.append(stops)
            times_l.append(times)
        stops = pd.concat(stops_l, ignore_index=True)
        times = pd.concat(times_l, ignore_index=True)

        # Paradas → UTM
        lat = pd.to_numeric(stops["stop_lat"], errors="coerce")
        lon = pd.to_numeric(stops["stop_lon"], errors="coerce")
        keep = lat.notna() & lon.notna()
        stops, lat, lon = stops[keep].reset_index(drop=True), lat[keep], lon[keep]
        pts = gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs="EPSG:4326").to_crs(utm_crs)
        stop_xy = np.column_stack([pts.x.to_numpy(), pts.y.to_numpy()])
        stop_ids = stops["stop_id"].to_numpy()
        names = stops["stop_name"].to_numpy() if "stop_name" in stops else np.full(len(stops), "")
        sid_idx = pd.Series(np.arange(len(stop_ids)), index=stop_ids)
        sid_idx = sid_idx[~sid_idx.index.duplicated(keep="last")]

        # Horarios
        print("  Construyendo grafo de paradas...")
        times["_dep"] = _hms_seconds(times["departure_time"])
        times["_arr"] = _hms_seconds(times["arrival_time"])
        times["_seq"] = pd.to_numeric(times["stop_sequence"], errors="coerce")
        times = times.dropna(subset=["_dep", "_arr", "_seq"])
        times = times.sort_values(["trip_id", "_seq"], kind="mergesort").reset_index(drop=True)

        # Tramos entre paradas consecutivas del mismo viaje
        same = (times["trip_id"].to_numpy()[1:] == times["trip_id"].to_numpy()[:-1])
        a = times["stop_id"].to_numpy()[:-1][same]
        b = times["stop_id"].to_numpy()[1:][same]
        tt = (times["_arr"].to_numpy()[1:] - times["_dep"].to_numpy()[:-1])[same].astype(np.int64)
        ia = sid_idx.reindex(a).to_numpy()
        ib = sid_idx.reindex(b).to_numpy()
        ok = (tt > 0) & (tt <= MAX_HOP_S) & ~np.isnan(ia) & ~np.isnan(ib)
        edges = pd.DataFrame({"a": ia[ok].astype(np.int64), "b": ib[ok].astype(np.int64), "t": tt[ok]})
        n_raw = len(edges)
        edges = edges.groupby(["a", "b"], as_index=False)["t"].min()

        # Espera media = headway / 2 (expediciones = filas de stop_times por parada)
        n_trips = times["stop_id"].value_counts().reindex(stop_ids, fill_value=0).to_numpy()
        wait = np.maximum(30, DAY_S / np.maximum(1, 2 * n_trips))

        print(f"  Grafo combinado: {len(stop_ids)} paradas, {len(edges)} aristas "
              f"({n_raw} tramos; {len(sources)} {'fuente' if len(sources) == 1 else 'fuentes'})")
        return cls(stop_ids, names, stop_xy, wait, edges["a"], edges["b"], edges["t"], utm_crs)

    # ------------------------------------------------------------------
    # Algoritmo principal
    # ------------------------------------------------------------------

    def reachable_matrix(self, origins, budget_s: float, walk_mps: float, max_walk_m: float) -> np.ndarray:
        """
        Tiempo mínimo (s) desde cada origen (GeoSeries/array de puntos UTM) hasta cada parada,
        embarcando en paradas a ≤ max_walk_m. Matriz (n_origenes, n_paradas); np.inf si no se
        llega en menos de budget_s.
        """
        origins = np.asarray(getattr(origins, "values", origins))
        n_o, n_s = len(origins), self.n_stops
        oi, si = self._tree.query(origins, predicate="dwithin", distance=max_walk_m)
        start = shapely.distance(origins[oi], self._geoms[si]) / walk_mps + self.wait_s[si]
        m = start < budget_s
        oi, si, start = oi[m], si[m], start[m]

        # Nodos 0..n_s-1 = paradas; n_s + i = origen virtual i (solo aristas de salida)
        rows = np.concatenate([self.edge_a, n_s + oi])
        cols = np.concatenate([self.edge_b, si])
        w = np.concatenate([self.edge_t, start])
        csr = coo_matrix((w, (rows, cols)), shape=(n_s + n_o, n_s + n_o)).tocsr()
        dist = dijkstra(csr, directed=True, indices=np.arange(n_s, n_s + n_o), limit=budget_s)
        dist = dist[:, :n_s]
        dist[dist >= budget_s] = np.inf
        return dist

    def service_min_times(self, reach: np.ndarray, service_geoms, walk_mps: float, budget_s: float,
                          max_walk_m: float, chunk: int = 64) -> tuple[np.ndarray, np.ndarray]:
        """
        Servicio más cercano en tiempo para cada origen de `reach` (salida de reachable_matrix):
        origen → parada → bajar en una parada a ≤ max_walk_m del servicio → andar hasta él.
        Devuelve (tiempo_s, índice del elemento) por origen; (inf, -1) si no hay ninguno ≤ budget_s.
        En empate gana el primer elemento del servicio (como en el código original).
        """
        geoms = np.asarray(getattr(service_geoms, "values", service_geoms))
        n_o = reach.shape[0]
        best_t = np.full(n_o, np.inf)
        best_f = np.full(n_o, -1)
        if len(geoms) == 0:
            return best_t, best_f
        # La búsqueda de paradas se hace desde el centroide; el tiempo a pie, desde la geometría
        pts = np.where(shapely.get_type_id(geoms) == 0, geoms, shapely.centroid(geoms))
        fi, si = self._tree.query(pts, predicate="dwithin", distance=max_walk_m)
        order = np.argsort(fi, kind="stable")
        fi, si = fi[order], si[order]
        if len(fi) == 0:
            return best_t, best_f
        walk = shapely.distance(geoms[fi], self._geoms[si]) / walk_mps

        for lo in range(0, n_o, chunk):
            tot = reach[lo:lo + chunk][:, si] + walk[None, :]
            tot[tot > budget_s] = np.inf
            j = np.argmin(tot, axis=1)
            t = tot[np.arange(len(j)), j]
            best_t[lo:lo + chunk] = t
            best_f[lo:lo + chunk] = np.where(np.isfinite(t), fi[j], -1)
        return best_t, best_f

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def stops_geodataframe(self) -> gpd.GeoDataFrame:
        """GeoDataFrame con todas las paradas en WGS84."""
        return gpd.GeoDataFrame(
            {"stop_id": self.stop_ids, "stop_name": self.stop_names},
            geometry=list(self._geoms), crs=self.utm_crs,
        ).to_crs(epsg=4326)
