"""Lectura de la lista de usuarios desde un fichero Markdown.

El objetivo es que puedas editar la lista a mano, cómodamente, sin saber
YAML ni JSON:

    # Usuarios que sigo

    usuario_ejemplo
    otro_usuario
    # desactivado

Reglas del parser (deliberadamente permisivas):

* Una línea vacía se ignora.
* Una línea que empieza por `#` es un comentario (igual que un título).
* Se admite viñeta al principio: `- usuario`, `* usuario`, `+ usuario`.
* Se admite numeración: `1. usuario`, `2) usuario`.
* Se admite la URL completa: `https://www.etoro.com/people/usuario_ejemplo`
  o `https://www.etoro.com/people/usuario_ejemplo/portfolio`.
* Se admite un enlace Markdown: `[usuario_ejemplo](https://...)`.
* Se admiten tildes invertidas: `` `usuario_ejemplo`  ``.
* Se admite un comentario al final de la línea: `usuario_ejemplo  # nota`.
* Se ignoran las tablas Markdown, las reglas horizontales (`---`) y cualquier
  línea que no sea un nombre de usuario limpio.
* Se ignoran los comentarios HTML (`<!-- ... -->`), incluso multilínea: así
  puedes dejar instrucciones de ejemplo dentro de un comentario sin que el
  ejemplo se tome por un usuario de verdad.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# Un nombre de usuario de eToro: letras, números, punto, guion y guion bajo.
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# URL de eToro: https://www.etoro.com/people/<usuario>[/portfolio|/...]
PEOPLE_URL_RE = re.compile(
    r"https?://(?:www\.)?etoro\.com/people/([A-Za-z0-9._-]+)", re.IGNORECASE
)

# Enlace Markdown: [texto](url)
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")

# Comentario HTML, posiblemente multilínea
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Viñeta o numeración al principio de la línea (opcionalmente con casilla).
BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|\[[ xX]\]\s+)+")

# Separador de tabla Markdown: |---|---|
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")

# Regla horizontal Markdown: ---, ***, ___, === (tres o más)
HORIZONTAL_RULE_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")

# Fila de tabla Markdown: contiene una barra vertical
TABLE_ROW_RE = re.compile(r"^\s*\|")


def parse_usernames(text: str) -> list[str]:
    """Extrae los usuarios de un texto Markdown, en orden y sin duplicados."""
    found: list[str] = []
    seen: set[str] = set()

    # Los comentarios HTML (<!-- ... -->) pueden ocupar varias líneas y a
    # menudo contienen instrucciones de ejemplo con URLs: los quitamos
    # primero para que "un ejemplo" no acabe tratado como un usuario real.
    text = HTML_COMMENT_RE.sub("", text)

    for raw_line in text.splitlines():
        username = _parse_line(raw_line)
        if not username:
            continue
        key = username.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(username)

    return found


def _parse_line(raw_line: str) -> str | None:
    line = raw_line.rstrip()

    # Comentario o título
    if line.lstrip().startswith("#"):
        return None

    # Separador de tabla o regla horizontal
    if TABLE_SEPARATOR_RE.match(line) or HORIZONTAL_RULE_RE.match(line):
        return None

    # Fila de tabla: no adivinamos qué celda es el usuario, la ignoramos
    if TABLE_ROW_RE.match(line) or line.rstrip().endswith("|"):
        return None

    # Quitar viñetas / numeración
    line = BULLET_RE.sub("", line).strip()

    if not line:
        return None

    # Comentario al final de la línea: "usuario  # nota"
    line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
    if not line:
        return None

    # Un usuario de eToro nunca lleva espacios: si los tiene tras quitar la
    # viñeta, es texto (viñetas anidadas tipo "- - texto", frases...).
    if " " in line or "\t" in line:
        return None

    # Enlace Markdown -> nos quedamos con la URL (o con el texto si no hay URL)
    link = MD_LINK_RE.search(line)
    if link:
        target = (link.group(2) or "").strip() or (link.group(1) or "").strip()
        line = target

    # URL completa de eToro
    url = PEOPLE_URL_RE.search(line)
    if url:
        return url.group(1)

    # Quitar tildes invertidas, negritas, comillas y separadores finales
    line = line.strip().strip("`'\"*")
    line = line.rstrip("/|,;").strip()

    # Si después de todo queda algo con espacios, no es un usuario:
    # podría ser una celda de tabla con texto o una frase.
    if not line or " " in line or "\t" in line:
        return None

    if not USERNAME_RE.match(line):
        return None

    return line


def load_usernames(path: str | Path) -> list[str]:
    """Lee los usuarios de un fichero Markdown."""
    return parse_usernames(Path(path).read_text(encoding="utf-8"))


def describe(usernames: Iterable[str]) -> str:
    """'a', 'b', 'c' -> 'a, b y c' (para los mensajes)."""
    users = list(usernames)
    if not users:
        return "(ninguno)"
    if len(users) == 1:
        return users[0]
    return ", ".join(users[:-1]) + " y " + users[-1]
