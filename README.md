# etoro-monitor

Avisos automáticos por **Telegram** cuando los usuarios de eToro que tú elijas
mueven su cartera: **compran, venden, amplían o reducen** posiciones.

* **Sin KYC, sin login, sin API key, sin cuenta en eToro.** Solo se leen los
  endpoints *públicos* que carga la propia web
  (`https://www.etoro.com/people/<usuario>/portfolio`). Nada de esto toca
  cuentas privadas: únicamente carteras que sus dueños han hecho públicas.
* Funciona solo, en la nube, con **GitHub Actions** (gratis), cada **30
  minutos**. No necesitas tener un ordenador encendido.
* A quién sigues se decide en **`watchlist.md`**: un usuario por línea.
* Cuando un usuario tiene la cartera privada, el programa lo detecta y lo
  avisa en el log en vez de inventarse datos.

---

## 1. Cómo funciona (en 30 segundos)

1. Cada 30 minutos, GitHub Actions ejecuta un script de Python.
2. El script traduce cada nombre de usuario a su `CID` interno de eToro
   (`/api/logininfo/v1.1/users/<usuario>`).
3. Descarga la cartera pública: activos, pesos y **todas las posiciones
   abiertas**, cada una con su `PositionID`.
4. Lo compara con la foto guardada en `state/state.json`.
5. Si hay diferencias, manda un mensaje a Telegram y actualiza el estado.

**¿Por qué es fiable?** Porque en eToro cada compra crea un `PositionID` nuevo
e inmutable:

| Lo que pasa en tu foto guardada | Lo que ha hecho el usuario |
|---|---|
| Aparece un `PositionID` nuevo | Ha **comprado** (abre o amplía) |
| Desaparece un `PositionID` | Ha **vendido** (cierra) |
| Mismo `PositionID` con menos unidades | Ha **reducido** |
| Mismo `PositionID` con más unidades | Ha **ampliado** |

El precio subiendo o bajando **no** cambia ni los identificadores ni las
unidades, así que no genera falsos avisos ("el mercado ha movido la cartera"
no es una noticia). Eso sí: cuanto más frecuente sea el cron, menos probable
es perderse algo.

---

## 2. Puesta en marcha (10 minutos)

### 2.1 Crea tu bot de Telegram

