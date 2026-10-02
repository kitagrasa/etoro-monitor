"""Orquestación: leer carteras públicas, compararlas con el estado y avisar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

from .client import (
    Blocked,
    EtoroClient,
    EtoroError,
    PrivatePortfolio,
    UserNotFound,
)
from .diff import diff_snapshots
from .models import Change, EtoroUser, Instrument, Position, Snapshot
from .render import render_baseline, render_problem, render_user_changes
from .state import RuntimeState, State

log = logging.getLogger(__name__)


@dataclass
class UserReport:
    username: str
    user: Optional[EtoroUser]
    changes: list[Change]
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
        notify: Optional[Callable[[str], None]] = None,
        notify_errors: bool = True,
        quiet_baseline: bool = False,
    ) -> None:
        self.client = client
        self.state = state
        self.runtime = runtime or RuntimeState(state.path.with_suffix(".runtime.json"))
        self.notify = notify or (lambda _text: None)
        self.notify_errors = notify_errors
        self.quiet_baseline = quiet_baseline

    # ------------------------------------------------------------------ #
    def run(self, usernames: Sequence[str]) -> list[UserReport]:
        reports: list[UserReport] = []
        for username in usernames:
            try:
                reports.append(self._process_user(username))
            except (UserNotFound, PrivatePortfolio, Blocked, EtoroError) as exc:
                log.error("%s: %s", username, exc)
                reports.append(
                    UserReport(username=username, user=None, changes=[], error=str(exc))
                )
        return reports

    # ------------------------------------------------------------------ #
    def _process_user(self, username: str) -> UserReport:
        started = self.client.request_count
        user = self._resolve(username)
        log.info("%s -> CID %s", user.username, user.real_cid)

        snapshot = self.fetch_snapshot(user)

        previous_state = self.state.user(user.slug)
        is_baseline = previous_state is None or not previous_state.baseline_sent

        changes: list[Change] = []
        if previous_state is not None and previous_state.positions:
            previous = Snapshot(
                user=previous_state.user, positions=dict(previous_state.positions)
            )
            changes = diff_snapshots(previous, snapshot)

        # Guardar el estado nuevo
        new_state = self.state.upsert_user(user)
        new_state.positions = dict(snapshot.positions)
        new_state.known_instruments = set(snapshot.instrument_ids) | (
            previous_state.known_instruments if previous_state else set()
        )

        labels = {
            iid: self.state.instrument_label(iid) for iid in snapshot.instrument_ids
        }

        if is_baseline:
            if not self.quiet_baseline:
                self.notify(
                    render_baseline(user, snapshot, instrument_labels=labels)
                )
            new_state.baseline_sent = True
            log.info("%s: línea base establecida", user.username)
        elif changes:
            self.notify(
                render_user_changes(
                    user,
                    changes,
                    instrument_labels=labels,
                    weights=snapshot.weights,
                )
            )
            log.info("%s: %s cambio(s) notificados", user.username, len(changes))
        else:
            log.info("%s: sin cambios", user.username)

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
        # El nombre canónico lo devuelve eToro; si no, usamos lo que escribiste.
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
        cid = user.real_cid
        portfolio = self.client.get_portfolio(cid)
        exposure = self.client.get_exposure(cid)

        rows = portfolio.get("AggregatedPositions") or []
        directions = {
            int(row["InstrumentID"]): row.get("Direction", "")
            for row in rows
            if "InstrumentID" in row
        }
        instrument_ids = sorted(set(directions) | set(exposure))

        positions: dict[int, Position] = {}
        for instrument_id in instrument_ids:
            payload = self.client.get_positions(cid, instrument_id)
            for raw in payload.get("PublicPositions") or []:
                try:
                    position = Position(
                        position_id=int(raw["PositionID"]),
                        instrument_id=int(raw.get("InstrumentID", instrument_id)),
                        is_buy=bool(raw.get("IsBuy", True)),
                        amount=float(raw.get("Amount") or 0.0),
                        open_rate=float(raw.get("OpenRate") or 0.0),
                        open_datetime=str(raw.get("OpenDateTime") or ""),
                        leverage=int(raw.get("Leverage") or 1),
                        mirror_id=int(raw.get("MirrorID") or 0),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    log.warning("Posición ilegible en %s: %s (%s)", instrument_id, raw, exc)
                    continue
                positions[position.position_id] = position
            self._ensure_instrument(instrument_id)

        return Snapshot(
            user=user,
            positions=positions,
            weights=exposure,
            directions=directions,
        )

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
            instrument_type_id=int(raw.get("InstrumentTypeID") or 0),
            industry_id=int(raw.get("StocksIndustryID") or 0),
            exchange_id=int(raw.get("ExchangeID") or 0),
        )

    # ------------------------------------------------------------------ #
    def notify_error_once(self, message: str, *, cooldown_minutes: int = 180) -> None:
        """Avisa de un problema sin spamear (los crons siguen pasando)."""
        if not self.notify_errors:
            return
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        last_raw = self.runtime.get("last_error_notified_at")
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw)
            except ValueError:
                last = None
            if last and (now - last).total_seconds() < cooldown_minutes * 60:
                return
        self.notify(render_problem(None, message))
        self.runtime.set("last_error_notified_at", now.isoformat())


def build_instrument_labels(state: State, instrument_ids: Iterable[int]) -> dict[int, str]:
    return {iid: state.instrument_label(iid) for iid in instrument_ids}
