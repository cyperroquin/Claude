"""Journal SQLite : tous les avis analysés, et la trace des envois."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from .extraction import Avis
from .scoring import Resultat

SCHEMA = """
CREATE TABLE IF NOT EXISTS avis (
    idweb TEXT PRIMARY KEY,
    objet TEXT,
    acheteur TEXT,
    nature TEXT,
    famille TEXT,
    dateparution TEXT,
    datelimite TEXT,
    cpv TEXT,
    score INTEGER,
    decision TEXT,
    statut_date TEXT,
    raisons TEXT,
    url TEXT,
    premiere_analyse TEXT,
    derniere_analyse TEXT,
    envoye_le TEXT
);
CREATE TABLE IF NOT EXISTS acheteurs_signales (
    cle TEXT PRIMARY KEY,
    nom TEXT,
    premier_signalement TEXT,
    dernier_signalement TEXT,
    nb_signalements INTEGER
);
CREATE TABLE IF NOT EXISTS envois (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    envoye_le TEXT,
    type TEXT,
    nb_avis INTEGER,
    sujet TEXT
);
"""


class Journal:
    def __init__(self, chemin: str | Path):
        self.cx = sqlite3.connect(str(chemin))
        self.cx.row_factory = sqlite3.Row
        self.cx.executescript(SCHEMA)
        # Colonnes ajoutées après coup : la base de GitHub existe déjà.
        colonnes = {r["name"] for r in self.cx.execute("PRAGMA table_info(avis)")}
        if "part_qualitatif" not in colonnes:
            self.cx.execute("ALTER TABLE avis ADD COLUMN part_qualitatif REAL")
            self.cx.commit()

    def enregistrer(self, avis: Avis, res: Resultat, part_qualitatif: float | None = None) -> None:
        maintenant = datetime.now().isoformat(timespec="seconds")
        self.cx.execute(
            """
            INSERT INTO avis (idweb, objet, acheteur, nature, famille, dateparution, datelimite,
                              cpv, score, decision, statut_date, raisons, url,
                              premiere_analyse, derniere_analyse, part_qualitatif)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idweb) DO UPDATE SET
                objet=excluded.objet, acheteur=excluded.acheteur, nature=excluded.nature,
                famille=excluded.famille, dateparution=excluded.dateparution,
                datelimite=excluded.datelimite, cpv=excluded.cpv, score=excluded.score,
                decision=excluded.decision, statut_date=excluded.statut_date,
                raisons=excluded.raisons, url=excluded.url,
                derniere_analyse=excluded.derniere_analyse,
                part_qualitatif=excluded.part_qualitatif
            """,
            (
                avis.idweb, avis.objet, avis.acheteur, avis.nature, avis.famille,
                avis.dateparution.isoformat() if avis.dateparution else None,
                avis.datelimite.isoformat() if avis.datelimite else None,
                ",".join(avis.cpv), res.score, res.decision, res.statut_date,
                json.dumps(res.raisons, ensure_ascii=False), avis.url, maintenant, maintenant, part_qualitatif,
            ),
        )
        self.cx.commit()

    def deja_envoyes(self, idwebs: list[str]) -> set[str]:
        if not idwebs:
            return set()
        marques = ",".join("?" * len(idwebs))
        rows = self.cx.execute(
            f"SELECT idweb FROM avis WHERE envoye_le IS NOT NULL AND idweb IN ({marques})", idwebs
        )
        return {r["idweb"] for r in rows}

    def marquer_envoyes(self, idwebs: list[str]) -> None:
        maintenant = datetime.now().isoformat(timespec="seconds")
        self.cx.executemany(
            "UPDATE avis SET envoye_le = ? WHERE idweb = ? AND envoye_le IS NULL",
            [(maintenant, i) for i in idwebs],
        )
        self.cx.commit()

    def noter_envoi(self, type_: str, nb: int, sujet: str) -> None:
        self.cx.execute(
            "INSERT INTO envois (envoye_le, type, nb_avis, sujet) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), type_, nb, sujet),
        )
        self.cx.commit()

    def dernier_envoi_depuis(self, jours: int, aujourdhui: date) -> bool:
        """Vrai si un e-mail de veille quotidienne (alerte ou « rien ») est parti dans les `jours` derniers jours."""
        limite = (aujourdhui - timedelta(days=jours - 1)).isoformat()
        row = self.cx.execute(
            "SELECT 1 FROM envois WHERE envoye_le >= ? AND type IN ('alerte', 'rien') LIMIT 1", (limite,)
        ).fetchone()
        return row is not None

    def premiers_signalements(self, cles: list[str]) -> dict[str, str]:
        """Date du premier envoi de chaque acheteur déjà signalé (flux 2)."""
        if not cles:
            return {}
        marques = ",".join("?" * len(cles))
        rows = self.cx.execute(
            f"SELECT cle, premier_signalement FROM acheteurs_signales WHERE cle IN ({marques})", cles
        )
        return {r["cle"]: r["premier_signalement"] for r in rows}

    def noter_signalements(self, acheteurs: list[tuple[str, str]], aujourdhui: date) -> None:
        jour = aujourdhui.isoformat()
        self.cx.executemany(
            """
            INSERT INTO acheteurs_signales (cle, nom, premier_signalement, dernier_signalement, nb_signalements)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(cle) DO UPDATE SET nom=excluded.nom,
                dernier_signalement=excluded.dernier_signalement,
                nb_signalements=nb_signalements + 1
            """,
            [(cle, nom, jour, jour) for cle, nom in acheteurs],
        )
        self.cx.commit()

    def envoi_de_type_depuis(self, type_: str, depuis: date) -> bool:
        row = self.cx.execute(
            "SELECT 1 FROM envois WHERE type = ? AND envoye_le >= ? LIMIT 1", (type_, depuis.isoformat())
        ).fetchone()
        return row is not None
