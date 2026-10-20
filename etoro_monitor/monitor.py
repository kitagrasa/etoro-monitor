"""Orquestación: leer carteras, comparar operaciones y avisar."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from .client import (
    Blocked,
    EtoroClient,
    EtoroError,
    PrivatePortfolio,
    UserNotFound,
)
from .cooldown import CooldownStore, error_key
from .diff import diff_snapshots, netear, split_adjustment
from .historial import arbitrar
from .models import Adjustment, Asset, Change, EtoroUser, Instrument, Position, Snapshot
from .render import render_baseline, render_problem, render_user_changes
from .state import RuntimeState, State

log = logging.getLogger(__name__)


def _ahora_utc() -> str:
    """Instante actual en UTC con la forma que quiere el histórico."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _es_nombre_de_relleno(nombre: str) -> bool:
    """¿Es el texto de emergencia ("Instrumento 8804") y no un nombre real?"""
    return nombre.startswith("Instrumento ")


@dataclass
class UserReport:
    username: str
    user: Optional[EtoroUser]
    changes: list[Change] = field(default_factory=list)
    baseline: bool = False
    error: Optional[str] = None
    requests: int = 0
    # Ajuste proporcional detectado en esta pasada (no es una operación).
    adjustment: Optional[Adjustment] = None
    # Cierres por activo que dijo el histórico en esta pasada. `None` si no se
    # consultó (no hacía falta) o si no contestó.
    closes: Optional[dict[int, int]] = None


