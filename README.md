# etoro-monitor

Avisos automáticos por **Telegram** cuando los usuarios de eToro que tú elijas
**compran, venden, amplían o reducen** posiciones.

* **Sin KYC, sin login, sin API key, sin cuenta en eToro.** Solo se leen los
  endpoints *públicos* que carga la propia web
  (`https://www.etoro.com/people/<usuario>/portfolio`).
* Solo avisa de **operaciones reales**. Que el mercado suba o baje **no**
  genera ningún aviso.
* Funciona solo, en la nube, con **GitHub Actions**, disparado por
  **cron-job.org**.
* A quién sigues se decide en **`watchlist.md`**: un usuario por línea.
* El **código puede ser público** (minutos de Actions ilimitados y gratis)
  mientras el estado, que contiene a quién sigues y sus datos, vive en un
  **repositorio privado aparte**. Ver [sección 2.6](#26-el-estado-en-un-repositorio-privado-recomendado).

---

## 1. Cómo funciona (en 30 segundos)

1. cron-job.org llama a la API de GitHub y despierta el workflow.
2. El script traduce cada nombre de usuario a su `CID` interno de eToro.
3. Descarga la cartera pública de cada persona.
4. Compara con la foto que guardó la vez anterior.
5. Si hay diferencias, manda un mensaje a Telegram y actualiza la foto.

### Por qué es fiable (y por qué no da falsos avisos)

Cada activo se resume en **un solo número que solo cambia cuando el usuario
opera**: el **total de unidades**.

| Lo que ve el monitor | Qué ha pasado |
|---|---|
| Aparece un activo que no tenía | **COMPRA** (o **ABRE CORTO**) |
| Desaparece un activo | **VENTA** (o **CIERRA CORTO**) |
| Tiene más unidades | **AMPLÍA** |
| Tiene menos unidades | **REDUCE** |

Las unidades **no cambian** porque el precio suba o baje, ni porque se mueva el
resto de la cartera. Por eso el mercado no puede generar un aviso falso.

El **peso** de cada activo (`Invested`) sí se mueve con el mercado, así que se
usa **solo como contexto** en el mensaje, nunca para decidir si hubo operación.

Los **cortos** se anuncian siempre en negrita y con la palabra CORTO:

```
🔔 Usuario Ejemplo ha movido su cartera
02/10/2026 16:03 UTC · 2 operaciones en 2 activos

🟢 COMPRA · Alphabet (GOOG)  (nueva en cartera)
   peso en cartera: 5,40%
🔴 CIERRA CORTO · EUR/USD
   peso antes: 0,71%
```

---

## 2. Puesta en marcha

### 2.1 Crea tu bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → te da un
   **token** (`123456789:AAH...`).
2. **Escríbele algo al bot.** Un bot no puede iniciar la conversación: si no
   le has hablado nunca, Telegram no le deja enviarte nada.

### 2.2 Averigua tu `chat_id`

* **Privado:** habla con [@userinfobot](https://t.me/userinfobot).
* **Grupo:** añade el bot, escribe algo y abre
  `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`. Busca `"chat":{"id":-100...`.

### 2.3 Apunta a quién quieres seguir

En **`watchlist.md`**, un usuario por línea (el trozo de la URL tras `/people/`):

```markdown
# Usuarios a los que sigo

usuario_ejemplo
otro_usuario
```

Las líneas con `#` se ignoran, así que sirven para desactivar a alguien sin
borrarlo. Si el repositorio es **público**, deja este fichero vacío y pon la
lista en el secreto `ETORO_USERS` (ver sección 6).

### 2.4 Sube el proyecto a GitHub

Vacío, **sin** README ni licencia, y luego sube el contenido del ZIP.

> Ojo: si arrastras la carpeta contenedora, los workflows acaban en
> `proyecto/.github/...` y **no se ejecutarán nunca**. En la raíz del repo debe
> haber `.github/`, `etoro_monitor/`, `tests/`, `state/`.
> Extrae el ZIP y usa **Ctrl+A** dentro de la carpeta extraída.

### 2.5 Configura los secretos

**Settings → Secrets and variables → Actions → New repository secret**:

| Secreto | Valor | ¿Obligatorio? |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | el token de BotFather | Sí |
| `TELEGRAM_CHAT_ID` | tu chat id | Sí |
| `ETORO_USERS` | `usuario_uno, usuario_dos` | Solo si el repo es público y no usas `watchlist.md` |
| `STATE_REPO` | `tu-usuario/etoro-monitor-estado` | Recomendado (ver 2.6) |
| `STATE_DEPLOY_KEY` | la clave privada SSH del repo de estado | Recomendado (ver 2.6, opción A) |
| `STATE_REPO_TOKEN` | token de escritura sobre ese repo | Alternativa a la anterior (opción B) |

Si falta Telegram, el workflow falla con un mensaje claro en vez de terminar en
verde sin enviar nada.

### 2.6 El estado en un repositorio privado (recomendado)

El monitor necesita recordar la foto anterior de cada cartera. Esa foto incluye
a quién sigues, qué activos tiene cada uno y en qué cantidades. Si el
repositorio del código es público, **esos datos también serían públicos y
quedarían archivados para siempre en el historial de git**.

La solución: guardar el estado en un repositorio **privado** aparte. Así el
código puede ser público (minutos de Actions ilimitados y gratis) sin exponer
datos de nadie.

**Paso 1 — crea el repositorio de estado.**

Uno nuevo, **privado** y **vacío** (sin README ni licencia). Por ejemplo
`etoro-monitor-estado`. No importa si no tiene rama todavía: el workflow la
crea sola.

**Paso 2 — dale una credencial para poder escribirlo.**

Hay dos formas. Basta con una. La **deploy key** es la recomendada.

#### Opción A: deploy key (recomendado)

Una clave SSH que vale **solo para ese repositorio**. No caduca y no hay
forma de configurarla mal dándole más acceso del que le toca.

1. Genera el par de claves. En GitHub **Codespaces** (terminal en el
   navegador, gratis), o en cualquier ordenador con `ssh-keygen`:
   ```bash
   ssh-keygen -t ed25519 -N "" -C "etoro-monitor-estado" -f ./clave_estado
   ```
   Salen dos ficheros: `clave_estado` (privada) y `clave_estado.pub` (pública).

2. En el repositorio de **estado** (`etoro-monitor-estado`):
   **Settings → Deploy keys → Add deploy key**
   * *Title*: `etoro-monitor`
   * *Key*: el contenido de **`clave_estado.pub`**
   * ✅ **Allow write access** ← imprescindible, si no no podrá guardar

3. Añade los secretos en el repositorio del **código**:

| Secreto | Valor |
|---|---|
| `STATE_REPO` | `tu-usuario/etoro-monitor-estado` |
| `STATE_DEPLOY_KEY` | el contenido **completo** de `clave_estado` (privada) |

> El secreto debe llevar el fichero privado entero, incluidas las líneas
> `-----BEGIN OPENSSH PRIVATE KEY-----` y `-----END OPENSSH PRIVATE KEY-----`.
> Y **borra `clave_estado` de donde lo hayas generado** una vez pegado.

#### Opción B: token (alternativa)

Settings → Developer settings → Personal access tokens → **Fine-grained**:

* *Repository access*: solo `etoro-monitor-estado`.
* *Permissions* → **Contents: Read and write**.
* *Expiration*: ponle **No expiration**, o apúntate en el calendario cuándo
  caduca. Si caduca y no te enteras, el monitor dejará de avisar (aunque el
  aviso de fallo de la sección 4 te lo dirá).

| Secreto | Valor |
|---|---|
| `STATE_REPO` | `tu-usuario/etoro-monitor-estado` |
| `STATE_REPO_TOKEN` | el token |

#### Comprobación

A partir de ahí, el workflow clona ese repositorio al empezar, lee el estado,
lo actualiza y lo vuelve a subir. En los ficheros `state/` del repositorio del
código solo quedan las plantillas vacías.

En el log de la primera ejecución debe aparecer
`Estado en tu-usuario/etoro-monitor-estado, con clave SSH (deploy key)`
(o `con token`).

#### Si algo falla, hay un diagnóstico

En **Actions → Diagnóstico de la credencial de estado → Run workflow**. No
envía nada a Telegram ni toca el estado: solo revisa la credencial y prueba la
conexión, diciéndote qué está mal.

Entre otras cosas comprueba si el contenido del secreto es realmente una
clave, y **muestra la clave pública que corresponde a ese secreto**, para que
puedas compararla con la que pegaste en GitHub. Si no coinciden, no son la
misma pareja y hay que corregir una de las dos.

**Si no defines ninguno de los dos**, el estado se guarda aquí. Eso está bien si
este repositorio ya es **privado**.

> **Rama del estado:** por defecto `main`. Si tu repositorio de estado usa
> `master`, cambia `STATE_BRANCH` al principio de `.github/workflows/monitor.yml`.
> (El workflow hace el checkout de esa rama a propósito, así que funciona aunque
> el repositorio esté vacío o su rama por defecto sea otra.)

### 2.7 Configura cron-job.org

Un solo trabajo:

| Campo | Valor |
|---|---|
| URL | `https://api.github.com/repos/<usuario>/<repo>/actions/workflows/monitor.yml/dispatches` |
| Método | `POST` |
| Cabeceras | `Authorization: Bearer <TOKEN>`<br>`Accept: application/vnd.github+json`<br>`Content-Type: application/json`<br>`X-GitHub-Api-Version: 2022-11-28` |
| Cuerpo | `{"ref":"main"}` |

Respuesta correcta: **204 No Content**. Marca los 2xx como éxito.

El **token** es un *fine-grained token* (Settings → Developer settings) con
acceso **solo a ese repositorio** y permiso **Actions: read and write**. Es un
secreto que queda guardado en cron-job.org.

**La ventaja de cron-job.org:** entiende de zonas horarias y cambios de hora, así
que programas en tu hora local y ya está. Las ejecuciones entran como
`workflow_dispatch`, así que el horario de `watchlist.yml` no las bloquea.

### 2.8 Comprueba que funciona

1. **Actions → Probar Telegram** → debe llegarte un mensaje al móvil.
2. **Actions → Diagnóstico eToro** → te dice si eToro responde desde GitHub.
3. **Actions → Monitor eToro → Run workflow** con **`dry_run` marcado** → recorre
   las carteras y muestra los mensajes en el log **sin enviar nada**.
4. El mismo, sin `dry_run` → recibes la "línea base".
5. Otra vez → debe decir `sin operaciones` y `STATE_CHANGED=false`.

---

## 3. Uso en tu ordenador (opcional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="123456:AA..."
export TELEGRAM_CHAT_ID="123456789"
export ETORO_USERS="usuario_ejemplo"

python -m etoro_monitor check --dry-run         # mira sin enviar nada
python -m etoro_monitor check                   # revisa y manda a Telegram
python -m etoro_monitor users                   # a quién está siguiendo
python -m etoro_monitor show usuario_ejemplo    # cartera actual por pesos
python -m etoro_monitor resolve usuario_ejemplo # nombre -> CID
python -m etoro_monitor ping                    # ¿responden los endpoints?
python -m etoro_monitor notify-test             # mensaje de prueba
python -m pytest tests -q                       # 162 tests, sin red
```

---

## 4. Cómo se comporta ante errores

| Situación | Qué hace |
|---|---|
| Nombre mal escrito o cuenta borrada | Aviso "no he podido leer la cartera", exit 1, y lo reintenta cada pasada |
| Cartera puesta en privado | Igual: aviso y exit 1 |
| eToro no responde | 4 intentos con espera creciente (~19 s) → aviso |
| eToro pide ir más despacio (HTTP 429) | **Se rinde en el acto**, con un aviso que dice cuánto pide esperar. La siguiente pasada del cron lo reintenta |
| eToro bloquea (captcha) | Aviso. **Nunca** interpreta "sin datos" como "lo ha vendido todo" |
| `state/state.json` dañado | Aviso **una vez**, sale en rojo y **no toca el fichero** |
| Telegram mal configurado | Falla con instrucciones, exit 2, sin tocar el estado |
| Un error en una persona | **No** afecta a las demás |
| **El workflow falla entero** (credencial del estado revocada, repositorio renombrado...) | **Te llega un aviso a Telegram** con el enlace al log |

### Red de seguridad: si el workflow falla, te enteras

Los pasos van en este orden:

```
1. Descargar el código
2. Preparar Python
3. Instalar dependencias
4. Preparar el estado      ← usa la credencial (deploy key o token)
5. Revisar carteras        ← aquí se envían los avisos
6. Guardar el estado
7. Avisar del fallo        ← if: failure()
```

Si el paso 4 falla (por ejemplo, la clave ya no sirve), el paso 5 **no llega a
ejecutarse**: no habría ningún aviso de Telegram y lo único visible sería una
❌ en la pestaña Actions, que es fácil no ver.

Por eso el paso 7 existe: si algo ha fallado, recibes un aviso en Telegram con
el enlace al log. Cubre cualquier fallo, no solo el de la credencial.

> **Ojo:** ese aviso **se repite en cada ejecución** mientras el problema siga
> sin arreglar (cada 30 minutos). Es incómodo a propósito: más vale que te
> enteres. En cuanto lo arregles, deja de llegar.

### Cuando eToro pide parar (el 429)

eToro limita las peticiones y, cuando se pasa, responde `429` con un
`retry-after` que **puede ser de más de 40 minutos**. Esperar sería un error: el
workflow tiene 15 minutos de tope, así que GitHub mataría el job a mitad de
espera.

Por eso, si la espera pedida es larga (más de 60 s), el monitor **abandona esa
pasada** con un aviso claro y deja que el siguiente cron lo reintente. Si la
espera es corta, sí reintenta. Con el cron cada 30 minutos, un 429 se resuelve
solo en la siguiente pasada.

Si te aparece a menudo, es señal de que vas demasiado rápido: sube
`http.delay_seconds` en `watchlist.yml` o espacia el cron.

### Los avisos no se repiten

Se guardan en `cooldown.json` (180 minutos por defecto). Los avisos de **una
persona** van con su propia clave, así que un problema con alguien no silencia
el de otra. En cambio, un bloqueo de eToro es un problema del sitio: se avisa
**una sola vez**, no una por cada usuario de la lista. En cuanto el problema
desaparece, la marca se olvida y el siguiente fallo avisa de inmediato.

**Y lo más importante:** un error **nunca borra las posiciones guardadas**. Si
no se puede leer una cartera, se deja la foto anterior intacta.

---

## 5. Elegir a quién vigilar

`watchlist.md`, un usuario por línea. Detalles:

* Las líneas que empiezan por `#` se ignoran.
* Las mayúsculas dan igual (`UsuarioEjemplo` = `usuarioejemplo`), y cambiar
  solo eso no pierde la memoria.
* Por comodidad también admite viñetas (`- usuario_ejemplo`) y la URL completa.
* **Si quitas a alguien de la lista, sus datos se borran del estado en la
  siguiente pasada.** Así no se acumula información de gente a la que ya no
  sigues, y si lo vuelves a añadir empieza limpio (con su línea base) en vez de
  avisarte de todo lo que hizo mientras no lo mirabas.

```bash
python -m etoro_monitor users   # a quién sigues, y a quién va a dejar de seguir
```

---

## 6. Privacidad: qué datos hay y dónde

El repositorio **no contiene ningún dato personal** por defecto: ni tokens, ni
correos, ni rutas locales. Los tests y la documentación usan nombres ficticios.
Hay dos guardianes automáticos en `tests/test_privacidad.py` que fallan si:

* se cuela un token, un correo, un chat_id o una ruta de tu ordenador, o
* aparece en el código, el README o los tests **alguno de los usuarios que
  sigues** (la lista solo debe estar en `watchlist.md`).

### Puedes tener el código público sin publicar datos

Si el repositorio del código va a ser **público**, hay dos cosas que no deben
acabar en él: **a quién sigues** y **el estado de las carteras**. Se resuelven
así:

| Qué | Cómo |
|---|---|
| A quién sigues | Deja `watchlist.md` vacío y pon los usuarios en el secreto **`ETORO_USERS`** (separados por comas) |
| El estado de las carteras | Guárdalo en un **repositorio privado** aparte (ver [2.6](#26-el-estado-en-un-repositorio-privado-recomendado)) |

Con eso, el repositorio público solo contiene código y documentación.

> **Por qué el estado también importa:** no es solo "a quién sigues". Es un
> registro con fecha y hora de las operaciones de esas personas. eToro solo
> muestra la cartera actual; tu repositorio acumularía un **histórico** que no
> existe en ningún otro sitio, y quedaría **archivado para siempre en el
> historial de git**. Es información de terceros que no decidieron publicarla ahí.

### Qué se guarda (y qué no)

`state/state.json` guarda, por persona y activo, **solo estos campos**:

```json
"1002:Buy": { "direction": "Buy", "instrument_id": 1002,
              "units": 11.417114, "invested_pct": 11.41 }
```

**No se guarda**: identificadores de posición, unidades por posición, fechas de
apertura, precios de entrada, ganancias ni valor de la cartera. Antes el fichero
ocupaba **61,7 KB**; ahora **~10 KB**.

### Nada crece sin control

Todo lo que se guarda cumple una de estas dos reglas: **se sobrescribe** o **se
borra solo**. No hay nada que se acumule indefinidamente.

| Qué | Cómo se limita |
|---|---|
| Las posiciones de cada persona | Se **sobrescriben**: solo existe la foto actual. Lo que ya no está en la cartera desaparece del fichero |
| Los avisos de error (`cooldown.json`) | Se **borran solos** a los **30 días** |
| Datos volátiles de cada ejecución | Fichero aparte (`.runtime.json`) que **no se versiona** |
| El histórico de commits | Se **compacta automáticamente**: el repositorio se queda con **1 commit** |

#### El histórico de commits se compacta solo

El monitor **solo lee el estado actual**: nunca consulta commits anteriores. Así
que el histórico no sirve para nada y, además, sería un registro fechado de las
operaciones de otras personas.

Con `COMPACTAR_HISTORIAL: true` (el valor por defecto), cada vez que hay algo
que guardar se deja el repositorio de estado con **un único commit**. Da igual
cuántas veces se ejecute: nunca acumula.

Si algún día prefieres conservar algo de histórico, cambia esa variable a
`false` al principio de `.github/workflows/monitor.yml`. El precio es que
perderás la posibilidad de deshacer un cambio a mano (recuperar un estado
anterior con `git checkout HEAD~1 -- state.json`).

> **Nota honesta sobre el espacio:** compactar deja el historial limpio (1
> commit en lugar de miles), pero **no reduce el tamaño del repositorio al
> instante**. Los commits reemplazados quedan como objetos «inalcanzables»
> hasta que GitHub ejecuta su mantenimiento. El beneficio real es que el
> historial visible no crece y que, con el tiempo, ese espacio se recupera.

#### Y se clona solo el último commit

El workflow usa `--depth 1 --branch <rama>`, así que cada ejecución se descarga
únicamente el último estado (unos KB) en vez del repositorio completo.

`state/cooldown.json` guarda cuándo se avisó de cada problema (con nombres de
usuario en las claves), así que también contiene información personal. El
guardián de privacidad comprueba que empiece vacío.

**Con la sección 2.6 configurada**, esos dos ficheros viven en el repositorio
privado y **nunca** se guardan en el repositorio del código: este puede ser
público sin más. Si prefieres no configurarla, entonces el repositorio del
código **debe ser privado**.

---

## 7. Estructura del proyecto

```
etoro-monitor/
├── .github/workflows/
│   ├── monitor.yml              # el que dispara cron-job.org
│   ├── test-telegram.yml        # mensaje de prueba
│   ├── test-etoro.yml           # diagnóstico de endpoints
│   └── test-instrumentos.yml    # catálogo de activos
├── etoro_monitor/
│   ├── client.py                # endpoints públicos, reintentos, anti-bloqueo
│   ├── models.py                # Usuario, Instrumento, Activo, Operación
│   ├── diff.py                  # compara unidades -> operaciones
│   ├── monitor.py               # orquesta: leer -> comparar -> avisar
│   ├── render.py                # redacta los mensajes
│   ├── state.py                 # estado persistente
│   ├── cooldown.py              # no repetir avisos de error
│   ├── watchlist.py             # lee watchlist.md
│   ├── config.py                # une watchlist.md + watchlist.yml
│   ├── schedule.py              # ventana horaria
│   ├── telegram.py              # envío a Telegram
│   └── cli.py                   # línea de comandos
├── state/
│   ├── state.json               # plantilla: memoria del monitor
│   └── cooldown.json            # plantilla: marcas de avisos
│                                # (con la opción 2.6, los de verdad viven en
│                                #  el repositorio privado)
├── tests/                       # 162 tests, sin red
│   ├── test_privacidad.py       #   vigila que no se cuelen datos personales
│   └── test_workflow.py         #   valida el YAML y el shell de los workflows
├── watchlist.md                 # ⬅ A QUIÉN SIGUES
├── watchlist.yml                # ajustes técnicos
└── requirements.txt
```

### Los endpoints que usa

| Endpoint | Para qué |
|---|---|
| `/api/logininfo/v1.1/users/<usuario>` | nombre de usuario → `CID` |
| `/sapi/trade-data-real/live/public/portfolios?cid=` | activos, lado y peso |
| `/sapi/trade-data-real/live/public/positions?cid=&instrumentId=` | unidades de cada activo |
| `/sapi/instrumentsmetadata/V1.1/instruments/<id>` | nombre y ticker |

**Coste:** ~1 petición por activo (unos 30 por persona con cartera grande). Es
el precio de distinguir una operación de un movimiento del mercado.

---

## 8. Avisos, límites y letra pequeña

* **No hay KYC ni login en ningún sitio.**
* **Términos de uso.** Esto lee datos públicos con la misma API que usa la web
  de eToro. Es scraping: eToro podría cambiar los endpoints o bloquear. Úsalo de
  forma moderada y bajo tu responsabilidad.
* **Bloqueos.** eToro usa DataDome/Cloudflare. Si detectamos un captcha, no se
  toca el estado y se avisa.
* **Operaciones muy seguidas.** Dos operaciones entre dos ejecuciones se ven
  como una sola: se compara el resultado, no el histórico.
* **Una compra y una venta exactamente iguales** entre dos ejecuciones dejarían
  las unidades iguales y no se detectarían. Es un caso extremo y asumible.
* **Repos inactivos.** GitHub desactiva los workflows programados tras 60 días
  sin actividad; al usar cron-job.org esto no te afecta.

### Límites de GitHub Actions (minutos)

| | Minutos de Actions | Coste |
|---|---|---|
| Repositorio **público** | **Ilimitados** | **0 €** |
| Repositorio **privado**, plan Free | **2.000 min/mes** | 0 € hasta el límite |

Cada ejecución del monitor tarda **menos de un minuto**, y GitHub **redondea al
alza**, así que se factura como 1 minuto. Con dos personas vigiladas:

| Frecuencia del cron | Minutos/mes | ¿Cabe en 2.000? |
|---|---|---|
| Cada 30 min | 1.440 | Sí, con margen |
| Cada 60 min | 720 | Sí, holgado |
| Cada 15 min | 2.880 | **No** |

Y ojo: **los minutos se comparten entre todos tus repositorios privados**. Si
agotas los 2.000, en el plan Free GitHub **no te cobra: te bloquea las
ejecuciones** hasta el mes siguiente, y el monitor se queda callado sin avisar.

Con el **código en un repositorio público y el estado en uno privado** (sección
2.6) tienes lo mejor de los dos: minutos **ilimitados y gratis**, porque las
ejecuciones corren en el repositorio público, y los datos a salvo.

> Los repositorios privados no consumen minutos por existir: solo por ejecutar
> Actions. El repositorio de estado no ejecuta nada, así que es gratis.

---

## 9. Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| No llega nada a Telegram | No le has escrito nunca al bot, o el `chat_id` está mal |
| El workflow no arranca con cron-job.org | Token sin permiso *Actions: read/write*, o URL/rama incorrecta |
| "Telegram no está configurado" | Faltan los secretos |
| "No hay ningún usuario que seguir" | `watchlist.md` vacío y sin secreto `ETORO_USERS` |
| "eToro ha devuelto un captcha" | Has ido muy rápido. Espera o sube `delay_seconds` |
| "eToro ha limitado las peticiones (429)" | Vas demasiado rápido: espacia el cron o sube `delay_seconds` |
| "la cartera es privada" | Ese usuario ha cerrado su cartera pública |
| "No puedo leer mi fichero de estado" | `state.json` dañado: repara el JSON o lanza con `reset_state` |
| El estado no se recupera entre pasadas | Revisa `STATE_BRANCH`: debe coincidir con la rama real del repositorio de estado |
| Avisos repetidos de operaciones | El estado no se está guardando: revisa los secretos `STATE_REPO`/`STATE_REPO_TOKEN` o `permissions: contents: write` |
| Sale `monitor.yml` en la raíz del repo | Estructura mal subida: los workflows no se ejecutarán |
