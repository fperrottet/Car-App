"""Pneus suivis par paire sur une frise avant / arrière (0.7) : reprise de
l'ancien suivi par jeu, km par période et modifications faites sur la frise."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import costs
from .models import AXLES, TirePair, TirePeriod, TireSet, Vehicle
from .util import format_date


class TimelineError(ValueError):
    """Modification refusée ; le message s'affiche tel quel sous la frise."""


def axle_name(axle: str) -> str:
    return "l'avant" if axle == "avant" else "l'arrière"


# ---------------------------------------------------------------- paires et reprise


def ensure_pairs(tire_set: TireSet) -> None:
    """Un achat de 4 pneus fait 2 paires, un achat de 2 en fait une."""
    wanted = max(1, (tire_set.count or 2) // 2)
    names = {p.name for p in tire_set.pairs}
    for name in "ABCD"[:wanted]:
        if name not in names:
            tire_set.pairs.append(TirePair(name=name))


def migrate(session: Session) -> int:
    """Reprend les montages d'avant la 0.7 : un jeu de 4 monté devient sa paire A à
    l'avant et sa paire B à l'arrière ; un jeu de 2 garde sa place. Sans effet ensuite."""
    done = 0
    for tire_set in session.scalars(select(TireSet)):
        if tire_set.pairs:
            continue
        ensure_pairs(tire_set)
        pairs = tire_set.pairs
        for mount in tire_set.mounts:
            if tire_set.position == "arrière":
                targets = [(pairs[0], "arriere")]
            elif len(pairs) > 1 and tire_set.position == "les 4":
                targets = [(pairs[0], "avant"), (pairs[1], "arriere")]
            else:
                targets = [(pairs[0], "avant")]
            for pair, axle in targets:
                pair.periods.append(
                    TirePeriod(
                        axle=axle,
                        start_date=mount.mounted_date,
                        end_date=mount.removed_date,
                        start_km=mount.mounted_km,
                        end_km=mount.removed_km,
                        tread_mm=mount.tread_mm,
                    )
                )
        if tire_set.retired_date:
            for pair in pairs:
                pair.retired_date = tire_set.retired_date
        done += 1
    session.commit()
    return done


# ---------------------------------------------------------------- km et sculpture


def period_km(period: TirePeriod, points: list[tuple[date, int]], km_now: int | None) -> int | None:
    """Km parcourus pendant une période (en cours : jusqu'à aujourd'hui)."""
    start = period.start_km if period.start_km is not None else costs.km_at(points, period.start_date)
    if period.end_date is None:
        end = km_now
    else:
        end = period.end_km if period.end_km is not None else costs.km_at(points, period.end_date)
    if start is None or end is None or end < start:
        return None
    return end - start


def pair_km(pair: TirePair, points, km_now) -> int:
    return sum(period_km(p, points, km_now) or 0 for p in pair.periods)


def last_tread(pair: TirePair) -> float | None:
    measured = [p.tread_mm for p in pair.periods if p.tread_mm is not None]
    return measured[-1] if measured else None


def vehicle_pairs(session: Session, vehicle: Vehicle) -> list[TirePair]:
    return list(
        session.scalars(
            select(TirePair).join(TireSet).where(TireSet.vehicle_id == vehicle.id).order_by(TireSet.purchase_date, TirePair.name)
        )
    )


def lane(session: Session, vehicle: Vehicle, axle: str) -> list[TirePeriod]:
    return list(
        session.scalars(
            select(TirePeriod)
            .join(TirePair)
            .join(TireSet)
            .where(TireSet.vehicle_id == vehicle.id, TirePeriod.axle == axle)
            .order_by(TirePeriod.start_date)
        )
    )


def covers(period: TirePeriod, day: date) -> bool:
    return period.start_date <= day and (period.end_date is None or day < period.end_date)


# ---------------------------------------------------------------- modifications depuis la frise


def mount(session: Session, vehicle: Vehicle, pair: TirePair, axle: str, day: date, today: date) -> str:
    """Monte une paire sur un essieu à une date ; la paire qui y était retourne au garage."""
    if axle not in AXLES:
        raise TimelineError("Essieu inconnu")
    if day > today:
        raise TimelineError("La date de montage ne peut pas être dans le futur")
    if pair.retired_date and day >= pair.retired_date:
        raise TimelineError(f"Cette paire a été éliminée le {format_date(pair.retired_date)}")
    for other in pair.periods:
        if covers(other, day):
            raise TimelineError(f"Cette paire est déjà montée à {axle_name(other.axle)} le {format_date(day)}")
    periods = lane(session, vehicle, axle)
    current = next((p for p in periods if covers(p, day)), None)
    later = next((p for p in periods if p.start_date > day), None)
    end = current.end_date if current else (later.start_date if later else None)
    # La nouvelle période ne doit pas chevaucher une autre période de la même paire.
    clash = next((p for p in pair.periods if p.start_date > day and (end is None or p.start_date < end)), None)
    if clash:
        end = clash.start_date
    message = f"{pair.label} montée à {axle_name(axle)} le {format_date(day)}"
    if current:
        message += f" ; {current.pair.label} retourne au garage"
        if current.start_date == day:
            current.pair.periods.remove(current)  # delete-orphan : supprimée
        else:
            current.end_date, current.end_km = day, None
    pair.periods.append(TirePeriod(axle=axle, start_date=day, end_date=end))
    session.flush()
    return message


def move_change(session: Session, vehicle: Vehicle, period: TirePeriod, day: date, linked: bool, today: date) -> str:
    """Déplace la date de début d'une période et la fin de la précédente sur le même
    essieu ; « linked » : le changement du même jour sur l'autre essieu suit."""
    old = period.start_date
    moves = [period]
    if linked:
        for axle in AXLES:
            if axle != period.axle:
                moves += [p for p in lane(session, vehicle, axle) if p.start_date == old]
    for p in moves:
        periods = lane(session, vehicle, p.axle)
        i = periods.index(p)
        before = periods[i - 1] if i > 0 and periods[i - 1].end_date == old else None
        low = (before.start_date if before else date.min) + timedelta(days=1)
        high = min(d for d in (p.end_date, today + timedelta(days=1)) if d) - timedelta(days=1)
        if not low <= day <= high:
            raise TimelineError(f"Date hors des périodes voisines ({format_date(low)} au {format_date(high)})")
        p.start_date, p.start_km = day, None
        if before:
            before.end_date, before.end_km = day, None
    session.flush()
    side = "avant et arrière" if len(moves) > 1 else axle_name(period.axle).removeprefix("l'")
    return f"Changement {side} déplacé du {format_date(old)} au {format_date(day)}"


def delete_period(session: Session, vehicle: Vehicle, period: TirePeriod) -> str:
    """Supprime une période ; la précédente sur l'essieu reprend sa place si elles se touchaient."""
    periods = lane(session, vehicle, period.axle)
    i = periods.index(period)
    before = periods[i - 1] if i > 0 and periods[i - 1].end_date == period.start_date else None
    if before:
        before.end_date, before.end_km = period.end_date, period.end_km
    label = period.pair.label
    period.pair.periods.remove(period)
    session.flush()
    return f"Période supprimée : {label}"


FAR = date(9999, 12, 31)


def clear_axle(session: Session, vehicle: Vehicle, axle: str, start: date, end: date | None, keep: TirePeriod) -> list[str]:
    """Libère l'essieu entre start et end pour la période « keep » : les autres paires
    y sont raccourcies, coupées en deux ou retirées. Renvoie ce qui a changé."""
    stop = end or FAR
    changes = []
    for q in lane(session, vehicle, axle):
        if q is keep:
            continue
        q_end = q.end_date or FAR
        if q_end <= start or q.start_date >= stop:
            continue
        name = q.pair.label
        if q.start_date >= start and q_end <= stop:
            q.pair.periods.remove(q)
            changes.append(f"{name} retirée de cette période")
        elif q.start_date < start and q_end > stop:
            q.pair.periods.append(TirePeriod(axle=axle, start_date=stop, end_date=q.end_date, end_km=q.end_km))
            q.end_date, q.end_km = start, None
            changes.append(f"{name} coupée en deux")
        elif q.start_date < start:
            q.end_date, q.end_km = start, None
            changes.append(f"{name} démontée le {format_date(start)}")
        else:
            q.start_date, q.start_km = stop, None
            changes.append(f"{name} montée le {format_date(stop)}")
    return changes


def set_period(session: Session, vehicle: Vehicle, period: TirePeriod, axle: str, start: date | None,
               end: date | None, today: date) -> str:
    """Dates d'entrée et de sortie saisies à la main pour une période (nouvelle ou existante)."""
    if axle not in AXLES:
        raise TimelineError("Choisis l'avant ou l'arrière")
    if start is None:
        raise TimelineError("Date de montage manquante")
    if start > today or (end and end > today):
        raise TimelineError("Les dates ne peuvent pas être dans le futur")
    if end is not None and end <= start:
        raise TimelineError("La date de démontage doit suivre la date de montage")
    pair = period.pair
    if pair.retired_date and (end is None or end > pair.retired_date):
        raise TimelineError(f"Cette paire a été éliminée le {format_date(pair.retired_date)} : démonte-la au plus tard ce jour-là")
    for other in pair.periods:
        if other is not period and other.start_date < (end or FAR) and start < (other.end_date or FAR):
            raise TimelineError(
                f"Cette paire est déjà à {axle_name(other.axle)} du {format_date(other.start_date)}"
                f" au {format_date(other.end_date) if other.end_date else 'aujourd’hui'}"
            )
    if start != period.start_date:
        period.start_km = None
    if end != period.end_date:
        period.end_km = None
    period.axle, period.start_date, period.end_date = axle, start, end
    session.flush()
    changes = clear_axle(session, vehicle, axle, start, end, period)
    session.flush()
    span = f"du {format_date(start)} au {format_date(end)}" if end else f"depuis le {format_date(start)}"
    message = f"{pair.label} à {axle_name(axle)} {span}"
    return message + (f" ; {', '.join(changes)}" if changes else "")


def end_period(period: TirePeriod, day: date, tread: float | None) -> str:
    if day <= period.start_date:
        raise TimelineError("La date de démontage doit suivre la date de montage")
    if period.end_date is not None and day > period.end_date:
        raise TimelineError("La période se termine déjà avant cette date")
    period.end_date, period.end_km = day, None
    if tread is not None:
        period.tread_mm = tread
    return f"{period.pair.label} démontée le {format_date(day)}"


def retire(pair: TirePair, day: date, tread: float | None) -> str:
    """Élimine une paire : sa période en cours se termine ce jour-là."""
    for p in pair.periods:
        if p.end_date is None or p.end_date > day:
            if p.start_date >= day:
                raise TimelineError(f"La paire est montée le {format_date(p.start_date)}, après cette date")
            p.end_date, p.end_km = day, None
    if tread is not None and pair.periods:
        pair.periods[-1].tread_mm = tread
    pair.retired_date = day
    return f"{pair.label} éliminée le {format_date(day)}"


def season_change(session: Session, vehicle: Vehicle, day: date, today: date) -> str:
    """Été <-> hiver sur les deux essieux, avec les paires libres de l'autre saison."""
    pairs = [p for p in vehicle_pairs(session, vehicle) if not p.retired_date]
    plans = []
    used: set[int] = set()
    for axle in AXLES:
        current = next((p for p in lane(session, vehicle, axle) if covers(p, day)), None)
        want = "hiver" if current is None or current.pair.tire_set.season != "hiver" else "été"
        free = [
            p for p in pairs
            if p.tire_set.season in (want, "4 saisons") and p.id not in used and not any(covers(q, day) for q in p.periods)
        ]
        if not free:
            raise TimelineError(f"Pas de paire « {want} » libre au garage pour {axle_name(axle)}")
        # De préférence la paire qui était sur cet essieu la dernière fois.
        free.sort(key=lambda p: not (p.periods and p.periods[-1].axle == axle))
        used.add(free[0].id)
        plans.append((free[0], axle))
    for pair, axle in plans:
        mount(session, vehicle, pair, axle, day, today)
    return f"Changement de saison noté le {format_date(day)} sur les deux essieux"


# ---------------------------------------------------------------- état envoyé à la frise


def timeline(session: Session, vehicle: Vehicle, today: date) -> dict:
    points = costs.odometer_points(session, vehicle)
    km_now = costs.current_km(session, vehicle)
    pairs = vehicle_pairs(session, vehicle)
    sets = []
    for tire_set in sorted({p.tire_set for p in pairs}, key=lambda s: (s.purchase_date or date.min, s.id)):
        sets.append({
            "id": tire_set.id,
            "label": tire_set.label,
            "season": tire_set.season,
            "size": tire_set.size,
            "count": tire_set.count,
            "purchase_date": tire_set.purchase_date.isoformat() if tire_set.purchase_date else None,
            "pairs": [
                {
                    "id": p.id,
                    "name": p.name,
                    "label": p.label,
                    "retired": p.retired_date.isoformat() if p.retired_date else None,
                    "km": pair_km(p, points, km_now),
                    "tread": last_tread(p),
                    "axle": next((q.axle for q in p.periods if q.end_date is None), None),
                }
                for p in tire_set.pairs
            ],
        })
    periods = [
        {
            "id": q.id,
            "pair": q.pair_id,
            "axle": q.axle,
            "start": q.start_date.isoformat(),
            "end": q.end_date.isoformat() if q.end_date else None,
            "km": period_km(q, points, km_now),
            "tread": q.tread_mm,
        }
        for axle in AXLES
        for q in lane(session, vehicle, axle)
    ]
    first = min([vehicle.purchase_date] + [date.fromisoformat(p["start"]) for p in periods], default=None, key=lambda d: d or today)
    return {"today": today.isoformat(), "start": (first or today).isoformat(), "sets": sets, "periods": periods}
