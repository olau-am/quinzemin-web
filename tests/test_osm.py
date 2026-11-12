import geopandas as gpd
import pytest
from shapely.geometry import Point

from quinzemin import osm


@pytest.mark.parametrize("tags,expected", [
    ({"isced:level": "1"}, {"primaria"}),
    ({"isced:level": "2;3"}, {"secundaria"}),
    ({"isced:level": "1-3"}, {"primaria", "secundaria"}),
    ({"isced:level": "0"}, set()),
    ({"school": "primary"}, {"primaria"}),
    ({"name": "CEIP Lluís Guarner"}, {"primaria"}),
    ({"name": "Col·legi d'Educació Infantil i Primària El Barranquet"}, {"primaria"}),
    ({"name": "Colegio de Educación Infantil y Primaria Jaime I"}, {"primaria"}),
    ({"name": "IES Vicent Andrés Estellés"}, {"secundaria"}),
    ({"name": "Institut Joan Fuster"}, {"secundaria"}),
    ({"name": "CEIPSO Los Ángeles"}, {"primaria", "secundaria"}),
    ({"name": "Escola Bressol Els Pins"}, set()),
    ({"name": "Escuela Infantil Pinocho"}, set()),
    ({"name": "Centro Privado de Formación Profesional Específica Teide"}, {"otro"}),
    ({"name": "EPA Burjassot"}, {"otro"}),
    ({"name": "Colegio Niño Jesús"}, None),          # concertado sin más datos: no se sabe
    ({}, None),
])
def test_classify_school(tags, expected):
    assert osm.classify_school(tags) == expected


def _schools(names):
    return gpd.GeoDataFrame({"name": names}, geometry=[Point(i, 0) for i in range(len(names))], crs=4326)


def test_split_education_une_si_hay_pocos_clasificados():
    parts, rep = osm.split_education(_schools(["CEIP A", "Colegio B", "Colegio C", "Escola Bressol D"]))
    assert list(parts) == ["educacion"]
    assert len(parts["educacion"]) == 3                 # la escuela infantil se descarta
    assert rep["separado"] is False and rep["descartados_infantil"] == 1


def test_split_education_separa_si_casi_todos_clasificados():
    names = ["CEIP A", "CEIP B", "CEIP C", "IES D", "IES E", "CEIPSO F", "IES G", "Colegio H"]
    parts, rep = osm.split_education(_schools(names), min_classified=0.85)
    assert rep["proporcion_clasificada"] == pytest.approx(7 / 8)
    assert rep["separado"] is True
    # los no clasificados cuentan en las dos etapas; CEIPSO también
    assert set(parts["primaria"]["name"]) == {"CEIP A", "CEIP B", "CEIP C", "CEIPSO F", "Colegio H"}
    assert set(parts["secundaria"]["name"]) == {"IES D", "IES E", "CEIPSO F", "IES G", "Colegio H"}


def test_merge_tags():
    merged = osm.merge_tags([{"amenity": ["clinic", "doctors"], "healthcare": "centre"},
                             {"amenity": "school"}, {"leisure": "park"}, {"shop": True}])
    assert merged == {"amenity": ["clinic", "doctors", "school"], "healthcare": ["centre"],
                      "leisure": ["park"], "shop": True}


def test_match_tags():
    g = gpd.GeoDataFrame({"amenity": ["clinic;doctors", "school", None], "shop": [None, None, "supermarket"]},
                         geometry=[Point(0, 0)] * 3, crs=4326)
    assert osm.match_tags(g, {"amenity": ["doctors"]}).tolist() == [True, False, False]
    assert osm.match_tags(g, {"shop": "supermarket", "amenity": "school"}).tolist() == [False, True, True]
    assert osm.match_tags(g, {"healthcare": "centre"}).tolist() == [False, False, False]
