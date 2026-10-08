"""Guardián de privacidad: el repositorio no debe contener datos personales.

Estos tests no comprueban "a quién sigues" (esa lista es tu config privada:
vive en el `watchlist.md` del repositorio de estado, que se clona en
`.state-repo`, y este repositorio debe quedarse sin nombres). Comprueban que no
se cuelen por descuido **secretos**, **correos**, **identificadores de chat**
ni **rutas de tu ordenador** en ningún fichero del proyecto.

Sirven también para el futuro: si algún día pegas un token en un fichero y
lo commiteas, el test te avisa antes.

Puedes silenciar un bloque concreto (por ejemplo en la documentación, donde
se citan los propios patrones a buscar) encerrándolo entre los marcadores
`privacidad:ignorar:inicio` y `privacidad:ignorar:fin`, cada uno en su propia
línea. Los marcadores solo cuentan si están solos en la línea: así, citarlos
dentro de un párrafo no abre un bloque por accidente.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RAIZ = Path(__file__).resolve().parents[1]

# Ficheros y carpetas que no se revisan
#
# .state-repo es el clon del repositorio de estado (privado): contiene a
# propósito la lista de personas y sus carteras. Está en .gitignore, así que no
# puede acabar en este repositorio, y por eso no se escanea.
EXCLUIR_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules",
    ".state-repo",
}
EXCLUIR_SUFIJOS = {
    ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".zip", ".gz", ".whl", ".xlsx", ".db", ".sqlite",
}

# Los marcadores solo valen si ocupan la línea entera (admitiendo el
# envoltorio de comentario). Si no, citarlos en un texto abriría un bloque
# por accidente y ocultaría datos reales.
INICIO_MARCADOR = re.compile(
    r"^\s*(?:<!--\s*|#\s*)?privacidad:ignorar:inicio\s*(?:-->)?\s*$"
)
FIN_MARCADOR = re.compile(
    r"^\s*(?:<!--\s*|#\s*)?privacidad:ignorar:fin\s*(?:-->)?\s*$"
)


def ficheros_del_proyecto() -> list[Path]:
    ficheros = []
    for ruta in RAIZ.rglob("*"):
        if not ruta.is_file():
            continue
        if any(parte in EXCLUIR_DIRS for parte in ruta.parts):
            continue
        if ruta.suffix.lower() in EXCLUIR_SUFIJOS:
            continue
        ficheros.append(ruta)
    return ficheros


def leer_sin_bloques_ignorados(ruta: Path) -> str:
    """Devuelve el texto sin las zonas marcadas como 'ignorar'."""
    try:
        texto = ruta.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""

    lineas, dentro = [], False
    for linea in texto.splitlines():
        if INICIO_MARCADOR.match(linea):
            dentro = True
            continue
        if FIN_MARCADOR.match(linea):
            dentro = False
            continue
        if not dentro:
            lineas.append(linea)
    return "\n".join(lineas)


def marcadores_balanceados(ruta: Path) -> bool:
    """Un marcador sin cerrar ocultaría el resto del fichero. Hay que vigilarlo."""
    try:
        texto = ruta.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return True
    dentro = False
    for linea in texto.splitlines():
        if INICIO_MARCADOR.match(linea):
            if dentro:
                return False  # dos inicios seguidos
            dentro = True
        elif FIN_MARCADOR.match(linea):
            if not dentro:
                return False  # fin sin inicio
            dentro = False
    return not dentro


# ---------------------------------------------------------------------- #
# Patrones peligrosos
# ---------------------------------------------------------------------- #
TOKEN_TELEGRAM = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")

CORREO = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

# Dominios genéricos que no identifican a nadie
DOMINIOS_PERMITIDOS = {
    "github.com",
    "users.noreply.github.com",
    "etoro.com",
    "www.etoro.com",
    "api.telegram.org",
    "example.com",
    "localhost",
}

# Un chat de Telegram real (los de grupo empiezan por -100)
CHAT_ID = re.compile(r"chat_?id\s*[:=]\s*[\"']?-?100\d{7,}")

# Rutas que revelan el nombre de usuario de tu ordenador
RUTAS_LOCALES = re.compile(r"(/home/[a-z][a-z0-9._-]+/|/Users/[A-Za-z][A-Za-z0-9._-]+/|C:\\\\Users\\\\)")


def _buscar(patron: re.Pattern[str]) -> list[str]:
    encontrados = []
    for ruta in ficheros_del_proyecto():
        texto = leer_sin_bloques_ignorados(ruta)
        if not texto:
            continue
        for numero, linea in enumerate(texto.splitlines(), start=1):
            if patron.search(linea):
                encontrados.append(f"{ruta.relative_to(RAIZ)}:{numero}: {linea.strip()[:120]}")
    return encontrados


# ---------------------------------------------------------------------- #
def test_no_hay_tokens_de_telegram():
    assert _buscar(TOKEN_TELEGRAM) == []


def test_no_hay_correos_personales():
    encontrados = []
    for ruta in ficheros_del_proyecto():
        texto = leer_sin_bloques_ignorados(ruta)
        for numero, linea in enumerate(texto.splitlines(), start=1):
            for coincidencia in CORREO.finditer(linea):
                if coincidencia.group(1).lower() not in DOMINIOS_PERMITIDOS:
                    encontrados.append(
                        f"{ruta.relative_to(RAIZ)}:{numero}: {coincidencia.group(0)}"
                    )
    assert encontrados == []


def test_no_hay_chat_ids_de_telegram():
    assert _buscar(CHAT_ID) == []


def test_no_hay_rutas_de_mi_ordenador():
    assert _buscar(RUTAS_LOCALES) == []


# ---------------------------------------------------------------------- #
# El estado no debe contener carteras de nadie en el repositorio
# ---------------------------------------------------------------------- #
def test_el_estado_inicial_esta_vacio():
    """En un clon recién hecho, state/state.json no debe traer carteras."""
    import json

    ruta = RAIZ / "state" / "state.json"
    if not ruta.exists():
        return
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos.get("users") in ({}, None), (
        "state/state.json contiene datos de usuarios: revísalo antes de "
        "publicar el repositorio"
    )
    assert datos.get("instruments") in ({}, None)


def _usuarios_que_sigo() -> list[str]:
    """Los usuarios de la lista de seguimiento, esté donde esté.

    Se usa el mismo parser que en producción, para que las reglas sean las
    mismas: comentarios HTML, líneas con #, viñetas, etc.

    Se miran tres sitios: el `watchlist.md` de este repositorio (que debe estar
    vacío: es el cartel que explica dónde está la lista de verdad), la copia
    privada que el workflow clona en `.state-repo` si estás trabajando en
    local, y la variable ETORO_USERS (formato antiguo).
    """
    import os
    import re

    from etoro_monitor.watchlist import load_usernames

    usuarios: list[str] = []
    for ruta in (RAIZ / "watchlist.md", RAIZ / ".state-repo" / "watchlist.md"):
        if ruta.exists():
            usuarios += load_usernames(ruta)
    for parte in re.split(r"[,\s]+", os.environ.get("ETORO_USERS", "")):
        if parte.strip():
            usuarios.append(parte.strip())
    return usuarios


def test_los_usuarios_que_sigo_no_se_cuelan_en_el_codigo():
    """La lista de seguimiento no debe aparecer en ningún fichero del repo.

    Su sitio es el repositorio de estado (privado). Aquí solo vale el
    watchlist.md vacío, que es el cartel que explica dónde está la lista.

    Si escribes tu usuario real en el README, en un comentario del código o en
    un test, este guardián lo detecta: el repositorio debe poder ser público
    sin revelar a quién sigues. (Este test existe porque el propio README llegó
    a llevar un usuario real como ejemplo.)
    """
    usuarios = _usuarios_que_sigo()
    if not usuarios:
        return  # lista vacía: no hay nada que proteger
    permitidos = {RAIZ / "watchlist.md"}
    encontrados = []
    for ruta in ficheros_del_proyecto():
        if ruta in permitidos:
            continue
        texto = leer_sin_bloques_ignorados(ruta)
        for usuario in usuarios:
            if usuario.lower() in texto.lower():
                encontrados.append(f"{ruta.relative_to(RAIZ)}: contiene '{usuario}'")
    assert encontrados == [], (
        "Hay usuarios de tu lista de seguimiento en el repositorio: " + "; ".join(encontrados)
    )


def test_el_cooldown_inicial_esta_vacio():
    """cooldown.json se versiona y lleva nombres de usuario en sus claves.

    Si se publicara con datos, revelaría a quién sigues y qué problemas has
    tenido con cada persona.
    """
    import json

    ruta = RAIZ / "state" / "cooldown.json"
    if not ruta.exists():
        return
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos.get("sent") == {}, (
        "state/cooldown.json contiene avisos previos (con nombres de usuario): "
        "revísalo antes de publicar el repositorio"
    )


def test_el_gitignore_cubre_lo_volatil():
    contenido = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in contenido
    assert "runtime.json" in contenido or "state/*.runtime.json" in contenido
    # .state-repo es el clon del repositorio de estado (privado): lleva la
    # lista de personas y sus carteras. Si una ejecución local lo deja ahí,
    # no debe poder colarse en el repositorio público con un 'git add'.
    assert ".state-repo" in contenido


def _token_falso() -> str:
    """Se construye por partes a propósito: si estuviera literal en el código,
    este mismo test lo detectaría como fuga (y con razón)."""
    return "1234567890" + ":" + "A" * 35


def _ruta_falsa() -> str:
    """Construida por partes, igual que el token: si estuviera literal, este
    mismo guardián la detectaría (y haría bien)."""
    return "/home/" + "persona/ejemplo"


def _chat_id_falso() -> str:
    """También por partes, por el mismo motivo."""
    return 'chat_id = "-100' + "1234567890" + '"'


def test_los_patrones_detectan_de_verdad():
    """Sin esto, un patrón mal escrito daría 'todo limpio' falsamente."""
    assert TOKEN_TELEGRAM.search(_token_falso())
    assert RUTAS_LOCALES.search(_ruta_falsa())
    assert CHAT_ID.search(_chat_id_falso())
    assert not CHAT_ID.search('chat_id = "123456789"')  # id antiguo, no de grupo


def test_el_mecanismo_de_ignorar_funciona():
    """El marcador debe permitir citar patrones sin que salten los tests."""
    temporal = RAIZ / "tests" / "_temporal_ignorado.txt"
    temporal.write_text(
        "<!-- privacidad:ignorar:inicio -->\n"
        f"token falso {_token_falso()}\n"
        f"ruta falsa {_ruta_falsa()}\n"
        "<!-- privacidad:ignorar:fin -->\n"
        "texto visible\n",
        encoding="utf-8",
    )
    try:
        limpio = leer_sin_bloques_ignorados(temporal)
        assert "texto visible" in limpio
        assert "1234567890" not in limpio
        assert "/home/" not in limpio
        assert marcadores_balanceados(temporal) is True
    finally:
        temporal.unlink()


def test_citar_los_marcadores_en_un_parrafo_no_abre_bloque():
    """Regresión: la propia documentación de los marcadores no debe ocultar
    lo que venga después."""
    temporal = RAIZ / "tests" / "_temporal_cita.txt"
    temporal.write_text(
        "Explica el formato privacidad:ignorar:inicio y privacidad:ignorar:fin\n"
        f"y luego una ruta de verdad: {_ruta_falsa()}\n",
        encoding="utf-8",
    )
    try:
        limpio = leer_sin_bloques_ignorados(temporal)
        assert RUTAS_LOCALES.search(limpio), "la cita del marcador ocultó datos"
    finally:
        temporal.unlink()


def test_los_marcadores_de_todos_los_ficheros_estan_balanceados():
    desbalanceados = [
        str(ruta.relative_to(RAIZ))
        for ruta in ficheros_del_proyecto()
        if not marcadores_balanceados(ruta)
    ]
    assert desbalanceados == []
