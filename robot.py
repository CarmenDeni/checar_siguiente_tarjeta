"""Robot que vigila https://kokora.mx/admin/cursor y manda «siguiente tarjeta» cuando Cursor queda libre."""

import hashlib
import json
import os
import re
import threading
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ENV = os.environ.get
BASE = ENV("KOKORA_URL", "https://kokora.mx").rstrip("/")
CONV = ENV("CONVERSACION", "plan-plataforma-2")
PAGE_URL = f"{BASE}/admin/cursor?c={CONV}"
USUARIO = ENV("KOKORA_USUARIO", "admin").strip().strip('"\'')
PASSWORD = ENV("KOKORA_PASSWORD", "").strip().strip('"\'')
PHONE = ENV("CALLMEBOT_PHONE", "")
APIKEY = ENV("CALLMEBOT_APIKEY", "")
CLAVE = ENV("CLAVE_ADMIN", "")
INTERVALO = int(ENV("INTERVALO_SEGUNDOS", "60"))
MAX_ENVIOS_HORA = int(ENV("MAX_ENVIOS_HORA", "6"))
REPETIR_ALERTA_MIN = int(ENV("REPETIR_ALERTA_MIN", "30"))
SILENCIO_DESDE = int(ENV("SILENCIO_DESDE", "22"))
SILENCIO_HASTA = int(ENV("SILENCIO_HASTA", "7"))
MODO_PRUEBA = ENV("MODO_PRUEBA", "no").lower() in ("1", "si", "sí", "true", "yes")
ESTADO_ARCHIVO = ENV("ESTADO_ARCHIVO", "/data/estado.json" if os.path.isdir("/data") else "estado.json")
TZ = ZoneInfo(ENV("ZONA", "America/Mexico_City"))

FRASE_ALTO = "REVISA AHORA ANTES DE CONTINUAR"
MENSAJE = ENV("MENSAJE", (
    "Siguiente tarjeta. Si en la siguiente corrida necesitas algo de mí que sea indispensable para poder "
    "continuar, escribe hasta arriba del mensaje exacto REVISA AHORA ANTES DE CONTINUAR. Si esto no es "
    "indispensable para seguir, escribe hasta arriba la cadena PENDIENTES ANOTADOS EN BITACORA. "
    "SIGAMOS CON LA SIGUIENTE TARJETA"
))
ALERTA_ATENCION = "🚨🚨 NECESITO TU ATENCION PARA CONTINUAR 🚨🚨"

despertar = threading.Event()
lock = threading.Lock()


def plano(t: str) -> str:
    return unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode().lower()


def log(*a):
    print(datetime.now(TZ).strftime("%d/%m %H:%M:%S"), *a, flush=True)


def cargar_estado():
    base = {"pausado": False, "ultimo_enviado": None, "alerta_de": None, "ultima_alerta": 0,
            "envios": [], "fallas": 0, "ultima_revision": None, "ultimo_estado": "arrancando",
            "pendientes_noche": []}
    try:
        with open(ESTADO_ARCHIVO) as f:
            base.update(json.load(f))
    except Exception:
        pass
    return base


E = cargar_estado()


def guardar():
    try:
        with open(ESTADO_ARCHIVO, "w") as f:
            json.dump(E, f, ensure_ascii=False, indent=1)
    except Exception as ex:
        log("no pude guardar estado:", ex)


def es_silencio() -> bool:
    h = datetime.now(TZ).hour
    if SILENCIO_DESDE > SILENCIO_HASTA:
        return h >= SILENCIO_DESDE or h < SILENCIO_HASTA
    return SILENCIO_DESDE <= h < SILENCIO_HASTA


def whatsapp(texto: str) -> bool:
    log("WHATSAPP:", texto)
    if not (PHONE and APIKEY):
        return False
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": PHONE, "text": texto, "apikey": APIKEY})
    for intento in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                if r.status == 200:
                    return True
        except Exception as ex:
            log("callmebot falló:", ex)
        time.sleep(5 * (intento + 1))
    return False


def aviso_falla(texto: str):
    """Avisos de que algo se trabó: de noche se guardan y se mandan a las 7."""
    if es_silencio():
        if texto not in E["pendientes_noche"]:
            E["pendientes_noche"].append(texto)
        return
    whatsapp("⚠️ Robot siguiente tarjeta: " + texto)


