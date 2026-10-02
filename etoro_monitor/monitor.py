"""Orquestación: leer carteras, comparar operaciones y avisar."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .client import (
    Blocked,
    EtoroClient,
    EtoroError,
    PrivatePortfolio,
    UserNotFound,
)
from .cooldown import CooldownStore, error_key
from .diff import diff_snapshots
from .models import Asset, Change, EtoroUser, Instrument, Snapshot
from .render import render_baseline, render_problem, render_user_changes
from .state import RuntimeState, State

log = logging.getLogger(__name__)


@dataclass
class UserReport:
    username: str
    user: Optional[EtoroUser]
    changes: list[Change] = field(default_factory=list)
    baseline: bool = False
    error: Optional[str] = None
    requests: int = 0


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

        snapshot = self.fetch_snapshot(user)

        previous_state = self.state.user(user.slug)
        is_baseline = previous_state is None or not previous_state.baseline_sent

        changes: list[Change] = []
        if previous_state is not None and previous_state.assets:
            previous = Snapshot(user=previous_state.user, assets=dict(previous_state.assets))
            changes = diff_snapshots(previous, snapshot)

        # El estado guarda SOLO la foto actual: se asigna, no se acumula. Lo
        # que ya no está en la cartera desaparece del fichero.
        new_state = self.state.upsert_user(user)
        new_state.assets = dict(snapshot.assets)

        labels = {
            iid: self.state.instrument_label(iid) for iid in snapshot.instrument_ids
        }

        if is_baseline:
            if not self.quiet_baseline:
                self.notify(render_baseline(user, snapshot, instrument_labels=labels))
            new_state.baseline_sent = True
            log.info("%s: línea base establecida", user.username)
        elif changes:
            self.notify(
                render_user_changes(user, changes, instrument_labels=labels)
            )
            log.info("%s: %s operación(es) notificadas", user.username, len(changes))
        else:
            log.info("%s: sin operaciones", user.username)

        return UserReport(
            username=user.username,
            user=user,
            changes=changes,
            baseline=is_baseline,
            requests=self.client.request_count - started,
        )

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
        """Lee la cartera y resume cada activo en (unidades, lado, peso)."""
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

            units = self._units_of(cid, instrument_id, direction)

            anteriores = assets.get(key)
            if anteriores is None:
                assets[key] = Asset(
                    instrument_id=instrument_id,
                    direction=direction,
                    units=units,
                    invested_pct=invested_pct,
                )
            else:
                # Caso raro: dos filas del mismo activo y lado. Se suman.
                assets[key] = Asset(
                    instrument_id=instrument_id,
                    direction=direction,
                    units=anteriores.units + units,
                    invested_pct=anteriores.invested_pct + invested_pct,
                )

            self._ensure_instrument(instrument_id)

        return Snapshot(user=user, assets=assets)

    def _units_of(self, cid: int, instrument_id: int, direction: str) -> float:
        """Suma las unidades de las posiciones abiertas de ese lado.

        eToro publica `Amount` en positivo también en los cortos, así que la
        distinción largo/corto se hace por `IsBuy`. Si un activo tuviera a la
        vez posiciones largas y cortas, cada lado se cuenta por separado.
        """
        payload = self.client.get_positions(cid, instrument_id)
        quiere_buy = direction.strip().lower() != "sell"
        total = 0.0
        for raw in payload.get("PublicPositions") or []:
            try:
                if bool(raw.get("IsBuy", True)) != quiere_buy:
                    continue
                total += float(raw.get("Amount") or 0.0)
            except (TypeError, ValueError):
                log.warning("Posición ilegible en %s: %s", instrument_id, raw)
        return total

    def _ensure_instrument(self, instrument_id: int) -> None:
        """Descarga el nombre del activo la primera vez que lo vemos."""
        if instrument_id in self.state.instruments:
            return
        try:
            raw = self.client.get_instrument(instrument_id)
        except EtoroError as exc:
            log.warning("Sin metadatos para %s: %s", instrument_id, exc)
            return
        if not raw:
            return
        self.state.instruments[instrument_id] = Instrument(
            instrument_id=int(raw.get("InstrumentID", instrument_id)),
            name=str(raw.get("InstrumentDisplayName") or f"Instrumento {instrument_id}"),
            symbol=str(raw.get("SymbolFull") or ""),
        )
