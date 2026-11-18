"""
Motor de accesibilidad por transporte público usando datos GTFS. Dos modelos, los dos basados en
frecuencias (tiempo de viaje "típico", independiente de la hora de salida):

1. Por frecuencias de línea (reachable_matrix_freq, el predeterminado en la web)
   - Grafo con nodos parada y parada-línea (ruta + sentido). Cada subida, también en los
     trasbordos, espera la mitad del intervalo de esa línea en esa parada, calculado con las
     salidas de un laborable representativo (mar–jue con más viajes según calendar.txt y
     calendar_dates.txt) en la franja 07–21 h.
   - Los trasbordos pueden sumar una penalización fija y se puede andar entre paradas a ≤ 250 m.
   - El motor (engine.py) toma el mínimo entre este tiempo e ir andando al servicio.

2. Simplificado (reachable_matrix): el del proyecto original (legacy/gtfs.py). Espera solo en la
   parada de origen, headway = 16 h / nº de filas de stop_times de la parada en todo el feed;
   los trasbordos no tienen espera (las aristas de todas las líneas se funden) y no se puede
   andar entre paradas. Se conserva para reproducir los resultados originales.

Modelo simplificado reescrito para que sea rápido (mismos resultados que legacy/gtfs.py):
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

DAY_S = 16 * 3600        # horas de servicio supuestas para el headway (modelo simplificado)
MAX_HOP_S = 3600         # tramos entre paradas consecutivas más largos se descartan

# Modelo por frecuencias de línea
WINDOW_S = (7 * 3600, 21 * 3600)   # franja en la que se cuentan las salidas de cada línea
MIN_WAIT_S = 30                    # espera mínima al subir
TRANSFER_MAX_M = 250               # trasbordo a pie entre paradas a esta distancia como mucho
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _read_optional(z: zipfile.ZipFile, name: str) -> pd.DataFrame | None:
    """Fichero opcional de un GTFS (trips, calendar…) como texto, con cabeceras y valores recortados."""
    n = next((x for x in z.namelist() if x.split("/")[-1] == name), None)
    if n is None:
        return None
    with z.open(n) as fh:
        df = pd.read_csv(fh, dtype=str, encoding="utf-8-sig").rename(columns=str.strip)
    return _strip(df)


def _representative_day(cal: pd.DataFrame | None, cdates: pd.DataFrame | None,
                        trips: pd.DataFrame) -> tuple[str | None, set | None]:
    """
    Laborable típico del feed: el martes, miércoles o jueves de su vigencia con más viajes
    activos (calendar.txt + excepciones de calendar_dates.txt). Devuelve (fecha AAAAMMDD,
    service_id activos), o (None, None) si el feed no trae calendario (se usan todos los viajes).
    """
    if "service_id" not in trips:
        return None, None
    n_by_svc = trips["service_id"].value_counts()
    has_cal = cal is not None and {"service_id", "start_date", "end_date"} <= set(cal.columns) and len(cal)
    has_cd = cdates is not None and {"service_id", "date", "exception_type"} <= set(cdates.columns) and len(cdates)
    if has_cal:
        c_start = pd.to_datetime(cal["start_date"], format="%Y%m%d", errors="coerce")
        c_end = pd.to_datetime(cal["end_date"], format="%Y%m%d", errors="coerce")
        start, end = c_start.min(), c_end.max()
    elif has_cd:
        dd = pd.to_datetime(cdates["date"], format="%Y%m%d", errors="coerce")
        start, end = dd.min(), dd.max()
    else:
        return None, None
    if pd.isna(start) or pd.isna(end):
        return None, None
    end = min(end, start + pd.Timedelta(days=370))
    cand = pd.date_range(start, end)
    mid = cand[cand.dayofweek.isin([1, 2, 3])]
    cand = mid if len(mid) else cand
    by_date = {d: g for d, g in cdates.groupby("date")} if has_cd else {}
    best = (0, None, None)
    for dt in cand:
        active = set()
        if has_cal:
            col = _WEEKDAYS[dt.dayofweek]
            on = cal[col].eq("1") if col in cal else pd.Series(False, index=cal.index)
            active = set(cal.loc[on & (c_start <= dt) & (dt <= c_end), "service_id"])
        ds = dt.strftime("%Y%m%d")
        if ds in by_date:
            g = by_date[ds]
            active |= set(g.loc[g["exception_type"] == "1", "service_id"])
            active -= set(g.loc[g["exception_type"] == "2", "service_id"])
        n = int(n_by_svc.reindex(list(active)).fillna(0).sum())
        if n > best[0]:
            best = (n, ds, active)
    return (best[1], best[2]) if best[0] > 0 else (None, None)


def _build_frequencies(times: pd.DataFrame, sid_idx: pd.Series, trips_l: list, days: dict,
                       stop_xy: np.ndarray) -> dict | None:
    """
    Estructuras del modelo por frecuencias a partir de los horarios del día representativo:
      rs_stop[r]          parada de cada nodo parada-línea (línea = ruta + sentido)
      rs_wait[r]          espera media al subir = intervalo / 2 en la franja WINDOW_S (inf si no hay salidas)
      ride_a/b/t          viajes entre paradas-línea consecutivas (mediana de tiempos, ≥ 1 s)
      xfer_a/b/d          pares de paradas a ≤ TRANSFER_MAX_M para trasbordar a pie (distancia en m)
    """
    if not trips_l:
        return None
    trips = pd.concat(trips_l, ignore_index=True).drop_duplicates("trip_id")
    t = times.merge(trips, on="trip_id", how="inner")
    t["s"] = sid_idx.reindex(t["stop_id"]).to_numpy()
    t = t[t["s"].notna()]
    if t.empty:
        return None
    t = t.sort_values(["trip_id", "_seq"], kind="mergesort").reset_index(drop=True)
    s = t["s"].to_numpy().astype(np.int64)
    rs_code, rs_keys = pd.factorize((t["route"] + "#" + pd.Series(s, index=t.index).astype(str)).to_numpy())
    rs_stop = np.zeros(len(rs_keys), dtype=np.int64)
    rs_stop[rs_code] = s

    trip = t["trip_id"].to_numpy()
    same = trip[1:] == trip[:-1]
    dep, arr = t["_dep"].to_numpy(), t["_arr"].to_numpy()
    tt = (arr[1:] - dep[:-1])[same]
    a, b = rs_code[:-1][same], rs_code[1:][same]
    ok = (tt >= 0) & (tt <= MAX_HOP_S) & (a != b)
    ride = pd.DataFrame({"a": a[ok], "b": b[ok], "t": tt[ok]}).groupby(["a", "b"], as_index=False)["t"].median()
    ride["t"] = ride["t"].clip(lower=1.0)

    # Salidas en la franja (la última parada de cada viaje no es una salida)
    departs = np.zeros(len(t), dtype=bool)
    departs[:-1] = same
    in_win = departs & (dep >= WINDOW_S[0]) & (dep < WINDOW_S[1])
    n_dep = np.bincount(rs_code[in_win], minlength=len(rs_keys))
    span = WINDOW_S[1] - WINDOW_S[0]
    with np.errstate(divide="ignore"):
        rs_wait = np.where(n_dep > 0, np.maximum(MIN_WAIT_S, span / np.maximum(n_dep, 1) / 2), np.inf)

    pts = shapely.points(stop_xy)
    xa, xb = STRtree(pts).query(pts, predicate="dwithin", distance=TRANSFER_MAX_M)
    keep = xa != xb
    xa, xb = xa[keep], xb[keep]
    xd = shapely.distance(pts[xa], pts[xb])

    lines = pd.Series(t["route"].unique()).nunique()
    print(f"  Modelo por frecuencias: {lines} líneas, {len(rs_keys)} paradas-línea, {len(ride)} tramos, "
          f"{len(xa)} trasbordos a pie; día {', '.join(f'{k}={v}' for k, v in days.items() if v) or 'sin calendario'}")
    return {"rs_stop": rs_stop, "rs_wait": rs_wait.astype(float),
            "ride_a": ride["a"].to_numpy(np.int64), "ride_b": ride["b"].to_numpy(np.int64),
            "ride_t": ride["t"].to_numpy(float),
            "xfer_a": xa.astype(np.int64), "xfer_b": xb.astype(np.int64), "xfer_d": xd.astype(float),
            "info": {"days": days, "window": "07:00-21:00", "lines": int(lines),
                     "route_stops": len(rs_keys), "transfer_max_m": TRANSFER_MAX_M}}


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
    def __init__(self, stop_ids, stop_names, stop_xy, wait_s, edge_a, edge_b, edge_t, utm_crs, freq=None):
        self.stop_ids   = np.asarray(stop_ids, dtype=object)   # stop_id con prefijo de feed
        self.stop_names = np.asarray(stop_names, dtype=object)
        self.stop_xy    = np.asarray(stop_xy, dtype=float)      # (n, 2) UTM
        self.wait_s     = np.asarray(wait_s, dtype=float)       # espera media al embarcar
        self.edge_a     = np.asarray(edge_a, dtype=np.int64)    # aristas dirigidas a → b
        self.edge_b     = np.asarray(edge_b, dtype=np.int64)
        self.edge_t     = np.asarray(edge_t, dtype=float)       # segundos
        self.utm_crs    = utm_crs
        # Modelo por frecuencias de línea (None en los transit.pkl anteriores): dict con
        # rs_stop, rs_wait, ride_a/b/t, xfer_a/b/d e info (ver _build_frequencies)
        self.freq       = freq
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

    @property
    def has_frequencies(self) -> bool:
        return getattr(self, "freq", None) is not None and len(self.freq["rs_stop"]) > 0

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
        stops_l, times_l, trips_l, days = [], [], [], {}
        for src in sources:
            prefix = src["id"] + "_"
            print(f"  Leyendo {src.get('label', src['id'])}: {src['path']}")
            with zipfile.ZipFile(src["path"]) as z:
                def member(name, z=z):
                    return next(n for n in z.namelist() if n.split("/")[-1] == name)
                trips = _read_optional(z, "trips.txt")
                if trips is not None and "trip_id" in trips:
                    day, active = _representative_day(_read_optional(z, "calendar.txt"),
                                                      _read_optional(z, "calendar_dates.txt"), trips)
                    days[src["id"]] = day
                    if active is not None:
                        trips = trips[trips["service_id"].isin(active)]
                    for c in ("route_id", "direction_id"):
                        if c not in trips:
                            trips[c] = ""
                    trips_l.append(pd.DataFrame({
                        "trip_id": prefix + trips["trip_id"],
                        "route": prefix + trips["route_id"].fillna("") + "|" + trips["direction_id"].fillna(""),
                    }))
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
        freq = _build_frequencies(times, sid_idx, trips_l, days, stop_xy)
        return cls(stop_ids, names, stop_xy, wait, edges["a"], edges["b"], edges["t"], utm_crs, freq=freq)

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

    def reachable_matrix_freq(self, origins, budget_s: float, walk_mps: float, max_walk_m: float,
                              transfer_penalty_s: float = 0.0) -> np.ndarray:
        """
        Como reachable_matrix, pero con el modelo por frecuencias de línea: cada subida (también
        en los trasbordos) espera la mitad del intervalo de esa línea en esa parada; los trasbordos
        suman además transfer_penalty_s y se puede andar entre paradas cercanas. Una parada cuenta
        como alcanzada solo después de haber viajado (se llega a ella bajando de un vehículo).
        """
        f = self.freq
        origins = np.asarray(getattr(origins, "values", origins))
        n_o, n_s, n_r = len(origins), self.n_stops, len(f["rs_stop"])
        eps = 1e-6
        S, R, O = 0, n_s, n_s + n_r                     # desplazamiento de cada tipo de nodo

        # Un nodo parada-línea significa "he llegado a esta parada en un vehículo de esta línea".
        # Subir se modela junto con el primer tramo: subir en a (esperar) y viajar a → b. Así no se
        # puede "subir y bajar" en la misma parada sin viajar.
        ride = pd.DataFrame({"a": f["ride_a"], "b": f["ride_b"], "t": f["ride_t"]})
        ride["wait"] = f["rs_wait"][ride["a"].to_numpy()]
        ride["s"] = f["rs_stop"][ride["a"].to_numpy()]
        board = ride[np.isfinite(ride["wait"])]

        # Origen → (subir en una parada a ≤ max_walk_m) → siguiente parada de esa línea
        oi, si = self._tree.query(origins, predicate="dwithin", distance=max_walk_m)
        walk = shapely.distance(origins[oi], self._geoms[si]) / walk_mps
        first = pd.DataFrame({"o": oi, "s": si, "w": walk}).merge(board, on="s")
        first["c"] = first["w"] + first["wait"] + first["t"]
        first = first[first["c"] < budget_s]

        edges = pd.concat([
            pd.DataFrame({"i": O + first["o"], "j": R + first["b"], "c": first["c"]}),            # 1.ª subida
            pd.DataFrame({"i": R + ride["a"], "j": R + ride["b"], "c": ride["t"]}),               # seguir
            pd.DataFrame({"i": R + np.arange(n_r), "j": S + f["rs_stop"], "c": eps}),             # bajar
            pd.DataFrame({"i": S + board["s"], "j": R + board["b"],                              # trasbordo
                          "c": board["wait"] + transfer_penalty_s + board["t"]}),
            pd.DataFrame({"i": S + f["xfer_a"], "j": S + f["xfer_b"],                            # a pie
                          "c": np.maximum(f["xfer_d"] / walk_mps, eps)}),
        ], ignore_index=True)
        # coo_matrix suma las aristas repetidas: quedarse con la más corta de cada par
        edges = edges.groupby(["i", "j"], as_index=False)["c"].min()
        n = n_s + n_r + n_o
        csr = coo_matrix((edges["c"].to_numpy(float), (edges["i"].to_numpy(), edges["j"].to_numpy())),
                         shape=(n, n)).tocsr()
        dist = dijkstra(csr, directed=True, indices=np.arange(O, O + n_o), limit=budget_s)
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