def leer_pagina(texto: str) -> dict:
    """Saca de lo visible: si Cursor está libre y la respuesta más nueva de Cursor."""
    p = plano(texto)
    libre = "cursor esta libre" in p
    ocupado = "trabajando en tu mensaje" in p
    lineas = [l.strip() for l in texto.splitlines()]
    try:
        ini = next(i for i, l in enumerate(lineas) if plano(l).startswith("lo ultimo"))
    except StopIteration:
        ini = 0
    cab = re.compile(r"^(Cursor|Tú|Tu)\s*·", re.I)
    respuesta, cabecera, primero = None, None, None
    i = ini
    while i < len(lineas):
        m = cab.match(lineas[i])
        if m:
            quien = plano(m.group(1))
            primero = primero or quien
            if quien == "cursor":
                cuerpo = []
                j = i + 1
                while j < len(lineas) and not cab.match(lineas[j]):
                    cuerpo.append(lineas[j])
                    j += 1
                cabecera, respuesta = lineas[i], "\n".join(x for x in cuerpo if x)
                break
        i += 1
    huella = hashlib.sha1(f"{cabecera}|{(respuesta or '')[:300]}".encode()).hexdigest()[:12] if cabecera else None
    alto = bool(respuesta) and FRASE_ALTO.lower() in respuesta.lower()
    return {"libre": libre, "ocupado": ocupado, "cabecera": cabecera, "huella": huella,
            "alto": alto, "primero": primero, "inicio": (respuesta or "")[:160]}


