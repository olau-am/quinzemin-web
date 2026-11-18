# Límite de tamaño de los municipios y trabajo futuro

La versión web de quinzemin solo prepara municipios de **hasta 1,5 millones de habitantes y
400 km²**. Este documento explica por qué existe ese límite, a quién deja fuera y qué habría que
hacer para quitarlo.

## 1. El límite

| Dónde | Qué |
|---|---|
| `quinzemin/prepare.py` | `MAX_POPULATION = 1_500_000`, `MAX_KM2 = 400`; `check_limits()` rechaza el municipio (la CLI lo salta con `--force`) |
| `api/app.py` | `within_limits()`: el buscador marca el municipio como "demasiado grande" y `POST /api/cities/{ine}/prepare` responde 422 |

Con el catálogo del INE de 2024 (8.132 municipios), el límite deja fuera:

| Criterio | Municipios | Población | Ejemplos |
|---|---|---|---|
| > 1,5 M hab. | 2 | 5,05 M | Madrid (3,41 M hab., 604 km²), Barcelona (1,64 M hab., 103 km²) |
| > 400 km² | 99 | 7,19 M (14,8 %) | Zaragoza (690.000 hab., 972 km²), Murcia (472.000, 886 km²), Córdoba (316.000, 1.263 km²), Cartagena, Jerez, Cáceres (1.754 km²), Lorca |
| **Total** (alguno de los dos) | **100 (1,2 %)** | **8,82 M (18,2 % de España)** | |

Afecta a pocos municipios, pero a casi uno de cada cinco habitantes. El criterio de superficie pesa
más que el de población: la mitad de esos 99 municipios tiene menos de 10.000 habitantes, pero entre
ellos están capitales cuyo término municipal es mayoritariamente rural.

## 2. Por qué existe

El límite no viene del motor de cálculo, que es rápido (València: 0,4–1,1 s por cálculo y 591
secciones), sino de la **preparación** de cada municipio y de que todo funciona con servicios
públicos y gratuitos.

### 2.1 Descarga de OpenStreetMap con Overpass (el cuello de botella principal)

- La preparación hace dos consultas a Overpass: los servicios y la red peatonal. Overpass es un
  servicio público mantenido por voluntarios. Sus servidores limitan el tiempo y la memoria de cada
  consulta y, cuando están saturados, responden 429/502/504 o no contestan.
- Tiempos medidos: Burjassot (3,5 km²) tardó 8 min; València (136 km²) entre 17 y 28 min. Casi todo
  ese tiempo es espera y reintentos contra los servidores, que llegan a fallar varias veces seguidas.
- La red peatonal crece con la superficie. València (136 km² + 1 km de margen) da 62.919 nodos y
  94.970 aristas, y su descarga sola ya tarda entre 1 y 1,5 min cuando el servidor responde. Un
  término de 1.000 km² multiplica el volumen. osmnx parte las áreas grandes en varias consultas, pero
  cada una puede agotar el tiempo de un servidor público saturado, y la política de uso de Overpass
  pide no hacer uso intensivo automatizado de los servidores públicos.

### 2.2 Memoria y tiempo del Cloud Run Job

- El Job tiene **4 GiB y 1 h**. osmnx construye la red como grafo de networkx, que ocupa mucha
  memoria: el GraphML de València ocupa 80 MB en disco y bastante más cargado. La red de Madrid o
  Zaragoza, junto con la proyección, la simplificación y los servicios, se acercaría al límite de
  memoria. Con las esperas de Overpass, además, superaría la hora.
- Después la red se guarda como arrays (`walk_graph.npz`, 1,9 MB en València), así que el problema
  está en construirla, no en usarla.

### 2.3 Capa gratuita de Google Cloud

- Los Jobs comparten la cuota gratuita mensual con el servicio: 240.000 vCPU·s y 450.000 GiB·s.
  Una preparación de 30 min con 2 vCPU y 4 GiB consume 3.600 vCPU·s y 7.200 GiB·s, lo que da para
  unas **60 preparaciones al mes** sin coste. Los municipios grandes gastarían esa cuota mucho
  antes, y los límites por persona (2 al día) y globales (5 ciudades nuevas al día, 1 a la vez)
  existen por la misma razón.

