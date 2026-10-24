"""Picks ya emitidos: no se borran al rehacer los del día.

Rehacer los picks con cuotas nuevas sirve (el precio se mueve), pero un pick
que ya se mostró pudo haberse apostado. Antes se borraba si con las cuotas
nuevas ya no pasaba el filtro: el 25/09 desaparecieron así "Unión Magdalena o
empate" y el de Chico-Pasto, que el usuario había visto, y el contador solo
contaba los picks que sobrevivían a cada repaso.

Regla: lo emitido se queda tal cual se emitió (cuota y EV de ese momento) y
se liquida como cualquier otro; lo nuevo solo ocupa los cupos que queden, en
partidos (y jugadores) que no tengan ya un pick.
"""
from collections.abc import Callable, Hashable, Iterable
from typing import TypeVar

P = TypeVar("P")


def merge_with_issued(
    candidates: list[P],
    issued: list[P],
    n: int,
    unique_key: Callable[[P], Hashable],
    group_keys: Callable[[P], Iterable[Hashable]],
) -> tuple[list[P], list[P]]:
    """(candidatos a guardar, picks nuevos elegidos, ya marcados).

    `unique_key`: lo que no puede repetirse en la tabla (partido, mercado,
    selección): un candidato igual a un pick emitido no se guarda otra vez.
    `group_keys`: lo que no puede tener dos picks (el partido; en jugadores,
    también el jugador). Los nuevos se eligen por EV, de mayor a menor."""
    taken = {unique_key(p) for p in issued}
    to_store = [c for c in candidates if unique_key(c) not in taken]
    blocked = {k for p in issued for k in group_keys(p)}
    chosen: list[P] = []
    for c in sorted(to_store, key=lambda c: -float(c.expected_value)):
        if getattr(c, "kelly_fraction", 1) is None:
            continue
        if len(issued) + len(chosen) >= n:
            break
        keys = set(group_keys(c))
        if keys & blocked:
            continue
        chosen.append(c)
        blocked |= keys
    for c in chosen:
        c.is_pick_of_day = True
    return to_store, chosen
