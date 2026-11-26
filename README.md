# Robot «siguiente tarjeta»

Vigila `https://kokora.mx/admin/cursor?c=plan-plataforma-2` cada minuto, sin IA:

- Si Cursor está **trabajando**, espera.
- Si Cursor está **libre** y su respuesta más nueva **no** trae `REVISA AHORA ANTES DE CONTINUAR`, escribe el mensaje de «Siguiente tarjeta…» y pica **Mandar a Cursor**. Luego confirma que la página lo recibió.
- Si la respuesta **sí** trae esa frase, no manda nada y te avisa por WhatsApp (CallMeBot): `🚨🚨 NECESITO TU ATENCION PARA CONTINUAR 🚨🚨`. Repite el aviso cada 30 minutos (de noche no repite). En cuanto Cursor da una respuesta nueva sin la frase, el robot sigue solo.
- Nunca manda dos veces a la misma respuesta y como máximo hace 6 envíos por hora.
- Te avisa si algo se traba (el login falla, la página cambió o el envío no se confirma). Esos avisos se guardan de 22:00 a 7:00 y te llegan juntos a las 7.

Entra con tu usuario y contraseña del admin usando un navegador Chromium sin pantalla. Usa unos 300–400 MB de RAM; en Railway cuesta unos pocos dólares al mes.

## Ponerlo en Railway

1. En Railway: **New Project → Deploy from GitHub repo →** este repositorio. El `Dockerfile` ya está listo.
2. En **Variables** pon:
   - `KOKORA_PASSWORD` = tu contraseña del admin (`KOKORA_USUARIO` es `admin` por defecto)
   - `CALLMEBOT_PHONE` = tu número con lada, por ejemplo `+5215512345678`
   - `CALLMEBOT_APIKEY` = tu apikey de CallMeBot
   - `CLAVE_ADMIN` = cualquier palabra secreta para el control remoto
3. En **Settings → Networking → Generate Domain**. Opcional: en **Volumes** agrega un volumen montado en `/data` para que recuerde su estado entre reinicios.

Al arrancar te llega «✅ Robot siguiente tarjeta encendido y vigilando».

## Control remoto (desde el celular)

Cambia `TU-ROBOT` por tu dominio de Railway y `CLAVE` por tu `CLAVE_ADMIN`:

- Ver qué está haciendo: `https://TU-ROBOT.up.railway.app/estado?clave=CLAVE`
- Pausar: `.../pausar?clave=CLAVE`
- Reanudar: `.../reanudar?clave=CLAVE`
- Revisar ya: `.../aviso?clave=CLAVE`. El agente de kokora.mx puede llamar esta dirección al terminar para que el robot reaccione en segundos.
- Probar el WhatsApp: `.../prueba-whatsapp?clave=CLAVE`

## Variables opcionales

| Variable | Por defecto | Qué hace |
|---|---|---|
| `CONVERSACION` | `plan-plataforma-2` | conversación que vigila |
| `INTERVALO_SEGUNDOS` | `60` | cada cuánto revisa |
| `MAX_ENVIOS_HORA` | `6` | freno de seguridad |
| `REPETIR_ALERTA_MIN` | `30` | cada cuánto repite la alerta 🚨 |
| `SILENCIO_DESDE` / `SILENCIO_HASTA` | `22` / `7` | horario sin avisos de falla |
| `MODO_PRUEBA` | `no` | con `si`, solo anota lo que haría y no manda nada |
| `MENSAJE` | el texto de «Siguiente tarjeta…» | lo que escribe |

## Probarlo en tu compu

```bash
pip install -r requirements.txt && python -m playwright install chromium
python pruebas/pagina_falsa.py &   # imitación de la página (usuario admin, contraseña secreta)
KOKORA_URL=http://127.0.0.1:8765 KOKORA_PASSWORD=secreta CLAVE_ADMIN=x INTERVALO_SEGUNDOS=5 python robot.py
```
