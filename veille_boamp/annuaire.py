"""Vérifier qu'un numéro est bien le standard d'un organisme public.

Source : l'Annuaire de l'administration (service-public.fr, DILA), en open data
et sans clé. Il donne, pour chaque collectivité ou administration, le numéro
d'accueil officiel. Une requête par acheteur listé (25 par semaine au plus),
quelques kilo-octets chacune.

Une recherche web générale (Google…) n'est pas utilisable depuis GitHub
Actions sans service payant ; l'annuaire officiel est plus fiable pour ce cas.
"""

from __future__ import annotations

import json
import logging
import re

import requests

from .extraction import normaliser

log = logging.getLogger(__name__)

URL = ("https://api-lannuaire.service-public.fr/api/explore/v2.1/catalog/datasets/"
       "api-lannuaire-administration/records")
# Noms des fiches principales dans l'annuaire (« Conseil départemental - Somme »…).
_RE_PRINCIPALE = re.compile(r"^(conseil departemental|conseil regional|mairie|metropole|eurometropole|"
                            r"communaute urbaine|communaute d agglomeration|communaute de communes|"
                            r"agglomeration|prefecture|collectivite)\b")
_RE_NUMERO = re.compile(r"(?:\+33\s*\(?0?\)?\s*|\b0)[1-9](?:[\s.-]?\d{2}){4}\b")


def _chiffres(numero: str) -> str:
    """« +33 3 87 33 60 00 » → « 0387336000 »."""
    c = re.sub(r"\D", "", numero)
    if c.startswith("33") and len(c) >= 11:
        c = "0" + c[2:].lstrip("0")
    return c


def numeros_du_texte(texte: str) -> set[str]:
    return {c for c in (_chiffres(m) for m in _RE_NUMERO.findall(texte)) if len(c) == 10}


def _champs_telephone(rec: dict) -> str:
    """Tout le texte des champs « téléphone » d'une fiche (format variable : texte ou JSON)."""
    morceaux = []
    for k, v in rec.items():
        if "telephone" in k.lower() or k.lower() in ("tel", "telephones"):
            morceaux.append(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return " ".join(morceaux)


def _nom(rec: dict) -> str:
    return str(rec.get("nom") or rec.get("name") or "")


class Annuaire:
    def __init__(self, config: dict, session: requests.Session | None = None):
        a = (config.get("prospects") or {}).get("annuaire") or {}
        self.url = a.get("url", URL)
        self.delai = int(a.get("delai_secondes", 20))
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "veille-boamp-bulq/1.0")

    def _fiches(self, where: str) -> list[dict]:
        for essai in range(2):
            try:
                r = self.session.get(self.url, params={"where": where, "limit": 50}, timeout=self.delai)
                r.raise_for_status()
                return r.json().get("results") or []
            except (requests.RequestException, ValueError) as e:
                if essai:
                    raise
                log.debug("Annuaire indisponible (%s), nouvel essai", e)
        return []

    def standards(self, siret: str, nom_acheteur: str) -> set[str] | None:
        """Numéros d'accueil officiels de l'organisme (10 chiffres), ou None si
        l'organisme n'a pas été trouvé ou si l'annuaire n'a pas répondu."""
        if not siret:
            return None
        try:
            fiches = self._fiches(f'startswith(siret, "{siret[:9]}")')
        except (requests.RequestException, ValueError) as e:
            log.warning("Annuaire de l'administration : pas de réponse pour %s (%s)", nom_acheteur, e)
            return None
        if fiches and not getattr(self, "_champs_vus", False):
            self._champs_vus = True
            log.info("Annuaire : champs d'une fiche : %s", ", ".join(sorted(fiches[0])))
        # La fiche de l'établissement exact, sinon celle qui porte le nom de l'acheteur.
        retenues = [f for f in fiches if str(f.get("siret") or "").replace(" ", "") == siret]
        if not retenues:
            cle = normaliser(nom_acheteur)
            retenues = [f for f in fiches if normaliser(_nom(f)) and
                        (normaliser(_nom(f)) in cle or cle in normaliser(_nom(f)))]
        # Plusieurs fiches (antennes, centres sociaux…) : on ne garde que la fiche
        # principale de l'organisme ; sans elle, on ne vérifie rien plutôt que de
        # prendre le numéro d'une antenne pour le standard.
        if len(retenues) > 1:
            principales = [f for f in retenues if _RE_PRINCIPALE.match(normaliser(_nom(f)))]
            retenues = principales if principales else (retenues if len(retenues) <= 2 else [])
        numeros = set()
        for f in retenues:
            numeros |= numeros_du_texte(_champs_telephone(f))
        log.info("Annuaire : %s → %s fiche(s) pour le SIREN, %s retenue(s) [%s], standard(s) %s",
                 nom_acheteur, len(fiches), len(retenues),
                 " ; ".join(_nom(f) for f in retenues[:3]), sorted(numeros) or "aucun")
        return numeros or None
