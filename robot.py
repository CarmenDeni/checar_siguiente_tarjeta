"""Robot que vigila https://kokora.mx/admin/cursor y manda «siguiente tarjeta» cuando Cursor queda libre."""

import hashlib
import html
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
            "pendientes_noche": [], "prueba": MODO_PRUEBA, "avisos": True, "silencio_on": True,
            "alerta_noche": True}
    try:
        with open(ESTADO_ARCHIVO) as f:
            base.update(json.load(f))
    except Exception:
        pass
    return base


E = cargar_estado()
if MODO_PRUEBA:
    E["prueba"] = True


def guardar():
    try:
        with open(ESTADO_ARCHIVO, "w") as f:
            json.dump(E, f, ensure_ascii=False, indent=1)
    except Exception as ex:
        log("no pude guardar estado:", ex)


def es_silencio() -> bool:
    if not E.get("silencio_on", True):
        return False
    h = datetime.now(TZ).hour
    if SILENCIO_DESDE > SILENCIO_HASTA:
        return h >= SILENCIO_DESDE or h < SILENCIO_HASTA
    return SILENCIO_DESDE <= h < SILENCIO_HASTA


def whatsapp(texto: str, forzar: bool = False) -> bool:
    log("WHATSAPP:", texto)
    hora = datetime.now(TZ).strftime("%H:%M")
    if not (PHONE and APIKEY):
        E["ultimo_wa"] = f"{hora} ❌ No mandé: falta CALLMEBOT_PHONE o CALLMEBOT_APIKEY en Railway"
        return False
    if not (forzar or E.get("avisos", True)):
        E["ultimo_wa"] = f"{hora} No mandé: los avisos por WhatsApp están apagados"
        return False
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": PHONE, "text": texto, "apikey": APIKEY})
    motivo = ""
    for intento in range(1 if forzar else 3):
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                cuerpo = r.read(3000).decode("utf-8", "ignore")
            limpio = " ".join(re.sub(r"<[^>]+>", " ", cuerpo).split())
            if "error" not in limpio.lower() and "invalid" not in limpio.lower():
                E["ultimo_wa"] = f"{hora} ✅ CallMeBot lo aceptó"
                return True
            motivo = limpio[:160]
        except Exception as ex:
            motivo = str(ex)[:160]
            log("callmebot falló:", ex)
        if not forzar:
            time.sleep(5 * (intento + 1))
    E["ultimo_wa"] = f"{hora} ❌ CallMeBot no lo mandó: {motivo}"
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
        for _ in range(45):
            pg.wait_for_timeout(1000)
            cuerpo = plano(pg.inner_text("body"))
            if "incorrecta" in cuerpo or pg.locator("input[type=password]").count() == 0:
                break
        pg.wait_for_timeout(1500)
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
            if E.get("alerta_noche", True) or not es_silencio():
                whatsapp(ALERTA_ATENCION)
            else:
                E["ultima_alerta"] = 0
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

    if E.get("prueba"):
        if E.get("prueba_huella") != d["huella"]:
            log("MODO PRUEBA: aquí mandaría «siguiente tarjeta»")
            E["prueba_huella"] = d["huella"]
        E["ultimo_estado"] = "Modo prueba: habría mandado (no mandé nada)"
        return

    log("Cursor libre → mando siguiente tarjeta. Respuesta:", d["cabecera"])
    nav.mandar(MENSAJE)
    d2 = leer_pagina(nav.texto())
    if d2["ocupado"] or d2["primero"] == "tu":
        E["ultimo_enviado"], E["enviado_en"] = d["huella"], ahora
        E["envios"].append(ahora)
        hoy = datetime.now(TZ).strftime("%Y-%m-%d")
        E["exitos_total"] = E.get("exitos_total", 0) + 1
        E["exitos_dia"] = {hoy: E.get("exitos_dia", {}).get(hoy, 0) + 1}
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


def galleta():
    return hashlib.sha256(("robot:" + CLAVE).encode()).hexdigest()[:32]