### 2.4 Validez del método en términos municipales extensos

Además de lo técnico, hay una razón metodológica. En los términos municipales muy extensos, las
secciones censales rurales son enormes y su centroide no representa dónde vive la gente: puede caer
en un campo a kilómetros de los núcleos habitados. Medir la accesibilidad desde ese punto da
resultados poco fiables justo donde el término es más grande.

## 3. Trabajo futuro para quitar el límite

Ordenado de mayor a menor impacto.

### 3.1 Precalcular las distancias en la preparación

Es el cambio que más mejora el sistema. Las distancias de cada sección al servicio más cercano no
dependen de los parámetros que elige el usuario:

- en los modos **línea recta** y **red peatonal**, la distancia al servicio más cercano es la misma
  sea cual sea el umbral, el decaimiento o los pesos;
- en **transporte público**, el tiempo mínimo hasta cada servicio tampoco depende del presupuesto de
  tiempo, siempre que se calcule una vez con el máximo admitido (60 min × decaimiento 3). El
  presupuesto solo decide qué tiempos cuentan.

Si `prepare.py` guardara una tabla **secciones × servicios × modo** con la distancia o el tiempo y
el nombre del servicio más cercano, cada recálculo sería solo aplicar umbrales, créditos y pesos
sobre esa tabla. Eso supone:

- unos milisegundos por cálculo y muy poca memoria, aunque la ciudad tenga miles de secciones;
- que la API no necesitaría cargar la red peatonal ni el grafo de transporte;
- que incluso se podría calcular en el navegador, sin API, a partir de un fichero estático por
  ciudad.

La única restricción es que la velocidad a pie y la distancia máxima a una parada quedarían fijas
en la preparación, como ya ocurre ahora en la web.

### 3.2 Dejar de depender de Overpass: extractos de OpenStreetMap

- Descargar el extracto de España en formato PBF (Geofabrik, ~1,2 GB, actualizado a diario) en un
  trabajo periódico, por ejemplo semanal, y sacar de él los servicios y la red de cada municipio con
  `osmium`/`pyrosm`, sin consultas a servidores de terceros.
- También se podría montar una instancia propia de Overpass, pero necesita un servidor permanente
  con decenas de GB de disco, así que se sale de la capa gratuita.
- Sería coherente con la política de uso de Overpass y eliminaría la principal fuente de esperas y
  fallos: preparar un municipio pasaría de minutos de espera a segundos de cómputo.

### 3.3 Construir la red sin networkx

Como el motor solo usa arrays (`node_xy`, `u`, `v`, `w`), la red se puede construir directamente
desde el PBF con un recorrido en streaming de nodos y vías peatonales, sin pasar por un grafo de
networkx. Eso reduce la memoria de la preparación en un orden de magnitud y permite redes de cientos
de miles de nodos dentro de los 4 GiB del Job.

### 3.4 Partir los municipios grandes

El seccionado del INE incluye el **distrito censal** (`CUDIS`). Un municipio grande se puede preparar
distrito a distrito, cada uno con un margen mayor que el alcance máximo del cálculo (umbral ×
decaimiento, hasta 9 km a pie). Después se unen los resultados. Cada trabajo es pequeño y se pueden
lanzar en paralelo (Cloud Run Jobs admite varias tareas por ejecución). Otra opción es dividir el
territorio en teselas con solape.

### 3.5 Más recursos solo cuando hagan falta

Cloud Run Jobs permite hasta 8 vCPU y 32 GiB por tarea. Se podría usar un perfil "grande" solo para
los municipios que lo necesiten, asumiendo que salen de la capa gratuita, con un coste estimado de
céntimos por preparación. Sería útil para Madrid y Barcelona mientras no estén hechos 3.1–3.3.

### 3.6 Orígenes ponderados por población

Para resolver el problema metodológico del apartado 2.4, habría que medir desde donde vive la gente
y no desde el centroide de la sección:

- la **rejilla de población del INE** (celdas de 1 km² del Censo 2021), o los **edificios
  residenciales del Catastro**, como puntos de origen;
- la accesibilidad de la sección sería la media de sus orígenes, ponderada por población.

