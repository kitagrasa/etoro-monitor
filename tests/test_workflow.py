"""Comprueba que los workflows de GitHub Actions están bien formados.

Un error de sintaxis en un workflow no se detecta hasta que GitHub intenta
ejecutarlo, y entonces el fallo es confuso (o, peor, el workflow simplemente
no se ejecuta y nadie se entera). Estos tests lo pillan antes de subir nada:

* el YAML se puede leer,
* cada bloque `run:` es shell válido,
* el aviso de fallo existe y está al final (es la red que evita que el
  monitor se quede mudo si falla una credencial),
* las claves SSH de GitHub están completas y bien formadas.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

yaml = pytest.importorskip("yaml")

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((RAIZ / ".github" / "workflows").glob("*.yml"))


def cargar(ruta: Path) -> dict:
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))


def disparadores(doc: dict) -> dict:
    """En YAML, la clave `on:` se lee como el booleano True."""
    for clave in doc:
        if clave is True or clave == "on":
            return doc[clave] or {}
    return {}


def pasos(doc: dict) -> list[dict]:
    return list(doc["jobs"].values())[0]["steps"]


# ---------------------------------------------------------------------- #
# Formato general
# ---------------------------------------------------------------------- #
def test_hay_workflows():
    assert WORKFLOWS, "no encuentro ningún workflow en .github/workflows"


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_el_yaml_se_puede_leer(ruta):
    doc = cargar(ruta)
    assert doc.get("name"), f"{ruta.name} no tiene 'name'"
    assert "jobs" in doc, f"{ruta.name} no tiene 'jobs'"


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_cada_paso_es_shell_valido(ruta):
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("bash no disponible")
    for indice, paso in enumerate(pasos(cargar(ruta))):
        if "run" not in paso:
            continue
        archivo = Path("/tmp/_paso_del_workflow.sh")
        archivo.write_text(paso["run"], encoding="utf-8")
        resultado = subprocess.run(
            ["bash", "-n", str(archivo)], capture_output=True, text=True
        )
        assert resultado.returncode == 0, (
            f"{ruta.name}, paso «{paso.get('name', indice)}»: "
            f"el shell no es válido:\n{resultado.stderr}"
        )


@pytest.mark.parametrize("ruta", WORKFLOWS, ids=lambda p: p.name)
def test_los_pasos_tienen_nombre(ruta):
    for paso in pasos(cargar(ruta)):
        assert paso.get("name"), f"hay un paso sin 'name' en {ruta.name}"
        assert "uses" in paso or "run" in paso, (
            f"el paso «{paso.get('name')}» de {ruta.name} no hace nada"
        )


# ---------------------------------------------------------------------- #
# El workflow del monitor
# ---------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def monitor() -> dict:
    doc = cargar(RAIZ / ".github" / "workflows" / "monitor.yml")
    return doc


def test_el_monitor_no_se_dispara_solo(monitor):
    """No debe tener 'schedule': lo lanza cron-job.org desde fuera."""
    assert "schedule" not in disparadores(monitor), (
        "el workflow tiene un cron propio; se decidió que lo dispare "
        "cron-job.org para no depender de los minutos de GitHub"
    )
    assert "workflow_dispatch" in disparadores(monitor)


def test_avisa_si_algo_falla(monitor):
    """La red de seguridad.

    Sin un paso que avise cuando algo falla, un problema de credenciales
    dejaría el monitor mudo: el paso de revisar carteras no llegaría a
    ejecutarse y lo único visible sería una ❌ en la pestaña Actions.
    """
    avisos = [
        paso
        for paso in pasos(monitor)
        if str(paso.get("if", "")).strip() == "failure()"
    ]
    assert avisos, "no hay ningún paso con 'if: failure()' que avise del fallo"
    # Y debe ir al final, para que el estado se intente guardar antes.
    assert pasos(monitor)[-1] in avisos, (
        "el aviso de fallo debe ser el último paso"
    )


def test_el_aviso_de_fallo_manda_a_telegram(monitor):
    aviso = [
        p for p in pasos(monitor) if str(p.get("if", "")).strip() == "failure()"
    ][0]
    assert "api.telegram.org" in aviso["run"]
    assert "TELEGRAM_BOT_TOKEN" in aviso["env"]
    assert "TELEGRAM_CHAT_ID" in aviso["env"]
    # Si no puede avisar, no debe tumbar el job con un error distinto.
    assert "|| echo" in aviso["run"]


def test_el_estado_se_guarda_pase_lo_que_pase(monitor):
    guardar = [p for p in pasos(monitor) if p.get("if") == "always()"]
    assert guardar, "el paso de guardar el estado debe tener 'if: always()'"


def test_el_clon_es_superficial_y_de_la_rama_correcta(monitor):
    """`--depth 1` a secas perdería el estado, y en silencio.

    Sin `--branch`, git solo trae la rama por defecto del remoto. Si el
    repositorio de estado se creó con 'master' (o su HEAD apunta ahí) y el
    estado vive en 'main', el clon sale VACÍO. El monitor creería entonces
    que es la primera vez en cada ejecución y repetiría avisos sin parar.

    Con `--depth 1 --branch <rama>` el clon trae justo la rama del estado.
    """
    preparar = [p for p in pasos(monitor) if "STATE_DEPLOY_KEY" in (p.get("env") or {})][0]
    run = preparar["run"]

    assert "--depth 1" in run, "el clon no es superficial: baja todo el historial"
    assert '--branch "$STATE_BRANCH"' in run, (
        "el clon superficial no indica la rama: si el HEAD del remoto no es la "
        "del estado, el clon quedaría vacío y se perdería el estado"
    )
    # Y debe haber salida para el repositorio recién creado (sin esa rama).
    assert "init --quiet" in run, "no hay salida para un repositorio vacío"


def test_el_historial_se_compacta(monitor):
    """El repositorio de estado no debe acumular histórico.

    El monitor solo lee el estado actual: los commits anteriores no sirven
    para nada y son un registro fechado de las operaciones de terceros.
    """
    guardar = [p for p in pasos(monitor) if p.get("name") == "Guardar el estado"][0]
    run = guardar["run"]

    assert "--orphan" in run, "no se está compactando el historial"
    assert "--force" in run, "la compactación necesita reemplazar la rama remota"
    # La compactación debe incluir TODOS los ficheros de estado, no solo uno:
    # si se dejara fuera cooldown.json se perderían las marcas de avisos.
    assert "git add -A" in run


def test_la_compactacion_se_puede_desactivar(monitor):
    """Debe existir una salida de emergencia.

    Compactar deja el repositorio sin posibilidad de deshacer un cambio. Si
    alguien prefiere conservar algo de historial, tiene que poder hacerlo sin
    editar el cuerpo del workflow.
    """
    assert "COMPACTAR_HISTORIAL" in monitor["jobs"]["monitor"].get("env", {}), (
        "falta la variable que permite desactivar la compactación"
    )
    guardar = [p for p in pasos(monitor) if p.get("name") == "Guardar el estado"][0]
    assert 'if [ "$COMPACTAR_HISTORIAL" = "true" ]' in guardar["run"]


def test_soporta_repositorio_de_estado_privado(monitor):
    """El estado puede vivir fuera, para poder tener el código público."""
    preparar = [
        p for p in pasos(monitor) if "STATE_REPO" in (p.get("env") or {})
    ]
    assert preparar, "no hay ningún paso que use STATE_REPO"
    texto = "\n".join(p.get("run", "") for p in preparar)
    assert ".state-repo" in texto
    assert "ETORO_STATE" in "".join(
        str(p.get("env", {})) for p in pasos(monitor)
    )


def test_acepta_deploy_key_y_token(monitor):
    """Las dos formas de identificarse deben estar soportadas."""
    preparar = [
        p for p in pasos(monitor) if "STATE_REPO" in (p.get("env") or {})
    ][0]
    assert "STATE_DEPLOY_KEY" in preparar["env"], "falta soporte de deploy key"
    assert "STATE_REPO_TOKEN" in preparar["env"], "falta soporte de token"
    # La deploy key tiene prioridad (no caduca y no se puede sobre-otorgar).
    assert preparar["run"].index("deploy_key") < preparar["run"].index("token")


def test_el_clon_de_la_deploy_key_verifica_el_host(monitor):
    """Con StrictHostKeyChecking=yes, el known_hosts debe ser el correcto."""
    preparar = [
        p for p in pasos(monitor) if "STATE_DEPLOY_KEY" in (p.get("env") or {})
    ][0]
    texto = preparar["run"]
    assert "StrictHostKeyChecking=yes" in texto
    assert "IdentitiesOnly=yes" in texto
    # Claves de GitHub fijadas a mano, en vez de ssh-keyscan (que es TOFU).
    # Se miran solo las líneas de código: el comentario que lo explica sí
    # puede nombrarlo.
    codigo = [
        linea for linea in texto.splitlines() if not linea.strip().startswith("#")
    ]
    assert not any("ssh-keyscan" in linea for linea in codigo), (
        "se está usando ssh-keyscan, que acepta la clave del servidor sin "
        "comprobarla la primera vez"
    )
    assert "github.com ssh-ed25519 " in texto
    # Permisos: ssh se niega a usar una clave legible por otros.
    assert "chmod 600 ~/.ssh/state_key" in texto


def test_el_clon_le_dice_a_git_que_use_la_clave(monitor):
    """Regresión de un fallo que se escapó una vez.

    `git` NO usa la clave de la deploy key por su cuenta: busca las de
    siempre (~/.ssh/id_ed25519, id_rsa...). Si no se le indica, el clon falla
    con 'Permission denied (publickey)' aunque la deploy key esté bien puesta
    en GitHub. Hay que pasarle la clave explícitamente.
    """
    preparar = [
        p for p in pasos(monitor) if "STATE_DEPLOY_KEY" in (p.get("env") or {})
    ][0]
    texto = preparar["run"]

    # La clave debe pasarse a git para el clon inicial...
    assert "GIT_SSH_COMMAND" in texto, (
        "el clon no recibe la clave: fallará con Permission denied"
    )
    # ...y quedar configurada en el repositorio clonado para el push.
    assert "core.sshCommand" in texto, (
        "el push posterior no sabe qué clave usar"
    )


def test_la_identidad_del_commit_se_configura_dentro_del_repo_de_estado(monitor):
    """Regresión: el commit se hace en OTRO repositorio.

    Si `git config user.email` se ejecuta fuera de .state-repo, el commit
    falla con "Author identity unknown", porque cada repositorio tiene su
    propia configuración.
    """
    guardar = [p for p in pasos(monitor) if p.get("name") == "Guardar el estado"][0]
    run = guardar["run"]

    # La línea del cd...
    indice_cd = run.index("cd .state-repo")
    # ...debe ir ANTES de configurar la identidad del modo remoto.
    assert run.index("git config user.name") > indice_cd, (
        "la identidad se configura antes de entrar en .state-repo: el commit "
        "fallaría con 'Author identity unknown'"
    )
    # Y el modo local también tiene que configurarla.
    assert run.count("git config user.name") >= 2, (
        "falta configurar la identidad en el modo local"
    )


def test_las_claves_de_github_estan_bien_formadas(monitor):
    """Un solo carácter mal y fallaría la verificación del host.

    El contenido se compara con lo que publica GitHub en
    https://api.github.com/meta (sin necesidad de red aquí: se comprueba que
    son dos claves completas y de los tipos esperados).
    """
    preparar = [
        p for p in pasos(monitor) if "STATE_DEPLOY_KEY" in (p.get("env") or {})
    ][0]
    claves = [
        linea.strip()
        for linea in preparar["run"].splitlines()
        if linea.strip().startswith("github.com ")
    ]
    assert len(claves) == 2, f"esperaba 2 claves de GitHub, hay {len(claves)}"
    tipos = {clave.split()[1] for clave in claves}
    assert tipos == {"ssh-ed25519", "ecdsa-sha2-nistp256"}
    for clave in claves:
        material = clave.split()[2]
        assert len(material) > 60, f"clave sospechosamente corta: {clave[:40]}"
        assert " " not in material


# ---------------------------------------------------------------------- #
# Los demás workflows de diagnóstico
# ---------------------------------------------------------------------- #
def test_el_workflow_de_telegram_tiene_su_comando():
    doc = cargar(RAIZ / ".github" / "workflows" / "test-telegram.yml")
    texto = "\n".join(p.get("run", "") for p in pasos(doc))
    assert "notify-test" in texto


def test_los_workflows_de_diagnostico_usan_ping_y_resolve():
    doc = cargar(RAIZ / ".github" / "workflows" / "test-etoro.yml")
    texto = "\n".join(p.get("run", "") for p in pasos(doc))
    assert "ping" in texto and "resolve" in texto
