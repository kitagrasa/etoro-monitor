"""Tests del fichero watchlist.md (la lista de personas a seguir).

Los nombres usados aquí son ficticios a propósito: los tests no deben
depender de a quién sigue realmente el usuario.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.watchlist import describe, load_usernames, parse_usernames  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]


def test_lo_minimo_una_por_linea():
    assert parse_usernames("uno_ejemplo\ndos_ejemplo\n") == [
        "uno_ejemplo",
        "dos_ejemplo",
    ]


def test_el_fichero_del_repositorio_se_lee_bien():
    """El watchlist.md real debe dar usuarios válidos (sin fijar cuáles).

    Así el test sigue sirviendo si cambias de personas (o si lo dejas vacío
    y pones la lista en el secreto ETORO_USERS), y no incrusta en el código
    a quién sigues.
    """
    usuarios = load_usernames(RAIZ / "watchlist.md")
    assert isinstance(usuarios, list)
    for usuario in usuarios:
        assert " " not in usuario
        assert "//" not in usuario
        assert not usuario.startswith("#")
        assert usuario.strip() == usuario


def test_el_watchlist_del_repositorio_no_cuela_ejemplos():
    """El fichero tal cual viene no debe activar ningún usuario de ejemplo."""
    assert load_usernames(RAIZ / "watchlist.md") == []


def test_ignora_comentarios_titulos_y_lineas_vacias():
    texto = """
# Usuarios que sigo

<!-- un comentario HTML -->


uno_ejemplo
# dos_ejemplo (desactivado de momento)
"""
    assert parse_usernames(texto) == ["uno_ejemplo"]


def test_ignora_comentarios_html_multilinea_con_ejemplos():
    """Regresión: dentro de un comentario HTML se cita una URL de ejemplo y
    el parser la tomaba por un usuario de verdad."""
    texto = """
<!--
  Escribe el usuario que va en la URL:

      https://www.etoro.com/people/usuario_ejemplo/portfolio
                               ^^^^^^^^^^^^^^^

  Nada más.
-->

real_ejemplo
"""
    assert parse_usernames(texto) == ["real_ejemplo"]


def test_ignora_negritas_y_enlaces_dentro_de_comentario():
    texto = "<!-- ver [usuario_ejemplo](https://www.etoro.com/people/x) -->\nfinal\n"
    assert parse_usernames(texto) == ["final"]


def test_admite_vinetas_numeracion_y_comentario_final():
    texto = """
- uno_ejemplo
* dos_ejemplo
+ tres_ejemplo  # el que sea
1. cuatro_ejemplo
2) cinco_ejemplo
"""
    assert parse_usernames(texto) == [
        "uno_ejemplo",
        "dos_ejemplo",
        "tres_ejemplo",
        "cuatro_ejemplo",
        "cinco_ejemplo",
    ]


def test_admite_urls_y_enlaces_markdown():
    texto = """
https://www.etoro.com/people/uno_ejemplo
https://www.etoro.com/people/dos_ejemplo/portfolio
[tres_ejemplo](https://www.etoro.com/people/tres_ejemplo)
"""
    assert parse_usernames(texto) == [
        "uno_ejemplo",
        "dos_ejemplo",
        "tres_ejemplo",
    ]


def test_admite_tildes_invertidas_negritas_y_barras():
    texto = """
`uno_ejemplo`
**dos_ejemplo**
tres_ejemplo/
"""
    assert parse_usernames(texto) == ["uno_ejemplo", "dos_ejemplo", "tres_ejemplo"]


def test_no_duplica_y_respeta_el_orden():
    assert parse_usernames("dos_ejemplo\nDOS_EJEMPLO\nuno_ejemplo\n") == [
        "dos_ejemplo",
        "uno_ejemplo",
    ]


def test_ignora_reglas_horizontales_y_tablas():
    texto = """
---
***
___
===
| Usuario | Peso |
|---------|------|
| uno_ejemplo | 10% |
"""
    # No adivinamos qué celda de una tabla es el usuario: se ignora entera.
    # Lo importante es que "---" o "Usuario" no acaben como usuarios falsos.
    assert parse_usernames(texto) == []


def test_ignora_lineas_que_no_son_usuarios_limpios():
    texto = """
Esta es una frase normal con espacios
@alguien
usuario/muy/largo/con/barras
10%

bien123
"""
    assert parse_usernames(texto) == ["bien123"]


def test_frase_sin_usuario_no_produce_nada():
    assert parse_usernames("Esto es solo una nota suelta.\n") == []


def test_filtra_caracteres_invalidos():
    assert parse_usernames("usuario con espacios\nbien123\n") == ["bien123"]


def test_describe():
    assert describe(["a"]) == "a"
    assert describe(["a", "b"]) == "a y b"
    assert describe(["a", "b", "c"]) == "a, b y c"
    assert describe([]) == "(ninguno)"
