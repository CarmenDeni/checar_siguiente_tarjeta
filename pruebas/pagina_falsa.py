"""Imitación mínima de kokora.mx/admin/cursor para probar el robot sin tocar el sitio real."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

S = {"libre": True, "respuesta": "PENDIENTES ANOTADOS EN BITACORA. Terminé la tarjeta 4.3a.", "n": 1, "recibidos": []}

LOGIN = """<html><body><h1>kokora Admin</h1>
<label>Usuario <input aria-label="Usuario" id="u"></label>
<label>Contraseña <input aria-label="Contraseña" type="password" id="p"></label>
<button onclick="fetch('/login',{method:'POST',body:u.value+':'+p.value}).then(r=>{if(r.ok)location='/admin/cursor?c=plan-plataforma-2'})">Entrar</button>
</body></html>"""


def pagina():
    letrero = ("Cursor está libre: lo que mandes ahora le llega de inmediato." if S["libre"]
               else "Cursor está trabajando en tu mensaje. Cuando termine, aparece aquí abajo.")
    items = "".join(f"<div>Tú · 26/11 07:3{i}</div><p>{m[:30]}</p>" for i, m in enumerate(reversed(S["recibidos"])))
    return f"""<html><body><div>{letrero}</div>
<textarea id="t" placeholder="Por ejemplo: siguiente tarjeta"></textarea>
<button onclick="fetch('/mandar',{{method:'POST',body:t.value}}).then(()=>location.reload())">Mandar a Cursor</button>
<h3>Lo último, lo más nuevo primero</h3>{items if not S["libre"] else ""}
<div>Cursor · 26/11 07:{S['n']:02d}</div><p>{S['respuesta']}</p>
<div>Tú · 26/11 06:00</div><p>Mensaje viejo que dice REVISA AHORA ANTES DE CONTINUAR</p>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        if self.path.startswith("/_control"):
            return self.send(200, json.dumps(S))
        if self.path.startswith("/admin/cursor"):
            if "ok=1" not in (self.headers.get("Cookie") or ""):
                return self.send(302, "", {"Location": "/admin"})
            return self.send(200, pagina())
        self.send(200, LOGIN)

    def do_POST(self):
        cuerpo = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if self.path == "/login":
            if cuerpo == "admin:secreta":
                return self.send(200, "ok", {"Set-Cookie": "ok=1; Path=/"})
            return self.send(401, "no")
        if self.path == "/mandar":
            S["recibidos"].append(cuerpo)
            S["libre"] = False
            return self.send(200, "ok")
        if self.path == "/_set":
            S.update(json.loads(cuerpo))
            return self.send(200, "ok")


ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever()