class Monitor:
    def __init__(
        self,
        client: EtoroClient,
        state: State,
        *,
        runtime: Optional[RuntimeState] = None,
        cooldown: Optional[CooldownStore] = None,
        notify: Optional[Callable[[str], None]] = None,
        notify_errors: bool = True,
        error_cooldown_minutes: int = 180,
        quiet_baseline: bool = False,
    ) -> None:
        self.client = client
        self.state = state
        self.runtime = runtime or RuntimeState(state.path.with_suffix(".runtime.json"))
        self.cooldown = cooldown
        self.notify = notify or (lambda _text: None)
        self.notify_errors = notify_errors
        self.error_cooldown_minutes = error_cooldown_minutes
        self.quiet_baseline = quiet_baseline
        # Si el histórico falla de forma rara (bloqueo), no se le insiste en la
        # misma pasada: un reintento por usuario solo empeoraría el bloqueo.
        self._historico_descartado = False

    # ------------------------------------------------------------------ #
    # Errores
    # ------------------------------------------------------------------ #
    def notify_error(self, message: str, *, key: str) -> bool:
        """Avisa de un problema, como mucho una vez por periodo (por usuario).

        El cooldown va por `key` para que un problema en una persona no silencie
        el de otra. Se olvida en cuanto el problema desaparece, así que el
        siguiente fallo vuelve a avisar de inmediato.
        """
        if not self.notify_errors:
            return False
        if self.cooldown is not None:
            if not self.cooldown.is_ready(key, minutes=self.error_cooldown_minutes):
                log.info(
                    "Aviso de error silenciado (ya se avisó hace poco): %s", key
                )
                return False
        self.notify(render_problem(message))
        if self.cooldown is not None:
            self.cooldown.mark(key)
        return True

    def clear_error(self, key: str) -> None:
        if self.cooldown is not None:
            self.cooldown.forget(key)

    # ------------------------------------------------------------------ #
    # Recorrido
    # ------------------------------------------------------------------ #
    def run(self, usernames: Sequence[str], *, prune: bool = True) -> list[UserReport]:
        reports: list[UserReport] = []
        self._historico_descartado = False
        for username in usernames:
            try:
                report = self._process_user(username)
            except Blocked:
                # Un bloqueo de eToro (captcha o límite de peticiones) afecta a
                # TODAS las personas, no a una. Se deja subir para que se avise
                # una sola vez, en lugar de repetir el mismo mensaje por cada
                # usuario de la lista.
                raise
            except (UserNotFound, PrivatePortfolio, EtoroError) as exc:
                log.error("%s: %s", username, exc)
                report = UserReport(username=username, user=None, error=str(exc))
                self.notify_error(
                    f"No he podido leer la cartera de {report.username}: {exc}",
                    key=error_key(username),
                )
            else:
                # Si iba bien, olvidamos el error anterior de este usuario.
                self.clear_error(error_key(username))
            reports.append(report)

        if prune:
            self._prune(usernames)

        return reports

    def _prune(self, usernames: Sequence[str]) -> None:
        """Olvida a quien ya no está en la lista de seguimiento."""
        sobrantes = self.state.prune_users(usernames)
        if not sobrantes:
            return
        log.info("Dejo de seguir a: %s (borrado del estado)", ", ".join(sobrantes))
        if self.cooldown is not None:
            for slug in sobrantes:
                self.cooldown.forget(error_key(slug))
                self.cooldown.forget(f"baseline:{slug}")

    # ------------------------------------------------------------------ #
    def _process_user(self, username: str) -> UserReport:
        started = self.client.request_count
        user = self._resolve(username)
        log.info("%s -> CID %s", user.username, user.real_cid)

        # Instante de la foto. Es la marca de agua del histórico: la próxima
        # pasada preguntará por los cierres posteriores a este momento, así que
        # la ventana del histórico y la del diff son la misma.
        foto = _ahora_utc()
        snapshot = self.fetch_snapshot(user)

        previous_state = self.state.user(user.slug)
        is_baseline = previous_state is None or not previous_state.baseline_sent
        # Estado antiguo (sin posiciones): no se puede comparar con precisión,
        # así que esta pasada solo sirve para volver a tomar la foto. Comparar
        # unidades a secas daría avisos equivocados (una venta parcial se
        # anunciaría como venta total).
        aprendiendo_posiciones = (
            previous_state is not None and previous_state.needs_positions_baseline
        )

        changes: list[Change] = []
        adjustment: Optional[Adjustment] = None
        closes: Optional[dict[int, int]] = None
        if (
            previous_state is not None
            and previous_state.assets
            and not aprendiendo_posiciones
        ):
            previous = Snapshot(user=previous_state.user, assets=dict(previous_state.assets))
            changes, adjustment = split_adjustment(
                diff_snapshots(previous, snapshot),
                total_positions=sum(len(a.positions) for a in previous.assets.values()),
                total_assets=len(previous.instrument_ids),
            )
            # Se avisa del NETO de cada activo: comprar 5 y vender 5 entre dos
            # pasadas no es nada que copiar; comprar 5 y vender 4 es una compra
            # parcial de 1. Se hace después del detector de ajustes (que mira
            # posición a posición, que es donde se ve si el recorte es uniforme).
            changes = netear(changes)
            # El histórico se pide en toda pasada en la que se pueda: es el
            # árbitro de las bajadas pequeñas (recortes de eToro que no son
            # ventas). Una sola petición cubre toda la cartera.
            closes = self._historico(user, previous_state.history_since)
            if closes is not None:
                antes_del_arbitro = len(changes)
                changes = arbitrar(changes, closes)
                if len(changes) != antes_del_arbitro:
                    log.info(
                        "%s: %d bajada(s) pequeña(s) descartadas: el histórico no "
                        "tiene ningún cierre que las respalde",
                        user.username,
                        antes_del_arbitro - len(changes),
                    )

        # El estado guarda SOLO la foto actual: se asigna, no se acumula. Lo
        # que ya no está en la cartera desaparece del fichero.
        new_state = self.state.upsert_user(user)
        new_state.assets = dict(snapshot.assets)
        new_state.needs_positions_baseline = False
        new_state.history_since = foto

        labels = {
            iid: self.state.instrument_label(iid) for iid in snapshot.instrument_ids
        }

        if is_baseline:
            if not self.quiet_baseline:
                self.notify(render_baseline(user, snapshot, instrument_labels=labels))
            new_state.baseline_sent = True
            log.info("%s: línea base establecida", user.username)
        elif aprendiendo_posiciones:
            log.info(
                "%s: estado antiguo sin posiciones; se toma la foto nueva sin "
                "avisar (la próxima pasada ya compara posición a posición)",
                user.username,
            )
        elif changes:
            self.notify(
                render_user_changes(user, changes, instrument_labels=labels)
            )
            log.info("%s: %s operación(es) notificadas", user.username, len(changes))
            if adjustment is not None:
                log.info(
                    "%s: además, ajuste proporcional del %.2f%% en %d posiciones "
                    "(no se avisa como operación)",
                    user.username,
                    adjustment.percentage,
                    adjustment.positions,
                )
        elif adjustment is not None:
            # Solo hubo ajuste: no hay compras ni ventas que avisar.
            log.info(
                "%s: ajuste proporcional del %.2f%% en %d posiciones (sin aviso)",
                user.username,
                adjustment.percentage,
                adjustment.positions,
            )
        else:
            log.info("%s: sin operaciones", user.username)

        return UserReport(
            username=user.username,
            user=user,
            changes=changes,
            baseline=is_baseline,
            requests=self.client.request_count - started,
            adjustment=adjustment,
            closes=closes,
        )

    # ------------------------------------------------------------------ #
    def _historico(
        self,
        user: EtoroUser,
        since: Optional[str],
    ) -> Optional[dict[int, int]]:
        """Cierres por activo desde `since`, o `None` si no se puede usar.

        Se pide **una vez por persona y pasada** (no por activo) y cubre toda la
        cartera. Si no hay marca de agua (estado recién migrado) o el histórico
        no contesta, se devuelve `None` y la pasada sigue como antes: el
        histórico nunca puede tumbar un aviso ni callar una compra.
        """
        if not since:
            log.info(
                "%s: sin marca de agua del histórico; se decide como antes",
                user.username,
            )
            return None

        if self._historico_descartado:
            return None

        try:
            cierres = self.client.get_history_closes(user.real_cid, since)
        except Blocked as exc:
            # Un bloqueo no se reintenta por usuario: sería peor. Se deja de
            # pedir el histórico en esta pasada (el resto de peticiones ya
            # avisarán si eToro está bloqueando de verdad).
            self._historico_descartado = True
            log.warning(
                "%s: histórico bloqueado (%s); no se vuelve a pedir en esta pasada",
                user.username,
                exc,
            )
            return None
        except EtoroError as exc:
            log.warning(
                "%s: histórico de cierres no disponible (%s); se decide como antes",
                user.username,
                exc,
            )
            return None

        log.info(
            "%s: histórico desde %s -> %s",
            user.username,
            since,
            ", ".join(f"{iid}x{n}" for iid, n in sorted(cierres.items()))
            or "sin cierres",
        )
        return cierres

    # ------------------------------------------------------------------ #
    def _resolve(self, username: str) -> EtoroUser:
        data = self.client.resolve_username(username)
        avatars = data.get("avatars") or []
        avatar = ""
        if isinstance(avatars, list) and avatars:
            avatar = avatars[0].get("url", "") if isinstance(avatars[0], dict) else ""
        return EtoroUser(
            username=(data.get("username") or username).strip(),
            gcid=int(data["gcid"]),
            real_cid=int(data["realCid"] if "realCid" in data else data["realCID"]),
            demo_cid=data.get("demoCid") or data.get("demoCID"),
            first_name=data.get("firstName") or "",
            last_name=data.get("lastName") or "",
            allow_display_full_name=bool(data.get("allowDisplayFullName")),
            avatar_url=avatar,
        )

    def fetch_snapshot(self, user: EtoroUser) -> Snapshot:
        """Lee la cartera y resume cada activo en (posiciones, lado, peso)."""
        cid = user.real_cid
        portfolio = self.client.get_portfolio(cid)
        rows = portfolio.get("AggregatedPositions") or []

        assets: dict[str, Asset] = {}
        for row in rows:
            try:
                instrument_id = int(row["InstrumentID"])
            except (KeyError, TypeError, ValueError):
                log.warning("Activo ilegible: %s", row)
                continue

            direction = str(row.get("Direction") or "Buy")
            invested_pct = float(row.get("Invested") or 0.0)
            key = f"{instrument_id}:{direction}"

            posiciones = self._positions_of(cid, instrument_id, direction)

            anteriores = assets.get(key)
            if anteriores is None:
                assets[key] = Asset(
                    instrument_id=instrument_id,
                    direction=direction,
                    positions=posiciones,
                    invested_pct=invested_pct,
                )
            else:
                # Caso raro: dos filas del mismo activo y lado. Se juntan.
                juntas = dict(anteriores.positions)
                for position_id, position in posiciones.items():
                    previa = juntas.get(position_id)
                    juntas[position_id] = (
                        position
                        if previa is None
                        else Position(previa.position_id, previa.units + position.units)
                    )
                assets[key] = Asset(
                    instrument_id=instrument_id,
                    direction=direction,
                    positions=juntas,
                    invested_pct=anteriores.invested_pct + invested_pct,
                )

            self._ensure_instrument(instrument_id)

        return Snapshot(user=user, assets=assets)

    def _positions_of(
        self, cid: int, instrument_id: int, direction: str
    ) -> dict[int, Position]:
        """Las posiciones abiertas de ese lado, con su identificador.

        eToro publica `Amount` en positivo también en los cortos, así que la
        distinción largo/corto se hace por `IsBuy`. Si un activo tuviera a la
        vez posiciones largas y cortas, cada lado se cuenta por separado.

        Se guarda una entrada por `PositionID` porque es lo que permite saber
        si una compra ha abierto una posición nueva o si ha ampliado una que ya
        existía. Si el mismo identificador apareciera dos veces (no debería),
        las unidades se suman en vez de pisarse.
        """
        payload = self.client.get_positions(cid, instrument_id)
        quiere_buy = direction.strip().lower() != "sell"
        posiciones: dict[int, Position] = {}
        for raw in payload.get("PublicPositions") or []:
            try:
                if bool(raw.get("IsBuy", True)) != quiere_buy:
                    continue
                position_id = int(raw["PositionID"])
                units = float(raw.get("Amount") or 0.0)
            except (KeyError, TypeError, ValueError):
                log.warning("Posición ilegible en %s: %s", instrument_id, raw)
                continue
            previa = posiciones.get(position_id)
            posiciones[position_id] = Position(
                position_id=position_id,
                units=units if previa is None else previa.units + units,
            )
        return posiciones

    def _ensure_instrument(self, instrument_id: int) -> None:
        """Descarga el nombre del activo si aún no lo tenemos.

        Si el catálogo contesta sin nombre, NO se cachea el hueco "Instrumento
        N": se reintenta en la siguiente pasada. Antes se guardaba ese texto
        como si fuera el nombre y el aviso se quedaba con "Instrumento 8804"
        para siempre, aunque el catálogo sí tuviera "Sandisk Corp/DE (SNDK)".
        """
        cacheado = self.state.instruments.get(instrument_id)
        if cacheado is not None and not _es_nombre_de_relleno(cacheado.name):
            return
        try:
            raw = self.client.get_instrument(instrument_id)
        except EtoroError as exc:
            log.warning("Sin metadatos para %s: %s", instrument_id, exc)
            return
        if not raw:
            return
        nombre = str(raw.get("InstrumentDisplayName") or "").strip()
        if not nombre:
            log.warning(
                "El catálogo no da nombre para %s: se reintentará en la próxima pasada",
                instrument_id,
            )
            return
        self.state.instruments[instrument_id] = Instrument(
            instrument_id=int(raw.get("InstrumentID", instrument_id)),
            name=nombre,
            symbol=str(raw.get("SymbolFull") or ""),
        )