1. Abre Telegram y habla con [@BotFather](https://t.me/BotFather).
2. `/newbot` → te pide un nombre y un usuario → te devuelve un **token** del
   estilo `123456789:AAH...`. Eso es `TELEGRAM_BOT_TOKEN`.
3. **Escríbele algo al bot** (o añádelo a un grupo). Un bot no puede
   iniciar la conversación: si no le has hablado nunca, Telegram no le deja
   enviarte nada.

### 2.2 Averigua tu `chat_id`

* **Chat privado:** habla con [@userinfobot](https://t.me/userinfobot) y te
  dice tu `Id` (un número, puede ser negativo en grupos).
* **Grupo:** añade el bot al grupo, escribe cualquier mensaje y abre
  `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` en el navegador. Busca
  `"chat":{"id":-1001234567890`.

### 2.3 Apunta a quién quieres seguir

Abre `watchlist.md` y escribe **un usuario por línea** (ver
[sección 3](#3-elegir-a-quién-vigilar-watchlistmd)). Ya viene con dos nombres
de ejemplo que debes sustituir por los tuyos.

### 2.4 Sube el proyecto a GitHub

```bash
git init
git add .
git commit -m "Monitor de carteras públicas de eToro"
git branch -M main
git remote add origin git@github.com:<tu-usuario>/etoro-monitor.git
git push -u origin main
```

### 2.5 Configura los secretos

En GitHub: **Settings → Secrets and variables → Actions → New repository
secret**:

| Nombre | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | el token de BotFather |
| `TELEGRAM_CHAT_ID` | tu chat id |

Opcionalmente, `ETORO_USERS` si prefieres no tener tu lista de seguimiento
dentro del repositorio (ver [sección 6](#6-privacidad-qué-datos-hay-y-dónde)).

### 2.6 Comprueba que todo funciona

1. Pestaña **Actions** → **Probar Telegram** → *Run workflow*.
   Debe llegarte un mensaje al móvil. Si no llega, revisa el token y el chat id.
2. **Actions** → **Diagnóstico eToro** → *Run workflow*. Te dice si los
   endpoints de eToro responden desde GitHub (puede tardar unos segundos).
3. **Actions** → **Monitor eToro -> Telegram** → *Run workflow*.
   La primera vez recibirás un mensaje de "línea base" por cada persona de
   `watchlist.md`, con sus mayores pesos. Así confirmas que llega todo bien.

A partir de ahí no tienes que hacer nada más: el cron `*/30 * * * *` se
encarga de todo.

---

## 3. Elegir a quién vigilar: `watchlist.md`

Abre **`watchlist.md`** y escribe **un usuario por línea**. El usuario es lo
que va en la URL después de `/people/`:

```
https://www.etoro.com/people/usuario_ejemplo/portfolio
                         ^^^^^^^^^^^^^^^
```

Así queda el fichero:

```markdown
# Usuarios a los que sigo

usuario_ejemplo
otro_usuario
# desactivado_de_momento
```

Guarda el fichero y ya está: la siguiente ejecución lo detecta sola. No hay
que tocar YAML, ni comas, ni nada más.

**Detalles útiles:**

* Las líneas que empiezan por `#` se ignoran, así que sirven para poner
  notas o para **desactivar temporalmente** a alguien sin borrarlo.
* Las mayúsculas dan igual (`UsuarioEjemplo` = `usuarioejemplo`) y si cambias
  solo eso tampoco pierdes la memoria del bot.
* Se admiten también, por comodidad, viñetas (`- usuario_ejemplo`) y la URL
  completa (`https://www.etoro.com/people/usuario_ejemplo`).

Puedes ver a quién estás siguiendo con:

```bash
python -m etoro_monitor users
```

No hace falta buscar el CID a mano: se resuelve en cada ejecución. Para
comprobar que un usuario existe y su cartera es pública:

```bash
python -m etoro_monitor resolve usuario_ejemplo
# usuario_ejemplo -> username=UsuarioEjemplo CID=1234567 GCID=7654321 nombre=(oculto)
```

El `CID` es el identificador interno de la cartera real: lo traduce el
programa solo, no lo busques a mano. El nombre completo solo aparece si esa
persona ha decidido mostrarlo en su perfil público.

---

## 4. Uso en tu ordenador (opcional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="123456:AA..."
export TELEGRAM_CHAT_ID="123456789"

python -m etoro_monitor check --dry-run         # mira sin enviar nada
python -m etoro_monitor check                # revisa y manda a Telegram
python -m etoro_monitor users                   # a quién está siguiendo
python -m etoro_monitor show usuario_ejemplo    # volcado de la cartera actual
python -m etoro_monitor resolve usuario_ejemplo # nombre -> CID
python -m etoro_monitor ping                    # ¿responden los endpoints?
python -m etoro_monitor ping --username otro_usuario
python -m etoro_monitor notify-test             # mensaje de prueba
python -m pytest tests -q                       # tests
```

Para que se ejecute solo cada 30 minutos en tu máquina, un cron sencillo:

```cron
*/30 * * * * cd /ruta/etoro-monitor && /ruta/.venv/bin/python -m etoro_monitor check >> monitor.log 2>&1
```

---

## 5. Avisos, límites y letra pequeña

* **No hay KYC ni login en ningún sitio.** Todo son peticiones GET anónimas a
  la web pública de eToro. No se usa tu cuenta para nada.
* **Términos de uso.** Esto lee datos públicos de eToro con la misma API que
  usa su web. Es scraping: eToro podría cambiar los endpoints, bloquear el
  acceso o considerarlo contrario a sus términos. Úsalo de forma moderada y
  bajo tu responsabilidad. El proyecto está pensado para uso personal.
* **Bloqueos.** eToro usa DataDome/Cloudflare. Si detectamos un captcha, el
  programa **no** interpreta "no hay datos" como "lo ha vendido todo" (si lo
  hiciera, te inundaría de avisos falsos): avisa del problema y no toca el
  estado. Sube `http.delay_seconds` o espacia el cron.
* **Frecuencia de GitHub.** Está puesto cada 30 minutos (`*/30 * * * *`), que
  va bien tanto en repositorio público como privado: son ~1.440 ejecuciones
  al mes, dentro de los 2.000 minutos/mes gratis de Actions. El mínimo que
  admite GitHub es 5 minutos, y siempre con retrasos en horas punta. Si tu
  repo es **público** (no gasta cuota) y quieres más inmediatez, cámbialo a
  `*/10` o `*/5` en `.github/workflows/monitor.yml`.
* **Repos inactivos.** GitHub desactiva los crons de repositorios que llevan
  60 días sin actividad. Cualquier commit (o entrar en Actions y lanzarlo a
  mano) lo reactiva.
* **Carteras privadas.** Si un usuario pone su cartera en privado, verás un
  aviso de error y sus posiciones dejarán de actualizarse.
* **Posiciones y no unidades monetarias.** El API pública no da euros ni
  dólares de cada operación: da unidades y precio de entrada. Los avisos
  muestran eso y el peso del activo en la cartera (que es exactamente lo que
  se ve en la web de eToro).
* **Operaciones entre dos ejecuciones.** Si alguien compra y vende algo en
  menos de 30 minutos, solo se ve el resultado final.

---

## 6. Privacidad: qué datos hay y dónde

Este repositorio **no contiene ningún dato personal ni privado**: ni
tokens, ni correos, ni rutas de tu ordenador, ni identificadores de nadie.
Todo el código, los tests y esta documentación usan nombres ficticios
(`usuario_ejemplo`, CID `1234567`).

**Lo único que dice algo de ti es `watchlist.md`**: es la lista de cuentas
que sigues, y por definición son tus intereses. Son perfiles **públicos** de
eToro (si fueran privados el programa lo detecta y no lee nada), pero sigue
siendo información tuya. Si el repositorio va a ser **público**, tienes dos
opciones:

**Opción A — dejar el repositorio privado.** GitHub Actions funciona igual
gratis y nadie ve tu lista.

**Opción B — sacar la lista del repositorio.** Guarda los usuarios en un
**secreto** de GitHub llamado `ETORO_USERS` (separados por comas o saltos de
línea) y deja `watchlist.md` vacío o con comentarios:

```
Settings → Secrets and variables → Actions → New repository secret
   Nombre:  ETORO_USERS
   Valor:   usuario_uno, usuario_dos
```

Si `ETORO_USERS` existe, manda sobre `watchlist.md`. Así el repositorio puede
ser público sin que se vea a quién sigues. El precio a pagar es que para
añadir a alguien tienes que editar el secreto en lugar del `.md`.

### Qué NO se guarda nunca

* **`state/state.json`** guarda solo identificadores de posiciones y
  unidades, y **no contiene precios ni el valor de la cartera**. Al subirlo a
  GitHub, ten en cuenta que el workflow lo irá actualizando con commits.
  Si prefieres que no se versione, añade `state/` al `.gitignore` y guarda el
  estado en la caché de Actions (o acepta volver a la línea base si se
  pierde).
* **No se guardan credenciales de eToro** porque no existen: no hay login.
* **`TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`** viven solo como secretos de
  GitHub, nunca en un fichero.
* Los ficheros volátiles (`state/state.runtime.json`) están en `.gitignore`.

### Repaso rápido antes de publicar

<!-- privacidad:ignorar:inicio -->
```bash
# ¿Hay rutas de mi ordenador o correos con dominio propio?
grep -rnE "/home/[a-z]|/Users/[A-Za-z]|C:\\\\Users" . --exclude-dir=.git

# ¿A quién estoy siguiendo? (esto es lo único personal)
cat watchlist.md

# ¿El estado tiene datos de carteras?
cat state/state.json
```

Los tests incluyen un guardián automático (`tests/test_privacidad.py`), así
que basta con ejecutar `python -m pytest tests -q` antes de publicar.
<!-- privacidad:ignorar:fin -->

---

## 7. Estructura del proyecto

```
etoro-monitor/
├── .github/workflows/
│   ├── monitor.yml              # el cron: revisa y avisa
│   ├── test-telegram.yml        # manda un mensaje de prueba
│   ├── test-etoro.yml           # diagnóstico de los endpoints
│   └── test-instrumentos.yml    # catálogo de activos
├── etoro_monitor/
│   ├── client.py                # endpoints públicos de eToro, reintentos, anti-bloqueo
│   ├── models.py                # Usuario, Instrumento, Posición, Cambio
│   ├── diff.py                  # comparación de fotos de cartera
│   ├── monitor.py               # orquestación: leer -> comparar -> avisar
│   ├── render.py                # redacción de los mensajes
│   ├── state.py                 # estado persistente entre ejecuciones
│   ├── telegram.py              # envío a Telegram
│   ├── watchlist.py             # lector del watchlist.md
│   ├── config.py                # une watchlist.md + watchlist.yml
│   └── cli.py                   # línea de comandos
├── state/state.json             # memoria del bot (se versiona)
├── tests/                       # 56 tests, sin red y con datos ficticios
├── watchlist.md                 # ⬅ A QUIÉN SIGUES (lo único que tocas tú)
├── watchlist.yml                # ajustes técnicos (pausas, avisos)
└── requirements.txt
```

### Los endpoints que usa

| Endpoint | Para qué |
|---|---|
| `/api/logininfo/v1.1/users/<usuario>` | nombre de usuario → `CID` |
| `/sapi/trade-data-real/live/public/portfolios?cid=` | activos y espejos de la cartera |
| `/sapi/trade-data-real/live/public/portfolios/exposure?cid=` | peso (%) de cada activo |
| `/sapi/trade-data-real/live/public/positions?cid=&instrumentId=` | cada posición abierta con su `PositionID` |
| `/sapi/instrumentsmetadata/V1.1/instruments/<id>` | nombre y ticker del activo |

### El estado

`state/state.json` guarda **solo datos estables** (CID, instrumentos conocidos y
las posiciones con sus unidades). No guarda precios ni valoraciones, así que el
workflow solo hace commit cuando **alguien ha operado de verdad**: nada de
cientos de commits al día. Los datos volátiles van a `state/state.runtime.json`,
que está en `.gitignore`.

Repositorio recién clonado: `state/state.json` viene vacío, sin carteras de
nadie (ver [sección 6](#6-privacidad-qué-datos-hay-y-dónde)).

---

## 8. Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| No llega nada a Telegram | No le has escrito nunca al bot, o el `chat_id` es incorrecto. Prueba el workflow *Probar Telegram*. |
| Añado gente a `watchlist.md` y no se sigue | Revisa con `python -m etoro_monitor users` que se leen bien: cada usuario en su propia línea, sin texto alrededor. |
| "eToro ha devuelto un captcha" | Has ido demasiado rápido. Espera unas horas o sube `delay_seconds`. |
| "la cartera es privada" | Ese usuario ha cerrado su cartera pública: no hay nada que leer. |
| "No hay ningún usuario que seguir" | `watchlist.md` no tiene ninguna línea con un usuario (¿está todo comentado con `#`?) y no has definido `ETORO_USERS`. |
| "no encuentra watchlist.yml" | Falta `watchlist.yml` en la raíz del repo. |
| Avisos repetidos | Se perdió el commit de `state/state.json` (el workflow no tiene permiso de escritura: revisa `permissions: contents: write`). |
| El cron no se ejecuta | GitHub desactiva crons tras 60 días sin actividad, o el repo es privado y se agotó la cuota. |
