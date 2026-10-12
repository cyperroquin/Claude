"""Client de l'API open data du BOAMP (DILA, OpenDataSoft Explore v2.1).

On n'utilise que l'API : le site boamp.fr interdit l'accès automatisé.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Iterator

import requests

log = logging.getLogger(__name__)

# OpenDataSoft refuse offset + limit > 10 000 : on découpe les longues périodes par jour.
LIMITE_OFFSET = 10_000


class ClientBoamp:
    def __init__(self, config: dict, session: requests.Session | None = None):
        api = config.get("api") or {}
        self.url = api["url"]
        self.natures = list((api.get("natures") or {}).keys())
        self.taille_page = int(api.get("taille_page", 100))
        self.delai = int(api.get("delai_secondes", 60))
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "veille-boamp-bulq/1.0")

    def _get(self, params: dict) -> dict:
        for essai in range(4):
            try:
                r = self.session.get(self.url, params=params, timeout=self.delai)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                if r.status_code >= 400:
                    raise RuntimeError(f"Erreur API BOAMP {r.status_code} : {r.text[:500]}")
                return r.json()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                if essai == 3:
                    raise
                attente = 2 ** (essai + 1)
                log.warning("API BOAMP indisponible (%s), nouvel essai dans %s s", e, attente)
                time.sleep(attente)
        raise AssertionError("inaccessible")

    def champs(self) -> dict:
        """Un enregistrement brut, pour vérifier la liste réelle des champs."""
        res = self._get({"limit": 1, "order_by": "dateparution desc"})
        return res["results"][0] if res.get("results") else {}

    def _filtre(self, debut: date, fin: date | None, ouverts: bool) -> str:
        natures = ", ".join(f"'{n}'" for n in self.natures)
        w = [f"dateparution >= date'{debut.isoformat()}'", f"nature in ({natures})"]
        if fin:
            w.append(f"dateparution <= date'{fin.isoformat()}'")
        if ouverts:
            # Les procédures restreintes n'ont souvent pas de `datelimitereponse` :
            # on les garde et la date est recherchée dans `donnees` ensuite.
            w.append("(datelimitereponse > now() OR datelimitereponse is null)")
        return " AND ".join(w)

    def _pages(self, where: str) -> Iterator[dict]:
        offset = 0
        while True:
            limite = min(self.taille_page, LIMITE_OFFSET - offset)
            if limite <= 0:
                log.error("Plus de %s avis pour le filtre %s : résultat tronqué", LIMITE_OFFSET, where)
                return
            res = self._get({"where": where, "limit": limite, "offset": offset,
                             "order_by": "dateparution, idweb"})
            lot = res.get("results") or []
            yield from lot
            offset += len(lot)
            if not lot or offset >= int(res.get("total_count", 0)):
                return

    def recents(self, aujourdhui: date, heures: int) -> Iterator[dict]:
        """Avis de marché et informatifs parus récemment, encore ouverts."""
        debut = aujourdhui - timedelta(days=max(1, -(-heures // 24)))
        yield from self._pages(self._filtre(debut, None, ouverts=True))

    def periode(self, debut: date, fin: date) -> Iterator[dict]:
        """Tous les avis retenus parus entre deux dates, jour par jour (rejeu)."""
        jour = debut
        while jour <= fin:
            yield from self._pages(self._filtre(jour, jour, ouverts=False))
            jour += timedelta(days=1)

    def par_idweb(self, idweb: str) -> dict | None:
        res = self._get({"where": f'idweb="{idweb}"', "limit": 1})
        return (res.get("results") or [None])[0]
