"""Lecture des critères d'attribution d'un avis et de la part du qualitatif.

Trois formes existent dans l'API du BOAMP :
- MAPA : liste structurée `criterePondere` (`critere`, `criterePCT`) ;
- formulaire national : texte libre `criteresAttrib`, souvent « voir le règlement
  de la consultation », parfois « prix 40 %, valeur technique 60 % » ;
- eForms : `cac:AwardingCriterion` avec un type (price, cost, quality) et un poids.

Quand les pondérations ne sont pas lisibles, on renvoie None : pas de points,
pas d'erreur.
"""

from __future__ import annotations

import re

from .extraction import charger_json, normaliser, parcourir

_PRIX = re.compile(r"\b(prix|cout|couts|tarif|tarifs|montant)\b")
_POIDS = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*(%|pourcents?|points?|pts)(?![a-z])")
_COUPURES = re.compile(r"[;\n]|\s[-–]\s|,(?!\d)|\b(?:et|puis|dont)\b|\b[a-z]\)|\s(?=\d{1,2}[.)]\s+[^\W\d])", re.IGNORECASE)


def _nombre(texte) -> float | None:
    try:
        return float(str(texte).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _part(poids: list[tuple[bool, float]]) -> float | None:
    """Part du qualitatif sur 100, à partir de couples (est_prix, poids)."""
    total = sum(p for _, p in poids)
    if not poids or total <= 0 or not any(prix for prix, _ in poids):
        return None
    if not any(not prix for prix, _ in poids):
        return 0.0
    return round(sum(p for prix, p in poids if not prix) * 100 / total, 1)


def _depuis_mapa(donnees) -> float | None:
    for k, v in parcourir(donnees):
        if k == "criterePondere":
            lignes = v if isinstance(v, list) else [v]
            poids = []
            for ligne in lignes:
                if not isinstance(ligne, dict):
                    continue
                p = _nombre(ligne.get("criterePCT"))
                if p is None:
                    continue
                poids.append((bool(_PRIX.search(normaliser(ligne.get("critere", "")))), p))
            return _part(poids) if poids else None
    return None


def _criteres_eforms(bloc) -> list[dict]:
    """Les critères élémentaires (ceux qui portent un type) d'un bloc AwardingCriterion."""
    trouves = []
    if isinstance(bloc, dict):
        if any(k.lower().endswith("awardingcriteriontypecode") for k in bloc):
            return [bloc]
        for v in bloc.values():
            trouves += _criteres_eforms(v)
    elif isinstance(bloc, list):
        for v in bloc:
            trouves += _criteres_eforms(v)
    return trouves


def _depuis_eforms(donnees) -> float | None:
    # Les critères sont répétés pour chaque lot : on lit le premier bloc trouvé.
    bloc = next((v for k, v in parcourir(donnees) if k == "cac:AwardingCriterion"), None)
    if bloc is None:
        return None
    poids = []
    for crit in _criteres_eforms(bloc):
        type_code = next((v for k, v in crit.items() if k.lower().endswith("awardingcriteriontypecode")), "")
        type_code = (type_code.get("#text", "") if isinstance(type_code, dict) else str(type_code)).lower()
        valeur = next((v for k, v in parcourir(crit) if k.lower().endswith("parameternumeric")), None)
        valeur = _nombre(valeur.get("#text") if isinstance(valeur, dict) else valeur)
        if type_code and valeur is not None:
            poids.append((type_code in ("price", "cost"), valeur))
    return _part(poids) if poids else None


def _depuis_texte(donnees) -> float | None:
    for k, v in parcourir(donnees):
        if k.lower() == "criteresattrib" and isinstance(v, str):
            poids = []
            # On découpe le texte brut (retours à la ligne, tirets, « et »…)
            # avant de le normaliser, pour garder les séparateurs.
            for morceau in _COUPURES.split(v.replace("%", " % ")):
                morceau = normaliser(morceau.replace("%", " pourcent "))
                for m in _POIDS.finditer(morceau):
                    p = _nombre(m.group(1))
                    if p is not None and 0 < p <= 100:
                        poids.append((bool(_PRIX.search(morceau)), p))
            if not poids:
                return None
            qualite = [p for prix, p in poids if not prix]
            total = sum(p for _, p in poids)
            # « Valeur technique 60 % dont 30 %, 20 %, 10 % » : les sous-critères
            # répètent le critère principal ; on ne garde que celui-ci.
            if total > 105 and qualite and abs(sum(qualite) - 2 * max(qualite)) < 1:
                prix_total = sum(p for prix, p in poids if prix)
                return _part([(False, max(qualite)), (True, prix_total)])
            return _part(poids)
    return None


def part_qualitatif(rec: dict) -> float | None:
    """Part des critères non-prix (qualité, technique…) sur 100, ou None si illisible."""
    donnees = charger_json(rec.get("donnees"))
    for lecteur in (_depuis_mapa, _depuis_eforms, _depuis_texte):
        part = lecteur(donnees)
        if part is not None:
            return part
    return None
