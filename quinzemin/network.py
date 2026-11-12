"""
Motor de accesibilidad a pie sobre la red peatonal real (OSM).

Pasos (ver DOCUMENTACIO.md):
  1-2. Grafo peatonal de OSMnx (network_type="walk", simplificado) → data/walk_graph.graphml
       (lo genera download_data.py).
  3.   Snapping: cada origen/destino se engancha al nodo más cercano (cKDTree).
       La distancia de enganche se suma al recorrido y se documenta.
  4.   Dijkstra multi-origen (scipy.sparse.csgraph, min_only=True): todos los
       servicios de una categoría actúan como orígenes a la vez, de modo que un único
       cálculo da a cada nodo la distancia al servicio más cercano.
       Para respetar la distancia de enganche de cada servicio se añade un nodo
       virtual por servicio, unido a su nodo de red con peso = distancia de enganche.
  5.   Isócronas (solo visualización): buffer de las aristas alcanzadas.
"""
import numpy as np
import pandas as pd
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely import contains_xy
from shapely.ops import substring


class WalkNetwork:
    def __init__(self, node_xy, u, v, w, edge_geoms, utm_crs):
        self._xy    = node_xy            # (n, 2) coordenadas UTM de los nodos
        self._n     = len(node_xy)
        self._u     = u                  # extremos de arista (índices de nodo)
        self._v     = v
        self._w     = w                  # longitud de arista (m)
        self._geoms = edge_geoms         # LineString UTM por arista (orientada u→v)
        self._tree  = cKDTree(node_xy)
        self._utm   = utm_crs
        self._csr   = self._build_csr(self._n, u, v, w)

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str, utm_crs) -> "WalkNetwork":
        import osmnx as ox

        print(f"  Leyendo red peatonal: {path}")
        return cls.from_graph(ox.load_graphml(path), utm_crs)

    @classmethod
    def from_graph(cls, G, utm_crs) -> "WalkNetwork":
        """Desde un grafo de osmnx (sin proyectar o proyectado)."""
        import osmnx as ox

        G = ox.project_graph(G, to_crs=utm_crs)
        nodes, edges = ox.graph_to_gdfs(G)

        node_ids = nodes.index.to_numpy()
        pos      = pd.Series(np.arange(len(node_ids)), index=node_ids)
        node_xy  = np.column_stack([nodes.geometry.x.to_numpy(), nodes.geometry.y.to_numpy()])

        edges = edges.reset_index()
        u = pos.loc[edges["u"].to_numpy()].to_numpy()
        v = pos.loc[edges["v"].to_numpy()].to_numpy()
        w = edges["length"].astype(float).to_numpy()

        # Red no dirigida: quedarse con una arista por par de nodos (la más corta)
        a, b = np.minimum(u, v), np.maximum(u, v)
        df = pd.DataFrame({"a": a, "b": b, "w": w, "i": np.arange(len(w))})
        df = df[df["a"] != df["b"]].sort_values("w").drop_duplicates(["a", "b"])
        keep = df["i"].to_numpy()

        geoms = edges.geometry.to_numpy()[keep]
        net = cls(node_xy, u[keep], v[keep], w[keep], list(geoms), utm_crs)
        print(f"  Red peatonal: {net._n} nodos, {len(keep)} aristas")
        return net

    def to_npz(self, path) -> None:
        """Guarda la red ya proyectada (sin geometrías de arista: solo hacen falta para isócronas)."""
        np.savez_compressed(
            path, node_xy=self._xy, u=self._u.astype(np.int32), v=self._v.astype(np.int32),
            w=self._w, crs=np.array(self._utm.to_wkt()),
        )

    @classmethod
    def from_npz(cls, path) -> "WalkNetwork":
        """Carga una red guardada con to_npz (no necesita osmnx)."""
        from pyproj import CRS

        with np.load(path) as d:
            return cls(d["node_xy"], d["u"].astype(np.int64), d["v"].astype(np.int64), d["w"],
                       None, CRS.from_wkt(str(d["crs"])))

    @staticmethod
    def _build_csr(n, u, v, w, extra=None):
        """Matriz de adyacencia simétrica. `extra` = (u, v, w) de aristas virtuales."""
        if extra is not None:
            u = np.concatenate([u, extra[0]])
            v = np.concatenate([v, extra[1]])
            w = np.concatenate([w, extra[2]])
        # scipy trata 0 como "sin arista": usar un epsilon para aristas de longitud 0
        w = np.maximum(w, 1e-6)
        return coo_matrix((w, (u, v)), shape=(n, n)).tocsr()

    # ------------------------------------------------------------------
    # Paso 3: snapping
    # ------------------------------------------------------------------

    def snap(self, points) -> tuple[np.ndarray, np.ndarray]:
        """Puntos (GeoSeries UTM o lista de Point) → (índice de nodo, distancia de enganche m)."""
        xy = np.array([(p.x, p.y) for p in points])
        dist, idx = self._tree.query(xy)
        return idx, dist

    def nodes_within(self, geom) -> np.ndarray:
        """Índices de los nodos dentro de una geometría (UTM)."""
        minx, miny, maxx, maxy = geom.bounds
        cand = self._tree.query_ball_point(
            [(minx + maxx) / 2, (miny + maxy) / 2],
            r=np.hypot(maxx - minx, maxy - miny) / 2,
        )
        if not cand:
            return np.array([], dtype=int)
        cand = np.asarray(cand)
        mask = contains_xy(geom, self._xy[cand, 0], self._xy[cand, 1])
        return cand[mask]

    # ------------------------------------------------------------------
    # Paso 4: Dijkstra multi-origen
    # ------------------------------------------------------------------

    def destinations(self, service_gdf, polygon_access: bool = False, access_m: float = 20,
                     max_snap_m: float = 500):
        """
        Convierte un GeoDataFrame de servicios en destinos sobre la red.
        Devuelve (feature_idx, node_idx, offset_m): un servicio puede aportar varios
        destinos (zonas verdes: todos los nodos dentro del polígono + access_m).
        Se descartan los destinos a más de max_snap_m de la red (servicios fuera del
        área descargada, p. ej. un WFS autonómico); self.last_dropped guarda cuántos.
        """
        f_idx, n_idx, off = [], [], []
        for i, geom in enumerate(service_gdf.geometry):
            if geom is None or geom.is_empty:
                continue
            if polygon_access and geom.geom_type in ("Polygon", "MultiPolygon"):
                inside = self.nodes_within(geom.buffer(access_m))
                if len(inside):
                    f_idx += [i] * len(inside)
                    n_idx += list(inside)
                    off   += [0.0] * len(inside)
                    continue
                # Sin nodos dentro: enganchar el punto de la frontera más próximo a la red
                d, j = self._tree.query(np.array(geom.exterior.coords if geom.geom_type == "Polygon"
                                                 else geom.convex_hull.exterior.coords))
                k = int(np.argmin(d))
                f_idx.append(i); n_idx.append(int(j[k])); off.append(float(d[k]))
                continue
            pt = geom if geom.geom_type == "Point" else geom.representative_point()
            d, j = self._tree.query((pt.x, pt.y))
            f_idx.append(i); n_idx.append(int(j)); off.append(float(d))
        f_idx, n_idx, off = np.array(f_idx, dtype=int), np.array(n_idx, dtype=int), np.array(off)
        keep = off <= max_snap_m
        self.last_dropped = len(np.unique(f_idx[~keep])) - len(np.intersect1d(f_idx[~keep], f_idx[keep]))
        return f_idx[keep], n_idx[keep], off[keep]

    def nearest_service(self, dest_feature, dest_node, dest_offset):
        """
        Un único Dijkstra para toda la categoría.
        Devuelve, para cada nodo de la red: (distancia_m al servicio más cercano,
        índice de feature del servicio más cercano o -1).
        """
        k = len(dest_node)
        if k == 0:
            return np.full(self._n, np.inf), np.full(self._n, -1)
        virt = np.arange(self._n, self._n + k)
        csr = self._build_csr(self._n + k, self._u, self._v, self._w,
                              extra=(virt, dest_node, dest_offset))
        dist, _, src = dijkstra(csr, directed=False, indices=virt,
                                min_only=True, return_predecessors=True)
        dist, src = dist[: self._n], src[: self._n]
        feat = np.where(src >= 0, dest_feature[np.clip(src - self._n, 0, k - 1)], -1)
        return dist, feat

    # ------------------------------------------------------------------
    # Paso 5: isócronas (visualización)
    # ------------------------------------------------------------------

    def isochrone(self, node_idx: int, limit_m: float, start_offset: float = 0.0,
                  buffer_m: float = 25):
        """Polígono de la zona alcanzable a pie desde un nodo (buffer de aristas)."""
        if self._geoms is None:
            raise RuntimeError("Red cargada sin geometrías (from_npz): usa WalkNetwork.load para isócronas")
        budget = limit_m - start_offset
        if budget <= 0:
            return None
        d = dijkstra(self._csr, directed=False, indices=node_idx, limit=budget)
        du, dv = d[self._u], d[self._v]
        lines = []
        for i in np.flatnonzero(np.isfinite(du) | np.isfinite(dv)):
            g, L = self._geoms[i], self._w[i]
            a, b = du[i], dv[i]
            if a + L <= budget or b + L <= budget:
                lines.append(g)                 # arista completa
                continue
            # Aristas frontera: tramos parciales desde cada extremo alcanzado
            gl = g.length or L
            if np.isfinite(a):
                lines.append(substring(g, 0, (budget - a) / L * gl))
            if np.isfinite(b):
                lines.append(substring(g, gl - (budget - b) / L * gl, gl))
        if not lines:
            return None
        # Buffer por arista + unión: ~40x más rápido que unir primero y luego hacer buffer
        bufs = shapely.buffer(np.array(lines, dtype=object), buffer_m, quad_segs=4)
        return shapely.union_all(bufs).simplify(10)

    @property
    def node_count(self) -> int:
        return self._n
