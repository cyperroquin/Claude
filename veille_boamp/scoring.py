"""Score d'un avis selon les règles de config.yaml (section 2.5 du cahier des charges)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .extraction import Avis, normaliser

A_EXAMINER = "À examiner"
A_VERIFIER = "À vérifier"
JOURNAL = "Journal"

DATE_OK = "ok"
DATE_URGENT = "urgent"
DATE_TROP_TARD = "trop tard"
DATE_INCONNUE = "inconnue"


def motif(terme: str) -> re.Pattern:
    """Expression régulière d'un terme normalisé, chaque mot acceptant un pluriel."""
    mots = normaliser(terme).split()
    corps = r" ".join(re.escape(m) + r"[sx]?" for m in mots)
    return re.compile(r"(?<![a-z0-9])" + corps + r"(?![a-z0-9])")


def cpv_couvre(code_regle: str, code_avis: str) -> bool:
    """48000000 couvre 48xxxxxx, 72260000 couvre 7226xxxx, etc."""
    racine = code_regle.rstrip("0")
    if len(racine) < 2:
        racine = code_regle[:2]
    return code_avis.startswith(racine)


@dataclass
class Resultat:
    score: int = 0
    raisons: list[str] = field(default_factory=list)
    criteres: list[str] = field(default_factory=list)  # id des règles déclenchées
    decision: str = JOURNAL
    jours_restants: int | None = None
    statut_date: str = DATE_INCONNUE

    @property
    def urgent(self) -> bool:
        return self.statut_date == DATE_URGENT

    @property
    def a_envoyer(self) -> bool:
        return self.decision != JOURNAL and self.statut_date != DATE_TROP_TARD


class Moteur:
    def __init__(self, config: dict):
        self.config = config
        self.groupes = {
            nom: [(t, motif(t)) for t in termes]
            for nom, termes in (config.get("mots_cles") or {}).items()
        }
        self.regles = config.get("regles") or []
        self.acheteur_motifs = {
            r["id"]: [(t, motif(t)) for t in r.get("termes", [])]
            for r in self.regles if r.get("type") == "acheteur"
        }
        excl = config.get("exclusions") or {}
        self.points_exclusion = int(excl.get("points", -5))
        self.exclusions = [
            {
                "mot": e["mot"] if isinstance(e, dict) else e,
                "motif": motif(e["mot"] if isinstance(e, dict) else e),
                "champs": (e.get("champs") if isinstance(e, dict) else None) or ["objet", "description"],
                "sauf": [motif(s) for s in (e.get("sauf", []) if isinstance(e, dict) else [])],
            }
            for e in excl.get("mots", [])
        ]
        self.neutres = [motif(t) for t in config.get("neutraliser") or []]
        self.seuils = config.get("seuils") or {}
        self.dates = config.get("dates") or {}

    def _trouves(self, groupe: str, texte: str) -> list[str]:
        return [t for t, m in self.groupes.get(groupe, []) if m.search(texte)]

    def _positions(self, groupe: str, texte: str) -> list[tuple[str, int, int]]:
        """(terme, premier mot, dernier mot) de chaque occurrence du groupe."""
        out = []
        for t, m in self.groupes.get(groupe, []):
            for x in m.finditer(texte):
                debut = texte.count(" ", 0, x.start())
                out.append((t, debut, debut + x.group(0).count(" ")))
        return out

    def _croises(self, groupes: list[str], textes: list[str], distance: int | None) -> list[str] | None:
        """Un terme de chaque groupe, dans le même champ et à `distance` mots au plus."""
        for texte in textes:
            occ = [self._positions(g, texte) for g in groupes]
            if not all(occ):
                continue
            if distance is None:
                return [o[0][0] for o in occ]
            premier, *autres = occ
            for t0, d0, f0 in premier:
                choisis = [t0]
                for liste in autres:
                    proche = next((t for t, d, f in liste
                                   if max(d, d0) - min(f, f0) <= distance + 1), None)
                    if proche is None:
                        break
                    choisis.append(proche)
                else:
                    return choisis
        return None

    def _cpv(self, regle: dict, avis: Avis) -> list[str]:
        codes = [str(c) for c in regle.get("codes", [])]
        if regle.get("hierarchie", regle.get("type") == "cpv_et_mots"):
            return [c for c in avis.cpv if any(cpv_couvre(r, c) for r in codes)]
        return [c for c in avis.cpv if c in codes]

    def _neutraliser(self, texte: str) -> str:
        t = normaliser(texte)
        for m in self.neutres:
            t = m.sub(" _ ", t)
        return t

    def statut_date(self, avis: Avis, aujourdhui: date) -> tuple[str, int | None]:
        if not avis.datelimite:
            return DATE_INCONNUE, None
        jours = (avis.datelimite - aujourdhui).days
        if jours >= int(self.dates.get("jours_alerte", 7)):
            return DATE_OK, jours
        if jours >= int(self.dates.get("jours_urgent", 3)):
            return DATE_URGENT, jours
        return DATE_TROP_TARD, jours

    def evaluer(self, avis: Avis, aujourdhui: date | None = None) -> Resultat:
        champs = {"objet": self._neutraliser(avis.objet), "description": self._neutraliser(avis.description)}
        tout = champs["objet"] + " | " + champs["description"]
        acheteur = normaliser(avis.acheteur)
        res = Resultat()

        for r in self.regles:
            typ, pts = r.get("type"), int(r.get("points", 0))
            detail = None
            if typ == "cpv":
                trouves = self._cpv(r, avis)
                if trouves:
                    detail = "CPV " + ", ".join(trouves)
            elif typ == "mots":
                trouves = [t for g in r.get("groupes", []) for t in self._trouves(g, tout)]
                if trouves:
                    detail = ", ".join(f"« {t} »" for t in dict.fromkeys(trouves))
            elif typ == "mots_croises":
                choisis = self._croises(r.get("groupes", []), [champs["objet"], champs["description"]],
                                        r.get("distance_max"))
                if choisis:
                    detail = " + ".join(f"« {t} »" for t in choisis)
            elif typ == "cpv_et_mots":
                trouves = self._cpv(r, avis)
                mots = [t for g in r.get("groupes", []) for t in self._trouves(g, tout)]
                if trouves and mots:
                    detail = f"CPV {trouves[0]} + « {mots[0]} »"
            elif typ == "acheteur":
                trouves = [t for t, m in self.acheteur_motifs[r["id"]] if m.search(acheteur)]
                if trouves:
                    detail = f"« {trouves[0]} »"
            else:
                raise ValueError(f"Type de règle inconnu dans config.yaml : {typ!r}")
            if detail:
                res.score += pts
                res.criteres.append(r["id"])
                res.raisons.append(f"{pts:+d} {r.get('libelle', r['id'])} ({detail})")

        for e in self.exclusions:
            for nom in e["champs"]:
                texte = champs.get(nom, "")
                for s in e["sauf"]:
                    texte = s.sub(" ", texte)
                if e["motif"].search(texte):
                    res.score += self.points_exclusion
                    res.criteres.append(f"exclusion:{e['mot']}")
                    res.raisons.append(f"{self.points_exclusion:+d} exclusion « {e['mot']} » ({nom})")
                    break

        if res.score >= int(self.seuils.get("a_examiner", 7)):
            res.decision = A_EXAMINER
        elif res.score >= int(self.seuils.get("a_verifier", 4)):
            res.decision = A_VERIFIER

        if aujourdhui:
            res.statut_date, res.jours_restants = self.statut_date(avis, aujourdhui)
        return res
