"""Flux 2 : repérer les acheteurs de travaux actifs, futurs clients de BulQ.

Un maître d'ouvrage qui publie beaucoup d'appels d'offres de travaux reçoit et
compare beaucoup d'offres : c'est exactement le besoin que BulQ outille. On
agrège les avis de travaux des N derniers jours par acheteur, on leur donne un
score (config.yaml, section `prospects`) et on envoie chaque semaine les mieux
placés.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .extraction import charger_json, lire_avis, normaliser, parcourir
from .scoring import motif

_RE_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_RE_SIRET = re.compile(r"^\d{14}$")


@dataclass
class AvisTravaux:
    idweb: str
    objet: str
    dateparution: date | None
    nb_lots: int
    url: str


@dataclass
class Acheteur:
    cle: str
    nom: str
    avis: list[AvisTravaux] = field(default_factory=list)
    siret: str = ""
    emails: Counter = field(default_factory=Counter)
    departements: Counter = field(default_factory=Counter)
    type_acheteur: str = ""
    score: int = 0
    raisons: list[str] = field(default_factory=list)
    nb_recents: int = 0
    deja_signale: str = ""  # date du premier signalement, vide si nouveau

    @property
    def nb_lots(self) -> int:
        return sum(a.nb_lots for a in self.avis)

    @property
    def contact(self) -> str:
        return self.emails.most_common(1)[0][0] if self.emails else ""

    @property
    def lien_annuaire(self) -> str:
        if self.siret:
            return f"https://annuaire-entreprises.data.gouv.fr/etablissement/{self.siret}"
        return ""

    @property
    def derniers_avis(self) -> list[AvisTravaux]:
        return sorted(self.avis, key=lambda a: a.dateparution or date.min, reverse=True)[:3]


def _nb_lots(donnees: dict) -> int:
    """Nombre de lots d'un avis (formulaire national, MAPA ou eForms)."""
    eforms = set()
    for k, v in parcourir(donnees):
        if k == "cac:ProcurementProjectLot":
            for lot in v if isinstance(v, list) else [v]:
                ident = (lot or {}).get("cbc:ID") if isinstance(lot, dict) else None
                texte = ident.get("#text", "") if isinstance(ident, dict) else str(ident or "")
                if texte.startswith("LOT-"):
                    eforms.add(texte)
    if eforms:
        return len(eforms)
    for k, v in parcourir(donnees):
        if k == "lot":
            return len(v) if isinstance(v, list) else 1
    return 1



def _siret(donnees: dict) -> str:
    """SIRET de l'acheteur, pris seulement dans le bloc `organisme` des formulaires nationaux."""
    for k, v in parcourir(donnees):
        if k == "organisme" and isinstance(v, dict):
            s = str(v.get("codeIdentificationNational") or "").replace(" ", "")
            if _RE_SIRET.match(s):
                return s
    return ""


def _emails(donnees: dict, plateformes: list[str]) -> list[str]:
    out = []
    for k, v in parcourir(donnees):
        if k.lower().split(":")[-1] in ("adressemailcontact", "electronicmail") and isinstance(v, str):
            for m in _RE_MAIL.findall(v):
                m = m.lower()
                if not any(m.endswith("@" + d) or m.endswith("." + d) for d in plateformes):
                    out.append(m)
    return out


class Prospection:
    def __init__(self, config: dict):
        self.config = config
        self.p = config.get("prospects") or {}
        self.types = [
            (t["nom"], int(t.get("points", 0)), [motif(x) for x in t.get("termes", [])])
            for t in self.p.get("types", [])
        ]
        self.exclure = [motif(x) for x in self.p.get("exclure", [])]
        self.plateformes = [d.lower() for d in self.p.get("domaines_plateformes", [])]

    def agreger(self, enregistrements, aujourdhui: date) -> list[Acheteur]:
        acheteurs: dict[str, Acheteur] = {}
        vus: set[str] = set()
        for rec in enregistrements:
            avis = lire_avis(rec)
            if not avis.acheteur or avis.idweb in vus:
                continue
            vus.add(avis.idweb)
            cle = normaliser(avis.acheteur)
            if any(m.search(cle) for m in self.exclure):
                continue
            a = acheteurs.setdefault(cle, Acheteur(cle=cle, nom=avis.acheteur))
            donnees = charger_json(rec.get("donnees"))
            a.avis.append(AvisTravaux(avis.idweb, avis.objet, avis.dateparution,
                                      _nb_lots(donnees), avis.url))
            a.siret = a.siret or _siret(donnees)
            a.emails.update(_emails(donnees, self.plateformes))
            a.departements.update(avis.departements)
        return list(acheteurs.values())

    def noter(self, acheteurs: list[Acheteur], aujourdhui: date) -> list[Acheteur]:
        pts = self.p.get("points") or {}
        recents = aujourdhui - timedelta(days=int(self.p.get("jours_recents", 7)))
        min_avis = int(self.p.get("min_avis", 2))
        retenus = []
        for a in acheteurs:
            if len(a.avis) < min_avis:
                continue
            a.nb_recents = sum(1 for x in a.avis if x.dateparution and x.dateparution >= recents)
            total = 0.0
            p_avis = min(len(a.avis) * float(pts.get("par_avis", 1)), float(pts.get("max_avis", 15)))
            total += p_avis
            a.raisons.append(f"+{p_avis:g} : {len(a.avis)} appels d'offres de travaux")
            if a.nb_lots > len(a.avis):
                p_lots = min(a.nb_lots * float(pts.get("par_lot", 0.25)), float(pts.get("max_lots", 10)))
                total += p_lots
                a.raisons.append(f"+{p_lots:g} : {a.nb_lots} lots à comparer")
            if a.nb_recents:
                p = float(pts.get("actif_recemment", 2))
                total += p
                a.raisons.append(f"+{p:g} : actif cette semaine ({a.nb_recents} avis)")
            nom = normaliser(a.nom)
            for libelle, points, motifs in self.types:
                if any(m.search(nom) for m in motifs):
                    a.type_acheteur = libelle
                    total += points
                    a.raisons.append(f"+{points} : {libelle.lower()}")
                    break
            a.score = round(total)
            retenus.append(a)
        retenus.sort(key=lambda a: (-a.score, -len(a.avis), a.nom))
        return retenus
