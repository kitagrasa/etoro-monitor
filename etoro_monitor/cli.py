"""Interfaz de línea de comandos.

    python -m etoro_monitor check                 # revisa y avisa (uso normal)
    python -m etoro_monitor check --dry-run       # revisa sin enviar nada
    python -m etoro_monitor users                 # a quién está siguiendo
    python -m etoro_monitor show usuario_ejemplo  # imprime la cartera actual
    python -m etoro_monitor resolve usuario_ejemplo
    python -m etoro_monitor ping                  # ¿responde eToro?
    python -m etoro_monitor notify-test           # prueba de Telegram
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from .client import Blocked, EtoroClient, EtoroError, PrivatePortfolio, UserNotFound
from .config import Config, load_config
from .cooldown import BLOCKED_KEY, STATE_ERROR_KEY, CooldownStore
from .monitor import Monitor
from .render import now_utc, render_portfolio_table
from .state import RuntimeState, State, StateCorruptError, cooldown_path
from .telegram import TelegramError, TelegramNotifier

DEFAULT_STATE = "state/state.json"

log = logging.getLogger("etoro_monitor")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def build_client(config: Config) -> EtoroClient:
    kwargs = {}
    if config.http.user_agent:
        kwargs["user_agent"] = config.http.user_agent
    return EtoroClient(
        delay=config.http.delay_seconds,
        timeout=config.http.timeout_seconds,
        max_retries=config.http.max_retries,
        **kwargs,
    )


def _notifier() -> TelegramNotifier:
    return TelegramNotifier(
        os.environ.get("TELEGRAM_BOT_TOKEN"),
        os.environ.get("TELEGRAM_CHAT_ID"),
    )


def _make_sender(args: argparse.Namespace, notifier: TelegramNotifier):
    """Devuelve la función de envío (imprime en --dry-run)."""

    def send(text: str) -> None:
        if args.dry_run:
            print("-" * 72)
            print(text)
            print("-" * 72)
            return
        notifier.send(text)
        log.info("Mensaje enviado a Telegram")

    return send


# ---------------------------------------------------------------------- #
# check
# ---------------------------------------------------------------------- #
def cmd_check(args: argparse.Namespace) -> int:
    users_file = getattr(args, "users_file", None)
    config = load_config(args.config, users_file)
    users = args.users or config.users
    log.info("Siguiendo a %s (desde %s)", config.describe_users(), config.users_origin)

    # Ventana horaria (hora local de la zona configurada).
    if not args.force and not config.schedule.is_open():
        log.info(
            "Fuera de la ventana de ejecución (%s). No se hace nada.",
            config.schedule.describe(),
        )
        return 0

    notifier = _notifier()
    runtime = RuntimeState(Path(args.state).with_suffix(".runtime.json"))
    cooldown = CooldownStore.load(cooldown_path(args.state))

    # ------------------------------------------------------------------
    # Sin Telegram no hay avisos posibles. Fallar aquí, con un mensaje
    # claro, es mucho mejor que recorrer todas las carteras y terminar en
    # verde sin haber enviado nada (el fallo silencioso más frustrante).
    # ------------------------------------------------------------------
    if not args.dry_run and not notifier.configured:
        faltan = [
            nombre
            for nombre, valor in (
                ("TELEGRAM_BOT_TOKEN", notifier.token),
                ("TELEGRAM_CHAT_ID", notifier.chat_id),
            )
            if not valor
        ]
        log.error(
            "Telegram no está configurado: falta %s.\n"
            "  · En GitHub: Settings -> Secrets and variables -> Actions -> "
            "New repository secret.\n"
            "  · En tu ordenador: exporta las dos variables.\n"
            "  · Para probar sin enviar nada: añade --dry-run.",
            " y ".join(faltan),
        )
        return 2

    if args.dry_run:
        log.info("Modo --dry-run: los mensajes se imprimen, no se envían")

    if args.reset:
        log.warning("--reset: se descarta el estado anterior (%s)", args.state)

    # ------------------------------------------------------------------
    # Fichero de estado. Si está dañado no se toca (para poder repararlo) y
    # se avisa una sola vez por periodo, en vez de dejar el monitor mudo.
    # ------------------------------------------------------------------
    state: State
    try:
        state = State.load(args.state)
    except StateCorruptError as exc:
        log.error("%s", exc)
        log.error(
            "No toco el fichero para que puedas repararlo. Soluciones:\n"
            "  · Corrígelo a mano en %s.\n"
            "  · O lanza el monitor con --reset (o el workflow marcando "
            "'reset_state') para empezar de cero: recibirás la línea base otra "
            "vez.",
            exc.path,
        )
        _alert_state_error(
            args, config, cooldown, notifier, exc
        )
        cooldown.save()
        return 1

    # El estado se ha podido leer: si antes avisamos de que estaba roto, ya no.
    cooldown.forget(STATE_ERROR_KEY)

    if args.reset:
        state = State(path=state.path)

    client = build_client(config)
    monitor = Monitor(
        client,
        state,
        runtime=runtime,
        cooldown=cooldown,
        notify=_make_sender(args, notifier),
        notify_errors=config.alerts.notify_on_error and not args.dry_run,
        error_cooldown_minutes=config.alerts.error_cooldown_minutes,
        quiet_baseline=args.quiet_baseline or not config.alerts.notify_on_baseline,
    )

    exit_code = 0
    total = 0
    reports = []
    try:
        # Con `--user` solo se revisa a esa persona, así que NO se puede podar
        # el resto de la lista: borraría del estado a todos los demás.
        reports = monitor.run(users, prune=not args.users)
        for report in reports:
            if report.error:
                exit_code = max(exit_code, 1)
                continue
            total += len(report.changes)
            for change in report.changes:
                log.info(
                    "  %-9s %s%s",
                    change.kind.upper(),
                    change.instrument_id,
                    " (CORTO)" if change.is_short else "",
                )
    except Blocked as exc:
        # eToro bloquea por captcha o por ir demasiado rápido. Es un problema
        # del sitio, no de una persona: un solo aviso, con su propio cooldown.
        log.error("eToro ha bloqueado las peticiones: %s", exc)
        monitor.notify_error(str(exc), key=BLOCKED_KEY)
        exit_code = 1
    except EtoroError as exc:
        log.error("Error global: %s", exc)
        monitor.notify_error(str(exc), key=STATE_ERROR_KEY)
        exit_code = 1
    else:
        # La pasada ha ido bien: si antes avisamos de un bloqueo, ya no.
        cooldown.forget(BLOCKED_KEY)
    finally:
        client.close()

    changed = state.save()
    cooldown.prune()
    cooldown.save()
    runtime.set("last_run_at", _utc_now())
    runtime.save()

    log.info(
        "Resumen: %s usuario(s), %s operación(es), %s peticiones HTTP, estado %s",
        len(reports),
        total,
        client.request_count,
        "actualizado" if changed else "sin novedades",
    )
    print(f"STATE_CHANGED={str(changed).lower()}")
    return exit_code


def _alert_state_error(args, config, cooldown, notifier, exc: StateCorruptError) -> None:
    """Avisa de que el estado está dañado, respetando el cooldown."""
    if args.dry_run:
        print("-" * 72)
        print(f"No puedo leer mi fichero de estado: {exc}")
        print("-" * 72)
        return
    # Se construye un Monitor mínimo solo para reutilizar su lógica de avisos.
    cliente = build_client(config)
    try:
        avisador = Monitor(
            cliente,
            State(path=Path(args.state)),
            cooldown=cooldown,
            notify=_make_sender(args, notifier),
            notify_errors=config.alerts.notify_on_error,
            error_cooldown_minutes=config.alerts.error_cooldown_minutes,
        )
        avisador.notify_error(
            f"No puedo leer mi fichero de estado ({exc.path.name}), así que dejo "
            f"de revisar carteras hasta que lo arregles.\n\nDetalle: {exc.causa}",
            key=STATE_ERROR_KEY,
        )
    finally:
        cliente.close()


# ---------------------------------------------------------------------- #
# show
# ---------------------------------------------------------------------- #
def cmd_show(args: argparse.Namespace) -> int:
    config = load_config(
        args.config, getattr(args, "users_file", None), require_users=False
    )
    client = build_client(config)
    try:
        state = State.load(args.state)
    except StateCorruptError as exc:
        log.error("%s", exc)
        return 2
    monitor = Monitor(client, state, notify=lambda _t: None)
    try:
        user = monitor._resolve(args.username)
        snapshot = monitor.fetch_snapshot(user)
    except (UserNotFound, PrivatePortfolio, Blocked, EtoroError) as exc:
        log.error("%s", exc)
        return 1
    finally:
        client.close()

    labels = {iid: state.instrument_label(iid) for iid in snapshot.instrument_ids}
    print(render_portfolio_table(user, snapshot, instrument_labels=labels))
    return 0


# ---------------------------------------------------------------------- #
# resolve / ping
# ---------------------------------------------------------------------- #
def cmd_resolve(args: argparse.Namespace) -> int:
    config = load_config(
        args.config, getattr(args, "users_file", None), require_users=False
    )
    client = build_client(config)
    try:
        for username in args.username:
            try:
                data = client.resolve_username(username)
            except UserNotFound as exc:
                print(f"{username}: {exc}")
                continue
            cid = data.get("realCid", data.get("realCID"))
            full = f"{data.get('firstName','')} {data.get('lastName','')}".strip()
            print(
                f"{username} -> username={data.get('username')} "
                f"CID={cid} GCID={data.get('gcid')} "
                f"nombre={full or '(oculto)'} "
                f"cartera_privada={'no' if cid else '?'}"
            )
    except EtoroError as exc:
        log.error("%s", exc)
        return 1
    finally:
        client.close()
    return 0


def cmd_ping(args: argparse.Namespace) -> int:
    config = load_config(
        args.config, getattr(args, "users_file", None), require_users=False
    )
    client = build_client(config)
    username = args.username or (config.users[0] if config.users else None)
    if not username:
        print(
            "❌ Indica un usuario: python -m etoro_monitor ping --username <usuario>\n"
            "   (o rellena watchlist.md)"
        )
        return 1
    ok = True
    try:
        data = client.resolve_username(username)
        cid = data.get("realCid", data.get("realCID"))
        print(f"✅ /api/logininfo/v1.1/users/{username} -> CID {cid}")

        portfolio = client.get_portfolio(cid)
        activos = portfolio.get("AggregatedPositions") or []
        print(f"✅ /live/public/portfolios -> {len(activos)} activos")

        if activos:
            instrument_id = int(activos[0]["InstrumentID"])
            positions = client.get_positions(cid, instrument_id)
            n = len(positions.get("PublicPositions") or [])
            print(f"✅ /live/public/positions?instrumentId={instrument_id} -> {n} posiciones")
            meta = client.get_instrument(instrument_id)
            print(
                "✅ /instrumentsmetadata -> "
                f"{meta.get('InstrumentDisplayName') if meta else '?'}"
            )
    except (UserNotFound, PrivatePortfolio, Blocked, EtoroError) as exc:
        print(f"❌ {exc}")
        ok = False
    finally:
        client.close()
    print(f"\n{client.request_count} peticiones HTTP")
    return 0 if ok else 1


def cmd_notify_test(args: argparse.Namespace) -> int:
    """Manda un mensaje de prueba a Telegram (para validar los secretos)."""
    notifier = _notifier()
    if not notifier.configured:
        print(
            "❌ Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID.\n"
            "   En GitHub: Settings -> Secrets and variables -> Actions."
        )
        return 1

    try:
        config = load_config(args.config, getattr(args, "users_file", None))
        usuarios = config.describe_users()
    except (FileNotFoundError, ValueError):
        usuarios = "(watchlist.md no encontrado)"

    text = (
        "✅ <b>Prueba del monitor de eToro</b>\n"
        f"<i>{now_utc()}</i>\n\n"
        "Si estás leyendo esto, el bot y el chat están bien configurados.\n"
        f"Usuarios vigilados: {usuarios}"
    )
    notifier.send(text)
    print(f"✅ Mensaje de prueba enviado al chat {notifier.chat_id}")
    return 0


def cmd_users(args: argparse.Namespace) -> int:
    """Muestra a quién se sigue, según watchlist.md."""
    try:
        config = load_config(args.config, getattr(args, "users_file", None))
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ {exc}")
        return 1

    print(f"\nSiguiendo a {len(config.users)} usuario(s) desde {config.users_origin}:\n")
    for index, username in enumerate(config.users, start=1):
        print(
            f"  {index:>2}. {username:<24} "
            f"https://www.etoro.com/people/{username.lower()}/portfolio"
        )
    print()

    # De paso, avisamos de a quién se está olvidando ya en el estado.
    try:
        state = State.load(args.state)
    except StateCorruptError as exc:
        print(f"⚠️  {exc}\n")
        return 0
    en_estado = sorted(state.users)
    actuales = {u.lower() for u in config.users}
    sobrantes = [n for n in en_estado if n not in actuales]
    if sobrantes:
        print("En el estado pero ya no en la lista (se borrarán en el próximo check):")
        for nombre in sobrantes:
            print(f"  · {nombre}")
        print()
    return 0


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------- #
def _common_arguments() -> argparse.ArgumentParser:
    """Opciones que valen tanto antes como después del subcomando.

    Se declaran con SUPPRESS porque argparse copia el valor por defecto del
    subparser encima de lo que ya se haya indicado antes del subcomando. Con
    SUPPRESS, si no se indica la opción el atributo ni siquiera existe y
    respetamos lo puesto antes (los valores por defecto reales se aplican
    después, en `_apply_defaults`).
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="ajustes técnicos (por defecto watchlist.yml)",
    )
    common.add_argument(
        "--users-file",
        dest="users_file",
        default=argparse.SUPPRESS,
        help="fichero Markdown con un usuario por línea (por defecto watchlist.md)",
    )
    common.add_argument(
        "--state",
        default=argparse.SUPPRESS,
        help=f"fichero de estado (por defecto {DEFAULT_STATE})",
    )
    common.add_argument(
        "--log-level",
        dest="log_level",
        default=argparse.SUPPRESS,
        help="DEBUG, INFO, WARNING, ERROR (por defecto INFO)",
    )
    return common


