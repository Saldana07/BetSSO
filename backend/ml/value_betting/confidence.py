"""Etiqueta de confianza de un pick: ¿cuánto fiarse de que su ventaja es real?

Se basa en la HABILIDAD demostrada del modelo en ese mercado, no en su
accuracy cruda. La diferencia importa:

  habilidad = accuracy − base_accuracy

donde base_accuracy es lo que acertaría alguien sin ninguna información:
  - en mercados validados por clasificación (córners): la tasa de la clase
    mayoritaria — en Over 8.5 córners casi siempre sale Over (59.9%), así que
    60.4% de accuracy es ~0 habilidad;
  - en mercados validados por apuestas colocadas (1X2, doble oportunidad):
    la probabilidad implícita media de las cuotas tomadas — acertar más que
    eso es, literalmente, ganarle al mercado.

Sin restar la base, un 60% en córners parecería igual de bueno que un 60% en
victoria local (cuya base es 44%), cuando uno es ruido y el otro es una
ventaja grande. Por eso la etiqueta anterior era engañosa.

El EV NO sube la confianza. Un EV grande es más a menudo un modelo mal
calibrado que una casa equivocada (por eso existe max_plausible_ev); aquí
solo actúa como piso: un pick con +0.5% de EV no merece "ALTA" aunque el
modelo sea bueno.
"""
from dataclasses import dataclass

MIN_BACKTEST_BETS = 100  # por debajo de esto la habilidad medida es ruido


@dataclass(frozen=True)
class SkillEvidence:
    accuracy: float | None
    base_accuracy: float | None
    n_bets: int | None

    @property
    def skill(self) -> float | None:
        if self.accuracy is None or self.base_accuracy is None:
            return None
        return self.accuracy - self.base_accuracy


def confidence_label(expected_value: float, evidence: SkillEvidence) -> str:
    skill = evidence.skill
    n = evidence.n_bets or 0

    if skill is None or n < MIN_BACKTEST_BETS:
        return "BAJA"
    if skill >= 0.05 and expected_value >= 0.03:
        return "ALTA"
    if skill >= 0.02 and expected_value >= 0.02:
        return "MEDIA"
    return "BAJA"