class Navegador:
    def __init__(self):
        self.pw = sync_playwright().start()
        self.browser = None
        self.page = None
        self.creado = 0

    def abrir(self):
        self.cerrar()
        self.browser = self.pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = self.browser.new_context(locale="es-MX", viewport={"width": 1000, "height": 1400})
        self.page = ctx.new_page()
        self.page.set_default_timeout(30000)
        self.creado = time.time()

    def cerrar(self):
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        self.browser = None

    def asegurar(self):
        if not self.browser or time.time() - self.creado > 6 * 3600:
            self.abrir()

    def texto(self) -> str:
        self.asegurar()
        pg = self.page
        pg.goto(PAGE_URL, wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        if "cursor" not in pg.url.split("?")[0].rsplit("/", 1)[-1]:
            self.login()
        try:
            pg.get_by_text(re.compile(r"Lo último|Lo ultimo", re.I)).first.wait_for(timeout=20000)
        except Exception:
            pass
        pg.wait_for_timeout(1000)
        return pg.inner_text("body")

    def login(self):
        if not PASSWORD:
            raise RuntimeError("Falta KOKORA_PASSWORD para entrar al admin")
        pg = self.page
        log("entrando al admin…")
        pg.get_by_label("Usuario").fill(USUARIO)
        pg.get_by_label("Contraseña").fill(PASSWORD)
        try:
            pg.get_by_text("Recordar este celular").click()
        except Exception:
            pass
        pg.get_by_role("button", name=re.compile("Entrar", re.I)).click()
        pg.wait_for_timeout(4000)
        aviso = " ".join(pg.inner_text("body").split())[-200:]
        pg.goto(PAGE_URL, wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        if "cursor" not in pg.url:
            pista = "la página dice «Credenciales incorrectas»" if "incorrecta" in plano(aviso) else f"la página dice: {aviso}"
            raise RuntimeError(f"No pude entrar al admin como «{USUARIO}» "
                               f"(contraseña de {len(PASSWORD)} caracteres): {pista}")

    def mandar(self, mensaje: str):
        pg = self.page
        caja = pg.get_by_placeholder(re.compile("Por ejemplo", re.I)).first
        caja.click()
        caja.fill(mensaje)
        pg.wait_for_timeout(500)
        pg.get_by_role("button", name=re.compile("Mandar a Cursor", re.I)).first.click()
        pg.wait_for_timeout(5000)


def ciclo(nav: Navegador):
    texto = nav.texto()
    d = leer_pagina(texto)
    E["ultima_revision"] = datetime.now(TZ).isoformat(timespec="seconds")
    E["ultima_lectura"] = d
    if not d["libre"] and not d["ocupado"]:
        raise RuntimeError("No encontré el letrero de estado en la página")
    E["fallas"] = 0

    if d["ocupado"]:
        E["ultimo_estado"] = "Cursor trabajando"
        return

    # Cursor libre
    if d["alto"]:
        E["ultimo_estado"] = "Esperándote: Cursor pidió REVISA AHORA"
        if E["alerta_de"] != d["huella"]:
            E["alerta_de"], E["ultima_alerta"] = d["huella"], time.time()
            whatsapp(ALERTA_ATENCION)
        elif not es_silencio() and time.time() - E["ultima_alerta"] > REPETIR_ALERTA_MIN * 60:
            E["ultima_alerta"] = time.time()
            whatsapp(ALERTA_ATENCION + " (recordatorio)")
        return

    if E["pausado"]:
        E["ultimo_estado"] = "Pausado por control remoto"
        return

    if d["huella"] and d["huella"] == E["ultimo_enviado"]:
        E["ultimo_estado"] = "Libre, pero ya mandé a esta respuesta; espero respuesta nueva"
        if time.time() - E.get("enviado_en", 0) > 3600 * 3:
            aviso_falla("Cursor quedó libre pero no contestó a mi último «siguiente tarjeta» en 3 h. Revísalo.")
            E["enviado_en"] = time.time()
        return

    ahora = time.time()
    E["envios"] = [t for t in E["envios"] if ahora - t < 3600]
    if len(E["envios"]) >= MAX_ENVIOS_HORA:
        E["ultimo_estado"] = "Límite de envíos por hora alcanzado"
        aviso_falla(f"Llegué al límite de {MAX_ENVIOS_HORA} envíos por hora; pauso hasta que baje.")
        return

    if MODO_PRUEBA:
        log("MODO PRUEBA: aquí mandaría «siguiente tarjeta»")
        E["ultimo_estado"] = "Modo prueba: habría mandado"
        E["ultimo_enviado"] = d["huella"]
        return

    log("Cursor libre → mando siguiente tarjeta. Respuesta:", d["cabecera"])
    nav.mandar(MENSAJE)
    d2 = leer_pagina(nav.texto())
    if d2["ocupado"] or d2["primero"] == "tu":
        E["ultimo_enviado"], E["enviado_en"] = d["huella"], ahora
        E["envios"].append(ahora)
        E["ultimo_estado"] = "Mandé siguiente tarjeta"
        log("envío confirmado")
    else:
        raise RuntimeError("Piqué «Mandar a Cursor» pero la página no confirmó el envío")


def bucle():
    nav = None
    while True:
        try:
            with lock:
                if nav is None:
                    nav = Navegador()
                if not es_silencio() and E["pendientes_noche"]:
                    whatsapp("⚠️ Robot siguiente tarjeta, en la noche pasó:\n- " + "\n- ".join(E["pendientes_noche"]))
                    E["pendientes_noche"] = []
                ciclo(nav)
        except Exception as ex:
            E["fallas"] += 1
            E["ultimo_estado"] = f"Error: {ex}"
            log("ERROR:", ex)
            traceback.print_exc()
            if nav:
                nav.cerrar()
            if E["fallas"] in (3, 30) or (E["fallas"] and E["fallas"] % 120 == 0):
                aviso_falla(f"Llevo {E['fallas']} revisiones fallidas seguidas. Último error: {ex}")
        guardar()
        despertar.wait(INTERVALO if E["fallas"] < 3 else min(300, INTERVALO * 2))
        despertar.clear()


class Control(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, code, cuerpo):
        b = json.dumps(cuerpo, ensure_ascii=False, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b)

    def manejar(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/salud"):
            return self.responder(200, {"ok": True})
        if not CLAVE or q.get("clave", [""])[0] != CLAVE:
            return self.responder(403, {"error": "clave incorrecta"})
        if u.path == "/pausar":
            E["pausado"] = True
        elif u.path == "/reanudar":
            E["pausado"] = False
            despertar.set()
        elif u.path in ("/aviso", "/revisar"):
            despertar.set()
        elif u.path == "/prueba-whatsapp":
            whatsapp("✅ Robot siguiente tarjeta: prueba de aviso")
        elif u.path != "/estado":
            return self.responder(404, {"error": "no existe"})
        guardar()
        vista = {k: E[k] for k in ("pausado", "ultimo_estado", "ultima_revision", "fallas", "pendientes_noche")}
        vista["ultima_lectura"] = E.get("ultima_lectura")
        vista["silencio_ahora"] = es_silencio()
        self.responder(200, vista)

    do_GET = manejar
    do_POST = manejar


def main():
    log(f"Robot arrancó. Vigilo {PAGE_URL} cada {INTERVALO}s. Modo prueba: {MODO_PRUEBA}")
    srv = ThreadingHTTPServer(("0.0.0.0", int(ENV("PORT", "8000"))), Control)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if not es_silencio():
        whatsapp("✅ Robot siguiente tarjeta encendido y vigilando.")
    bucle()


if __name__ == "__main__":
    main()
