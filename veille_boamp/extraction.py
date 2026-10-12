"""Lecture tolérante d'un enregistrement de l'API BOAMP.

Les champs à plat (`idweb`, `objet`, `nomacheteur`…) sont fiables. Le code CPV,
la description et parfois la date limite ne se trouvent que dans `donnees`
(chaîne JSON) ou `gestion`, avec une structure qui change selon le formulaire
(FNSimple, MAPA, eForms européen…). On les cherche donc par nom de clé, à
n'importe quelle profondeur, plutôt que par un chemin fixe.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator

CHAMPS_ATTENDUS = [
    "idweb", "objet", "nomacheteur", "dateparution", "datelimitereponse",
    "famille", "nature", "type_marche", "procedure_libelle", "code_departement",
    "descripteur_code", "descripteur_libelle", "donnees", "gestion",
]

_RE_CPV = re.compile(r"(?<!\d)(\d{8})(?:-\d)?(?!\d)")
_RE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Clés dont la valeur contient un code CPV.
_CLES_CPV = ("cpv", "itemclassificationcode", "classprincipale", "classsupplementaire")
# Clés dont la valeur est un texte descriptif utile.
_CLES_TEXTE = ("description", "intitule", "objet", "resume_objet")
# Sous-arbres de texte juridique ou de critères, sans intérêt pour la veille.
_CLES_IGNOREES = ("appeal", "awardingcriter", "legislation", "qualification",
                  "critere", "recours", "capacite", "justifications")
# Dates limites, par ordre de préférence.
_CLES_DATE_LIMITE = (
    "tendersubmissiondeadlineperiod",
    "datereceptionoffres",
    "datereceptioncandidatures",
    "participationrequestreceptionperiod",
    "date_limite_reponse",
)


def normaliser(texte: str | None) -> str:
    """Minuscules, sans accents ni ponctuation, espaces simples."""
    if not texte:
        return ""
    t = texte.replace("œ", "oe").replace("Œ", "oe").replace("æ", "ae").replace("Æ", "ae")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def charger_json(valeur: Any) -> Any:
    if isinstance(valeur, str):
        try:
            return json.loads(valeur)
        except ValueError:
            return {}
    return valeur or {}


def parcourir(obj: Any, cle: str = "") -> Iterator[tuple[str, Any]]:
    """Produit (clé, valeur) pour chaque nœud de l'arbre JSON."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from parcourir(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield cle, v
            yield from parcourir(v, cle)


def _textes(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if not k.startswith("@"):
                yield from _textes(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _textes(v)


def _parcourir_texte(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if any(x in kl for x in _CLES_IGNOREES):
                continue
            nom = kl.split(":")[-1]
            if any(nom.endswith(x) for x in _CLES_TEXTE):
                yield from _textes(v)
            else:
                yield from _parcourir_texte(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _parcourir_texte(v)


@dataclass
class Avis:
    idweb: str
    objet: str
    acheteur: str
    nature: str
    famille: str
    dateparution: date | None
    datelimite: date | None
    source_datelimite: str
    cpv: list[str]
    description: str
    departements: list[str] = field(default_factory=list)
    brut: dict = field(default_factory=dict, repr=False)

    @property
    def url(self) -> str:
        return f"https://www.boamp.fr/avis/detail/{self.idweb}"


def _en_date(valeur: Any) -> date | None:
    if not valeur:
        return None
    m = _RE_DATE.search(str(valeur))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(0))
    except ValueError:
        return None


def extraire_cpv(rec: dict) -> list[str]:
    codes: list[str] = []
    sources = [rec, charger_json(rec.get("donnees")), charger_json(rec.get("gestion"))]
    for source in sources:
        for k, v in parcourir(source):
            if any(x in k.lower() for x in _CLES_CPV):
                for s in _textes(v) if not isinstance(v, (int, float)) else [str(v)]:
                    codes.extend(_RE_CPV.findall(s))
    return list(dict.fromkeys(codes))


def extraire_description(rec: dict, objet: str) -> str:
    vus = {normaliser(objet)}
    morceaux = []
    for t in _parcourir_texte(charger_json(rec.get("donnees"))):
        t = t.strip()
        n = normaliser(t)
        if len(n) < 4 or n in ("true", "false", "sans objet") or n in vus:
            continue
        vus.add(n)
        morceaux.append(t)
    return "\n".join(morceaux)


def extraire_datelimite(rec: dict) -> tuple[date | None, str]:
    d = _en_date(rec.get("datelimitereponse"))
    if d:
        return d, "datelimitereponse"
    sources = [charger_json(rec.get("gestion")), charger_json(rec.get("donnees"))]
    for cle in _CLES_DATE_LIMITE:
        for source in sources:
            for k, v in parcourir(source):
                if k.lower().split(":")[-1] == cle:
                    # eForms : {"cbc:EndDate": "2026-11-04+01:00", ...}
                    if isinstance(v, dict):
                        v = next((x for kk, x in v.items() if kk.lower().endswith("enddate")), None)
                    d = _en_date(v)
                    if d:
                        return d, cle
    return None, ""


def lire_avis(rec: dict) -> Avis:
    objet = (rec.get("objet") or "").strip()
    datelimite, source = extraire_datelimite(rec)
    deps = rec.get("code_departement") or []
    if isinstance(deps, str):
        deps = [deps]
    return Avis(
        idweb=str(rec.get("idweb") or rec.get("id") or ""),
        objet=objet,
        acheteur=(rec.get("nomacheteur") or "").strip(),
        nature=rec.get("nature") or "",
        famille=rec.get("famille") or "",
        dateparution=_en_date(rec.get("dateparution")),
        datelimite=datelimite,
        source_datelimite=source,
        cpv=extraire_cpv(rec),
        description=extraire_description(rec, objet),
        departements=list(deps),
        brut=rec,
    )