Esto mejora también las secciones urbanas grandes o con formas irregulares (frentes marítimos,
polígonos industriales con algunas viviendas).

### 3.7 Cola de preparaciones

Hoy, si ya hay una preparación en curso, la API responde 429 y el usuario tiene que volver a
intentarlo. Con una cola (Cloud Tasks o Pub/Sub), las peticiones esperarían su turno y la web podría
mostrar la posición en la cola. Con 3.1–3.3 las preparaciones serían cortas y la cola casi no se
notaría.

### 3.8 Transporte público con horarios reales

El modelo actual de transporte, «con esperas y trasbordos», trabaja con **frecuencias**. Cada
subida espera la mitad del intervalo de la línea en un laborable representativo (7–21 h). Esto da
un tiempo típico que no depende de la hora de salida, pero tiene límites:

- supone llegadas aleatorias a la parada; con horarios coordinados (trasbordos sincronizados,
  líneas con pocos pasos al día) la espera real puede ser mucho menor o mucho mayor;
- no distingue la hora punta, la noche ni el fin de semana;
- en líneas con muy pocas expediciones, la mitad del intervalo (p. ej. 3 h) no representa a quien
  planifica el viaje según el horario.

**Propuesta: cálculo con horario real**

1. **Algoritmo**: *Connection Scan Algorithm* (CSA) o RAPTOR sobre las conexiones del día elegido.
   CSA recorre una sola vez la lista de conexiones ordenada por hora y es fácil de vectorizar con
   numpy o compilar con numba. RAPTOR va por rondas (una por trasbordo) y limita el número de
   trasbordos de forma natural.
2. **Hora de salida**: en vez de una hora fija, una **ventana**, por ejemplo de 8:00 a 9:00 cada
   5 min, y como resultado la mediana o un percentil del tiempo. Así el indicador no depende de
   haber salido un minuto antes o después del paso del autobús. Es la práctica habitual en los
   estudios de accesibilidad.
3. **Calendario**: elegir una fecha concreta (laborable, sábado, domingo) aplicando
   `calendar.txt` y `calendar_dates.txt`, festivos incluidos. La selección del día representativo
   que ya existe en `gtfs.py` (`_representative_day`) sirve de base.
4. **Trasbordos**: tiempo mínimo de trasbordo (`transfers.txt` si el feed lo trae; si no, un
   valor fijo) y trasbordos a pie entre paradas cercanas, como en el modelo actual.
5. **Interfaz**: selector de día y franja horaria en la web, y el modelo por frecuencias como
   opción rápida.

**Coste y por qué se aplaza**: con 591 secciones y 12 horas de salida, son unos 7.000 cálculos
desde un origen por recálculo. En Python puro serían minutos; con CSA vectorizado o numba, segundos.
Para que la web siga respondiendo al momento, haría falta precalcular en la preparación los tiempos
de cada sección a cada parada (la tabla del apartado 3.1, una por franja horaria) y dejar a la API
solo los umbrales y los pesos. Además el resultado pasa a depender del día y la hora elegidos, lo
que complica comparar municipios. Por eso se ha priorizado el modelo por frecuencias, que ya corrige
los problemas principales del original: esperas en los trasbordos, trasbordos a pie e ir andando.

## 4. Resumen

| Problema | Hoy | Propuesta |
|---|---|---|
| Descarga de OSM lenta e inestable | Overpass público, 8–30 min por municipio | extracto PBF propio (3.2) |
| Memoria al construir la red | networkx, límite de 4 GiB | arrays en streaming (3.3), partir por distritos (3.4) |
| Coste por cálculo | carga red y grafo GTFS en cada instancia | tabla de distancias precalculada (3.1) |
| Representatividad en términos extensos | centroide de la sección | orígenes ponderados por población (3.6) |
| Esperas entre usuarios | 429 y reintento manual | cola (3.7) |
| Transporte sin horarios | frecuencias por línea (tiempo típico) | horario real con ventana de salida, CSA/RAPTOR (3.8) |

Con 3.1, 3.2 y 3.3 el límite de 400 km² dejaría de ser necesario. Madrid y Barcelona entrarían con
3.4 o 3.5. Así se cubriría el 18,2 % de la población que hoy queda fuera.
