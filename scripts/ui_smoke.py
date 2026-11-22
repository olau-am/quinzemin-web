"""
Prueba de humo de la web con Selenium (Chrome sin ventana): carga inicial, recalcular, vistas, URL
compartida, buscador, feeds GTFS, municipio sin preparar/demasiado grande y móvil.

Requisitos: pip install selenium; API local y web servidas:
  QM_STORAGE=local QM_DATA_DIR=data .venv/Scripts/python -m uvicorn api.app:app --port 8080
  .venv/Scripts/python -m http.server 8000 -d docs
Uso:  PYTHONIOENCODING=utf-8 .venv/Scripts/python scripts/ui_smoke.py   (capturas en data/ui/)
Necesita València (46250) y Burjassot (46078) preparados en ./data.
"""
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "ui")
os.makedirs(OUT, exist_ok=True)
BASE = "http://localhost:8000/?api=http://localhost:8080&lang=es"

opts = webdriver.ChromeOptions()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1400,900")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
# Que Selenium Manager descargue el driver que corresponde a esta versión de Chrome
os.environ["PATH"] = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if "WebDriver" not in p)
d = webdriver.Chrome(options=opts)
w = WebDriverWait(d, 60)
problems = []


def shot(name):
    d.save_screenshot(os.path.join(OUT, f"ui_{name}.png"))


def text(css):
    return d.find_element(By.CSS_SELECTOR, css).text


def wait_idle():
    """Espera a que no haya cálculo en curso (botón Recalcular activo y sin 'Calculando…')."""
    w.until(lambda dr: dr.find_element(By.ID, "qm-run").is_enabled()
            and "Calculando" not in dr.find_element(By.ID, "qm-status").text)


def check_console(step):
    for e in d.get_log("browser"):
        if e["level"] in ("SEVERE",) and "favicon" not in e["message"] and "tile.openstreetmap" not in e["message"]:
            problems.append(f"[{step}] consola: {e['message'][:200]}")


