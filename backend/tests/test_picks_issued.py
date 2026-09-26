"""Un pick emitido no se borra al rehacer los picks del día."""
from types import SimpleNamespace

from ml.value_betting.picks import merge_with_issued


def _p(match, market, sel, ev, pick=False):
    return SimpleNamespace(match_id=match, market=market, selection=sel, expected_value=ev, is_pick_of_day=pick)


def _key(p):
    return p.match_id, p.market, p.selection


def test_el_emitido_se_queda_y_ocupa_su_cupo():
    # "Unión o empate" se emitió anoche; con las cuotas de hoy ya no pasaría el filtro.
    issued = [_p(1, "DOUBLE_CHANCE", "1X", 0.046, pick=True)]
    candidates = [_p(1, "OU_1.5", "OVER", 0.09), _p(2, "CARDS_OU_4.5", "UNDER", 0.05), _p(3, "SHOTS_OU_24.5", "OVER", 0.04)]
    to_store, chosen = merge_with_issued(candidates, issued, 2, _key, lambda p: [p.match_id])
    assert [c.match_id for c in chosen] == [2]            # un solo cupo libre, y nunca en el partido 1
    assert all(c.is_pick_of_day == (c in chosen) for c in to_store)
    assert issued[0].is_pick_of_day                         # intacto


def test_no_se_duplica_el_emitido():
    issued = [_p(1, "DOUBLE_CHANCE", "1X", 0.046, pick=True)]
    same = _p(1, "DOUBLE_CHANCE", "1X", 0.03)              # el mismo mercado, con la cuota nueva
    to_store, chosen = merge_with_issued([same], issued, 5, _key, lambda p: [p.match_id])
    assert to_store == [] and chosen == []                   # la tabla no admite dos iguales


def test_sin_emitidos_elige_el_mejor_de_cada_partido():
    candidates = [_p(1, "A", "OVER", 0.03), _p(1, "B", "OVER", 0.08), _p(2, "C", "UNDER", 0.05)]
    _, chosen = merge_with_issued(candidates, [], 5, _key, lambda p: [p.match_id])
    assert [(c.match_id, c.market) for c in chosen] == [(1, "B"), (2, "C")]


def test_jugadores_un_pick_por_partido_y_por_jugador():
    issued = [SimpleNamespace(game_id="g1", market="P7_PTS_24.5", selection="OVER", expected_value=0.05, is_pick_of_day=True)]
    cands = [SimpleNamespace(game_id="g2", market="P7_REB_8.5", selection="OVER", expected_value=0.09, is_pick_of_day=False),
             SimpleNamespace(game_id="g2", market="P9_AST_6.5", selection="UNDER", expected_value=0.04, is_pick_of_day=False)]
    player = lambda p: int(p.market[1:].split("_")[0])
    _, chosen = merge_with_issued(cands, issued, 3, lambda p: (p.game_id, p.market, p.selection),
                                  lambda p: [("partido", p.game_id), ("jugador", player(p))])
    assert [c.market for c in chosen] == ["P9_AST_6.5"]     # el jugador 7 ya tiene pick
