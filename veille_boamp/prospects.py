"""Flux 2 : repérer les acheteurs de travaux actifs, futurs clients de BulQ.

Un maître d'ouvrage qui publie beaucoup d'appels d'offres de travaux reçoit et
compare beaucoup d'offres : c'est exactement le besoin que BulQ outille. On
agrège les avis de travaux des N derniers jours par acheteur, on leur donne un
score (config.yaml, section `prospects`) et on envoie chaque semaine les mieux
placés.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .criteres import part_qualitatif
from .extraction import charger_json, lire_avis, normaliser, parcourir
from .scoring import motif

log = logging.getLogger(__name__)

_RE_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_RE_SIRET = re.compile(r"^\d{14}$")
_RE_SANS_REPONSE = re.compile(r"^(ne[-_.]?pas[-_.]?repondre|no[-_.]?reply|do[-_.]?not[-_.]?reply)\b")


@dataclass
class AvisTravaux:
    idweb: str
    objet: str
    dateparution: date | None
    nb_lots: int
    url: str
    datelimite: date | None = None
    jours_restants: int | None = None
    part_qualitatif: float | None = None
    procedures: list[str] = field(default_factory=list)  # libellés B2 trouvés


@dataclass
class Acheteur:
    cle: str
    nom: str
    avis: list[AvisTravaux] = field(default_factory=list)
    siret: str = ""
    emails: Counter = field(default_factory=Counter)
    telephones: Counter = field(default_factory=Counter)
    profil_acheteur: str = ""
    departements: Counter = field(default_factory=Counter)
    type_acheteur: str = ""
    score: int = 0
    raisons: list[str] = field(default_factory=list)
    nb_recents: int = 0
    deja_signale: str = ""  # date du premier signalement, vide si nouveau
    _date_profil: date = date.min

    @property
    def nb_lots(self) -> int:
        return sum(a.nb_lots for a in self.avis)

    @property
    def contact(self) -> str:
        return self.emails.most_common(1)[0][0] if self.emails else ""

    @property
    def telephone(self) -> str:
        return self.telephones.most_common(1)[0][0] if self.telephones else ""

    @property
    def lien_annuaire(self) -> str:
        if self.siret:
            return f"https://annuaire-entreprises.data.gouv.fr/etablissement/{self.siret}"
        return ""

    @property
    def derniers_avis(self) -> list[AvisTravaux]:
        return sorted(self.avis, key=lambda a: a.dateparution or date.min, reverse=True)[:3]

    @property
    def prochaine_echeance(self) -> int | None:
        """Jours restants avant la date limite la plus proche encore à venir."""
        jours = [a.jours_restants for a in self.avis if a.jours_restants is not None and a.jours_restants >= 0]
        return min(jours) if jours else None


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
                if any(m.endswith("@" + d) or m.endswith("." + d) for d in plateformes):
                    continue
                if _RE_SANS_REPONSE.match(m):  # nepasrepondre@…, noreply@… : inutilisables
                    continue
                out.append(m)
    return out


def _texte(v) -> str:
    return str(v.get("#text", "") if isinstance(v, dict) else v or "").strip()


def _telephone(brut: str) -> str:
    """Numéro lisible : « +33 0546370035 » → « 05 46 37 00 35 » ; sinon tel quel."""
    chiffres = re.sub(r"\D", "", brut or "")
    if chiffres.startswith("33") and len(chiffres) >= 11:
        chiffres = "0" + chiffres[2:].lstrip("0")
    if len(chiffres) == 10 and chiffres.startswith("0"):
        return " ".join(chiffres[i:i + 2] for i in range(0, 10, 2))
    return (brut or "").strip()


def _ids_acheteur_eforms(donnees) -> set[str]:
    """Identifiants (ORG-xxxx) de l'acheteur dans un avis eForms."""
    ids = set()
    for k, v in parcourir(donnees):
        if k == "cac:ContractingParty":
            for cp in v if isinstance(v, list) else [v]:
                parti = (cp or {}).get("cac:Party") if isinstance(cp, dict) else None
                ident = (parti or {}).get("cac:PartyIdentification") if isinstance(parti, dict) else None
                for i in ident if isinstance(ident, list) else [ident]:
                    if isinstance(i, dict) and i.get("cbc:ID"):
                        ids.add(_texte(i["cbc:ID"]))
    return ids