try:
    # 1) Carga inicial: València precalculada
    d.get(BASE)
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-legend-body"), "Todos los servicios"))
    w.until(lambda dr: dr.find_element(By.ID, "qm-city-info").text != "")      # metadatos de la API
    time.sleep(1)
    print("1 inicial:", text("#qm-subtitle"), "|", text("#qm-city-info"))
    shot("1_inicial")
    check_console("inicial")

    # 2) Recalcular en vista "nº de servicios" a 500 m
    d.find_element(By.CSS_SELECTOR, "input[name=view][value=count]").click()
    d.execute_script("var r=document.getElementById('qm-distance'); r.value=500; r.dispatchEvent(new Event('input',{bubbles:true}));")
    d.find_element(By.ID, "qm-run").click()
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-legend-body"), "ninguno"))
    wait_idle()
    print("2 count/500 m:", text("#qm-subtitle"), "| hash en URL:", "run=" in d.current_url,
          "| YAML:", d.find_element(By.ID, "qm-yaml").get_attribute("aria-disabled"))
    shot("2_count")
    check_console("count")

    # 3) Transporte público, puntuación, prioridad
    d.find_element(By.CSS_SELECTOR, "input[name=view][value=score]").click()
    d.find_element(By.CSS_SELECTOR, "input[name=mode][value=transit]").click()
    d.find_element(By.ID, "qm-run").click()
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-subtitle"), "transporte público"))
    wait_idle()
    d.find_element(By.CSS_SELECTOR, "input[name=qm-lview][value=prio]").click()
    time.sleep(0.5)
    print("3 transit+prio:", text("#qm-subtitle"), "| modelo:", d.find_element(By.ID, "qm-tmodel").get_attribute("value"),
          "| aviso:", "menor entre ir andando" in text("#qm-legend-body"), "| tm en URL:", "tm=frequencies" in d.current_url)
    shot("3_transit_prio")
    check_console("transit")

    # 3b) Modelo simplificado y penalización por trasbordo
    from selenium.webdriver.support.ui import Select
    Select(d.find_element(By.ID, "qm-tmodel")).select_by_value("simple")
    print("  penalización oculta en simplificado:", not d.find_element(By.ID, "qm-f-penalty").is_displayed())
    d.find_element(By.ID, "qm-run").click()
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-legend-body"), "Modelo simplificado"))
    wait_idle()
    print("3b simplificado:", "tm=simple" in d.current_url)
    Select(d.find_element(By.ID, "qm-tmodel")).select_by_value("frequencies")
    d.execute_script("var r=document.getElementById('qm-penalty'); r.value=5; r.dispatchEvent(new Event('input',{bubbles:true}));")
    d.find_element(By.ID, "qm-run").click()
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-legend-body"), "5 min por trasbordo"))
    wait_idle()
    print("  con esperas +5 min:", "tp=5" in d.current_url, "|", d.find_element(By.ID, "qm-penalty-out").text)
    check_console("modelos")

    # 4) URL compartida: abrir en otra carga y comprobar que reproduce el resultado
    url = d.current_url
    d.get("about:blank")
    d.get(url)
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-subtitle"), "transporte público"))
    wait_idle()
    print("4 URL compartida OK:", d.find_element(By.CSS_SELECTOR, "input[name=mode][value=transit]").is_selected(),
          "| penalización restaurada:", d.find_element(By.ID, "qm-penalty").get_attribute("value") == "5")

    # 5) Buscar y cambiar a Burjassot (preparado)
    s = d.find_element(By.ID, "qm-search")
    s.clear()
    s.send_keys("burjas")
    w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "#qm-results li[data-i]")))
    print("5 sugerencias:", [li.text.replace("\n", " ") for li in d.find_elements(By.CSS_SELECTOR, "#qm-results li")][:3])
    s.send_keys(Keys.ENTER)
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-subtitle"), "Burjassot"))
    wait_idle()
    time.sleep(1)
    print("  Burjassot:", text("#qm-subtitle"), "| servicios:", len(d.find_elements(By.CSS_SELECTOR, "input[data-svc]")))
    shot("5_burjassot")
    check_console("burjassot")

    # 6) Sección de transporte: lista de feeds
    d.find_element(By.CSS_SELECTOR, "#qm-transit-box summary").click()
    w.until(lambda dr: dr.find_elements(By.CSS_SELECTOR, "input[data-feed]") or "No hay" in dr.find_element(By.ID, "qm-feeds").text)
    feeds = d.find_elements(By.CSS_SELECTOR, "input[data-feed]")
    print("6 feeds:", len(feeds), "marcados:", sum(1 for f in feeds if f.is_selected()))
    shot("6_feeds")

    # 7) Municipio sin preparar y municipio demasiado grande
    s = d.find_element(By.ID, "qm-search")
    s.clear()
    s.send_keys("Alboraia")
    w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "#qm-results li[data-i]")))
    s.send_keys(Keys.ENTER)
    w.until(EC.visibility_of_element_located((By.ID, "qm-prepare")))
    print("7 sin preparar:", text("#qm-prepare-text"), "| botón activo:", d.find_element(By.ID, "qm-prepare-btn").is_enabled())
    shot("7_sin_preparar")
    s.clear()
    s.send_keys("Madrid")
    w.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "#qm-results li[data-i]")))
    s.send_keys(Keys.ENTER)
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-prepare-text"), "supera"))
    print("  Madrid:", text("#qm-prepare-text")[:60], "| botón activo:", d.find_element(By.ID, "qm-prepare-btn").is_enabled())
    check_console("sin preparar")

    # 8) Móvil
    d.set_window_size(400, 800)
    d.get(BASE)
    w.until(EC.text_to_be_present_in_element((By.ID, "qm-subtitle"), "València"))
    time.sleep(1)
    shot("8_movil")
    print("8 móvil: panel plegado:", "collapsed" in d.find_element(By.ID, "qm-panel").get_attribute("class"))
except Exception as e:
    shot("error")
    problems.append(f"EXCEPCIÓN: {type(e).__name__}: {e}")
finally:
    d.quit()

print("\nProblemas:" if problems else "\nSin errores en consola ni excepciones.")
for p in problems:
    print(" ·", p)
sys.exit(1 if problems else 0)