def _apply_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Rellena las opciones no indicadas (con la variable de entorno o el valor por defecto)."""
    if getattr(args, "config", None) is None:
        args.config = os.environ.get("ETORO_CONFIG", "watchlist.yml")
    if getattr(args, "users_file", None) is None:
        args.users_file = os.environ.get("ETORO_WATCHLIST_MD")
    if getattr(args, "state", None) is None:
        args.state = os.environ.get("ETORO_STATE", DEFAULT_STATE)
    if getattr(args, "log_level", None) is None:
        args.log_level = os.environ.get("LOG_LEVEL", "INFO")
    return args


def build_parser() -> argparse.ArgumentParser:
    common = _common_arguments()
    parser = argparse.ArgumentParser(
        prog="etoro_monitor",
        description="Avisos por Telegram de movimientos en carteras públicas de eToro.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser(
        "check",
        help="revisa las carteras y avisa de los cambios",
        parents=[common],
    )
    check.add_argument("--dry-run", action="store_true", help="no envía a Telegram, imprime")
    check.add_argument("--reset", action="store_true", help="olvida el estado anterior")
    check.add_argument(
        "--user", dest="users", action="append", help="limita a este usuario (repetible)"
    )
    check.add_argument(
        "--quiet-baseline",
        action="store_true",
        help="no envía el mensaje de bienvenida la primera vez",
    )
    check.add_argument(
        "--force",
        action="store_true",
        help="ignora la ventana horaria y ejecuta ahora",
    )
    check.set_defaults(func=cmd_check)

    show = sub.add_parser(
        "show", parents=[common], help="imprime la cartera actual de un usuario"
    )
    show.add_argument("username")
    show.set_defaults(func=cmd_show)

    resolve = sub.add_parser(
        "resolve", parents=[common], help="nombre de usuario -> CID"
    )
    resolve.add_argument("username", nargs="+")
    resolve.set_defaults(func=cmd_resolve)

    ping = sub.add_parser(
        "ping", parents=[common], help="comprueba los endpoints públicos de eToro"
    )
    ping.add_argument("--username", default=None)
    ping.set_defaults(func=cmd_ping)

    notify = sub.add_parser(
        "notify-test", parents=[common], help="envía un mensaje de prueba a Telegram"
    )
    notify.set_defaults(func=cmd_notify_test)

    users = sub.add_parser(
        "users",
        parents=[common],
        help="muestra a quién se está siguiendo (según watchlist.md)",
    )
    users.set_defaults(func=cmd_users)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments)
    if not getattr(args, "func", None):
        # Sin subcomando hacemos `check`, que es lo cómodo para el cron.
        args = parser.parse_args(arguments + ["check"])
    _apply_defaults(args)
    _setup_logging(args.log_level)
    try:
        return int(args.func(args) or 0)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 2
    except ValueError as exc:
        log.error("Configuración inválida: %s", exc)
        return 2
    except TelegramError as exc:
        log.error("Telegram: %s", exc)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