def _telephones(donnees) -> list[str]:
    """Téléphone de l'acheteur : `telContact` (formulaires nationaux), ou le contact
    de l'organisation acheteuse en eForms (pas la plateforme ni le tribunal)."""
    out = [_telephone(_texte(v)) for k, v in parcourir(donnees) if k == "telContact" and _texte(v)]
    if out:
        return out
    ids = _ids_acheteur_eforms(donnees)
    for k, v in parcourir(donnees):
        if k == "efac:Company" and isinstance(v, dict):
            ident = v.get("cac:PartyIdentification")
            ident = ident[0] if isinstance(ident, list) and ident else ident
            if isinstance(ident, dict) and _texte(ident.get("cbc:ID")) in ids:
                contact = v.get("cac:Contact") or {}
                if isinstance(contact, dict) and _texte(contact.get("cbc:Telephone")):
                    out.append(_telephone(_texte(contact["cbc:Telephone"])))
    return out


def _profil_acheteur(donnees) -> str:
    """Lien vers le profil acheteur (où se trouve le dossier de consultation)."""
    for k, v in parcourir(donnees):
        if k in ("urlProfilAch", "urlProfilAcheteur", "cbc:BuyerProfileURI") and _texte(v):
            url = _texte(v)
            return url if re.match(r"https?://", url, re.I) else "https://" + url
    return ""


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
        self.procedures = [
            (r["libelle"], float(r.get("points", 0)), [motif(x) for x in r.get("termes", [])],
             [c.lower() for c in r.get("codes", [])])
            for r in self.p.get("procedures", [])
        ]

    def _procedures(self, rec: dict, donnees: dict, avis) -> list[str]:
        """Libellés des procédures « qui répètent les notations » reconnues dans l'avis."""
        texte = normaliser(" ".join([avis.objet, avis.description, rec.get("procedure_libelle") or ""]))
        codes = {
            (v.get("#text", "") if isinstance(v, dict) else str(v)).lower()
            for k, v in parcourir(donnees)
            if k in ("cbc:ProcedureCode", "cbc:ContractingSystemTypeCode")
        }
        return [libelle for libelle, _, motifs, codes_regle in self.procedures
                if any(m.search(texte) for m in motifs) or codes & set(codes_regle)]

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
            part = part_qualitatif(rec)
            if part is None:
                log.debug("%s : critères d'attribution absents ou illisibles", avis.idweb)
            a.avis.append(AvisTravaux(
                avis.idweb, avis.objet, avis.dateparution, _nb_lots(donnees), avis.url,
                datelimite=avis.datelimite,
                jours_restants=(avis.datelimite - aujourdhui).days if avis.datelimite else None,
                part_qualitatif=part,
                procedures=self._procedures(rec, donnees, avis),
            ))
            a.siret = a.siret or _siret(donnees)
            a.emails.update(_emails(donnees, self.plateformes))
            a.telephones.update(_telephones(donnees))
            # Profil acheteur de l'avis le plus récent qui en donne un.
            profil = _profil_acheteur(donnees)
            if profil and (not a.profil_acheteur or (avis.dateparution or date.min) >= a._date_profil):
                a.profil_acheteur, a._date_profil = profil, avis.dateparution or date.min
            a.departements.update(avis.departements)
        lisibles = sum(1 for a in acheteurs.values() for x in a.avis if x.part_qualitatif is not None)
        total = sum(len(a.avis) for a in acheteurs.values())
        log.info("Critères d'attribution lisibles dans %s avis sur %s", lisibles, total)
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
            # Ajouts du correctif « notation » : chaque point compte une fois par acheteur,
            # dès qu'au moins un de ses avis remplit la condition.
            parts = [x.part_qualitatif for x in a.avis if x.part_qualitatif is not None]
            if parts and max(parts) >= float(pts.get("qualitatif_min", 50)):
                p = float(pts.get("qualitatif", 3))
                total += p
                a.raisons.append(f"+{p:g} : qualitatif {max(parts):g} %")
            for libelle, points, _, _ in self.procedures:
                if any(libelle in x.procedures for x in a.avis):
                    total += points
                    a.raisons.append(f"+{points:g} : {libelle}")
            mini, maxi = (pts.get("echeance_jours") or [7, 28])[:2]
            echeances = [x.jours_restants for x in a.avis
                         if x.jours_restants is not None and mini <= x.jours_restants <= maxi]
            if echeances:
                p = float(pts.get("echeance", 2))
                total += p
                a.raisons.append(f"+{p:g} : échéance dans {min(echeances)} jours")
            a.score = round(total)
            retenus.append(a)
        retenus.sort(key=lambda a: (
            -a.score,
            a.prochaine_echeance if a.prochaine_echeance is not None else 10**6,
            -len(a.avis), a.nom,
        ))
        return retenus