CABEZA = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Robot tarjeta"><title>Robot tarjeta</title><style>
body{font-family:system-ui,sans-serif;background:#f5f3ef;margin:0;padding:16px;color:#222}
main{max-width:520px;margin:auto} h1{font-size:20px}
.card{background:#fff;border-radius:14px;padding:14px;margin:12px 0;box-shadow:0 1px 3px #0001}
.fila{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #eee}
.fila:last-child{border:0} small{display:block;color:#777;margin-top:2px}
.sw{min-width:64px;padding:8px;border-radius:20px;border:0;background:#ddd;font-weight:600}
.sw.on{background:#7b6cc4;color:#fff}
.accion{width:100%;padding:12px;margin:4px 0;border-radius:10px;border:1px solid #7b6cc4;background:#fff;color:#5a4aa8;font-size:15px}
.accion.llena{background:#7b6cc4;color:#fff}
input[type=password]{width:100%;box-sizing:border-box;padding:12px;font-size:16px;border:1px solid #ccc;border-radius:10px}
.aviso{background:#eee9fb;color:#3d2f86}
.estado{font-size:17px;font-weight:600} .err{color:#b3261e}
</style></head><body><main>"""

LOGIN = """<h1>Robot «siguiente tarjeta»</h1><form method="post" action="/entrar" class="card">
<label>Contraseña<br><input type="password" name="clave" autocomplete="current-password" autofocus></label>
<p><label><input type="checkbox" name="recordar" checked> Recordarme en este celular</label></p>
{error}<button class="accion llena">Entrar</button></form>"""


INTERRUPTORES = {
    "pausado": ("Pausar el robot", "Sí = deja de mandar «siguiente tarjeta» (sigue mirando la página).", False),
    "prueba": ("Modo prueba", "Sí = solo dice lo que haría, no manda nada a Cursor.", False),
    "avisos": ("Mandarme WhatsApp", "Te escribe solo si Cursor pide «REVISA AHORA» (🚨) o si el robot se traba. "
               "No = ningún WhatsApp.", True),
    "silencio_on": ("Callar avisos de falla de 22 a 7", "Sí = si se traba de noche, te lo cuenta a las 7 am.", True),
    "alerta_noche": ("La 🚨 sí me despierta", "Sí = la 🚨 llega aunque sea de noche. No = espera a las 7 am.", True),
}


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
        if u.path == "/salud":
            return self.responder(200, {"ok": True})
        if u.path == "/entrar" and self.command == "POST":
            datos = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            if CLAVE and datos.get("clave", [""])[0] == CLAVE:
                edad = "; Max-Age=31536000" if datos.get("recordar") else ""
                self.send_response(303)
                self.send_header("Set-Cookie", f"robot={galleta()}; Path=/; HttpOnly; Secure; SameSite=Strict{edad}")
                self.send_header("Location", "/panel")
                return self.end_headers()
            return self.pagina(LOGIN.format(error="<p class='err'>Contraseña incorrecta</p>"))
        if u.path == "/salir":
            self.send_response(303)
            self.send_header("Set-Cookie", "robot=; Path=/; Max-Age=0")
            self.send_header("Location", "/")
            return self.end_headers()
        if not self.autorizado(q):
            if u.path in ("/", "/panel"):
                return self.pagina(LOGIN.format(error=""))
            return self.responder(403, {"error": "clave incorrecta"})
        if u.path in ("/", "/panel"):
            return self.panel()
        if u.path == "/accion" and self.command == "POST":
            que = q.get("que", [""])[0]
            if que in INTERRUPTORES:
                E[que] = not E.get(que, INTERRUPTORES[que][2])
            hora = datetime.now(TZ).strftime("%H:%M")
            if que == "revisar":
                E["aviso_panel"] = f"{hora} Pediste revisar: en unos segundos cambia «última revisión»."
                despertar.set()
            elif que == "probar_wa":
                whatsapp("✅ Robot siguiente tarjeta: prueba de aviso", forzar=True)
                E["aviso_panel"] = "Prueba de WhatsApp: " + E.get("ultimo_wa", "")
            elif que == "reenviar":
                E["ultimo_enviado"] = None
                libre = (E.get("ultima_lectura") or {}).get("libre")
                E["aviso_panel"] = (f"{hora} Mandaré «siguiente tarjeta» ahora mismo." if libre else
                                    f"{hora} Cursor está trabajando: lo mandaré en cuanto quede libre.")
                despertar.set()
            guardar()
            self.send_response(303)
            self.send_header("Location", "/panel")
            return self.end_headers()
        if u.path == "/pausar":
            E["pausado"] = True
        elif u.path == "/reanudar":
            E["pausado"] = False
            despertar.set()
        elif u.path in ("/aviso", "/revisar"):
            despertar.set()
        elif u.path == "/prueba-whatsapp":
            whatsapp("✅ Robot siguiente tarjeta: prueba de aviso", forzar=True)
        elif u.path != "/estado":
            return self.responder(404, {"error": "no existe"})
        guardar()
        vista = {k: E[k] for k in ("pausado", "ultimo_estado", "ultima_revision", "fallas", "pendientes_noche")}
        vista["ultima_lectura"] = E.get("ultima_lectura")
        vista["silencio_ahora"] = es_silencio()
        self.responder(200, vista)

    def autorizado(self, q):
        if CLAVE and q.get("clave", [""])[0] == CLAVE:
            return True
        galletas = self.headers.get("Cookie") or ""
        return bool(CLAVE) and f"robot={galleta()}" in galletas.replace(" ", "").split(";")

    def pagina(self, cuerpo):
        b = (CABEZA + cuerpo + "</main></body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def panel(self):
        filas = ""
        for k, (titulo, ayuda, defecto) in INTERRUPTORES.items():
            on = E.get(k, defecto)
            filas += (f'<form method="post" action="/accion?que={k}" class="fila"><div><b>{titulo}</b>'
                      f'<small>{ayuda}</small></div><button class="sw {"on" if on else ""}">'
                      f'{"Sí" if on else "No"}</button></form>')
        botones = "".join(
            f'<form method="post" action="/accion?que={k}"><button class="accion">{t}</button></form>'
            for k, t in (("revisar", "Revisar ahora"), ("probar_wa", "Probar WhatsApp"),
                         ("reenviar", "Volver a mandar «siguiente tarjeta»")))
        lec = E.get("ultima_lectura") or {}
        cursor = "libre" if lec.get("libre") else "trabajando" if lec.get("ocupado") else "sin dato"
        esc = html.escape
        cuerpo = f"""<h1>Robot «siguiente tarjeta»</h1>
<div class="card"><div class="estado {"err" if E["fallas"] else ""}">{esc(str(E["ultimo_estado"]))}</div>
<small>Cursor: {cursor} · última revisión {esc(str(E.get("ultima_revision") or "—"))[11:16]}
· fallas seguidas: {E["fallas"]}{" · horario de silencio" if es_silencio() else ""}</small>
<small>Respuesta más nueva: {esc(str(lec.get("cabecera") or "—"))}<br>{esc((lec.get("inicio") or "")[:120])}</small></div>
{f'<div class="card aviso">{esc(E["aviso_panel"])}</div>' if E.get("aviso_panel") else ""}
<div class="card"><b>Cursor activado con éxito</b><small>Hoy: {E.get("exitos_dia", {}).get(datetime.now(TZ).strftime("%Y-%m-%d"), 0)}
· desde que arrancó el robot: {E.get("exitos_total", 0)}</small>
<small>Último WhatsApp: {esc(E.get("ultimo_wa") or "ninguno todavía")}</small></div>
<div class="card">{filas}</div><div class="card">{botones}</div>
<small>Se actualiza solo cada 30 s · <a href="/salir">Salir</a></small><script>setTimeout(()=>location.reload(),30000)</script>"""
        self.pagina(cuerpo)

    do_GET = manejar
    do_POST = manejar


def main():
    log(f"Robot arrancó. Vigilo {PAGE_URL} cada {INTERVALO}s. Modo prueba: {MODO_PRUEBA}")
    srv = ThreadingHTTPServer(("0.0.0.0", int(ENV("PORT", "8000"))), Control)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bucle()


if __name__ == "__main__":
    main()
