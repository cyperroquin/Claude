"""Veille BOAMP pour BulQ.

    python -m veille_boamp                 passage quotidien (envoi de l'e-mail)
    python -m veille_boamp --dry-run       affiche l'e-mail sans l'envoyer
    python -m veille_boamp --backfill 90   rejoue 90 jours et compte les avis par critère
    python -m veille_boamp --avis 26-26177 note un ou plusieurs avis précis
    python -m veille_boamp --champs        affiche les champs réels de l'API
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .api import ClientBoamp
from .db import Journal
from .extraction import CHAMPS_ATTENDUS, lire_avis
from .mail import Ligne, construire, construire_rien, envoyer
from .scoring import A_EXAMINER, A_VERIFIER, JOURNAL, Moteur

RACINE = Path(__file__).resolve().parent.parent
log = logging.getLogger("veille_boamp")


def charger_env(chemin: Path) -> None:
    """Lecture minimale d'un fichier .env (CLE=valeur), sans écraser l'environnement."""
    if not chemin.exists():
        return
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        valeur = valeur.strip()
        if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "'\"":
            valeur = valeur[1:-1]
        os.environ.setdefault(cle.strip(), valeur)


def afficher_champs(client: ClientBoamp) -> None:
    rec = client.champs()
    print(f"Champs renvoyés par l'API ({len(rec)}) :")
    for k, v in rec.items():
        apercu = repr(v)
        print(f"  {k:32} {apercu[:90]}{'…' if len(apercu) > 90 else ''}")
    manquants = [c for c in CHAMPS_ATTENDUS if c not in rec]
    if manquants:
        print(f"\nATTENTION : champs attendus absents : {', '.join(manquants)}")
        print("Adapter veille_boamp/extraction.py avant de lancer la veille.")
    else:
        print("\nTous les champs attendus sont présents.")


def libelle_nature(config: dict, nature: str) -> str:
    return ((config.get("api") or {}).get("natures") or {}).get(nature, nature)


def passage_quotidien(config: dict, client: ClientBoamp, journal: Journal, moteur: Moteur,
                      aujourdhui: date, dry_run: bool) -> int:
    heures = int(config["api"].get("fenetre_heures", 48))
    natures = set(config["api"]["natures"])
    lignes: list[Ligne] = []
    nb = 0
    for rec in client.recents(aujourdhui, heures):
        avis = lire_avis(rec)
        if avis.nature not in natures:
            continue
        nb += 1
        res = moteur.evaluer(avis, aujourdhui)
        journal.enregistrer(avis, res)
        if res.a_envoyer:
            lignes.append(Ligne(avis, res, libelle_nature(config, avis.nature)))

    deja = journal.deja_envoyes([l.avis.idweb for l in lignes])
    lignes = [l for l in lignes if l.avis.idweb not in deja]
    log.info("%s avis analysés, %s à envoyer (%s déjà envoyés écartés)", nb, len(lignes), len(deja))

    if lignes:
        courriel = construire(lignes, aujourdhui, config)
        type_envoi = "alerte"
    else:
        jours = int((config.get("email") or {}).get("rien_tous_les_jours", 7))
        if journal.dernier_envoi_depuis(jours, aujourdhui):
            log.info("Rien à envoyer, et un e-mail est parti il y a moins de %s jours : pas d'envoi.", jours)
            return 0
        courriel = construire_rien(aujourdhui, config, nb)
        type_envoi = "rien"

    if dry_run:
        apercu = RACINE / "apercu_email.html"
        apercu.write_text(courriel.html, encoding="utf-8")
        print(f"Objet : {courriel.sujet}\n")
        print(courriel.texte)
        print(f"(--dry-run : rien n'a été envoyé ; version HTML dans {apercu})")
        return 0

    envoyer(courriel)
    journal.marquer_envoyes([l.avis.idweb for l in lignes])
    journal.noter_envoi(type_envoi, len(lignes), courriel.sujet)
    log.info("E-mail envoyé : %s", courriel.sujet)
    return 0


def backfill(config: dict, client: ClientBoamp, journal: Journal, moteur: Moteur,
             aujourdhui: date, jours: int) -> int:
    debut = aujourdhui - timedelta(days=jours)
    natures = set(config["api"]["natures"])
    compte: Counter = Counter()
    par_nature: Counter = Counter()
    decisions: Counter = Counter()
    retenus = []
    total = 0
    jour_courant = None
    for rec in client.periode(debut, aujourdhui):
        avis = lire_avis(rec)
        if avis.nature not in natures:
            continue
        if avis.dateparution != jour_courant:
            jour_courant = avis.dateparution
            print(f"  … {jour_courant} ({total} avis analysés)", file=sys.stderr, flush=True)
        total += 1
        res = moteur.evaluer(avis, aujourdhui)
        journal.enregistrer(avis, res)
        par_nature[avis.nature] += 1
        decisions[res.decision] += 1
        compte.update(set(res.criteres))
        if res.decision != JOURNAL:
            retenus.append((avis, res))

    print(f"\nRejeu du {debut:%d/%m/%Y} au {aujourdhui:%d/%m/%Y} : {total} avis analysés")
    for n, c in par_nature.most_common():
        print(f"  {libelle_nature(config, n):40} {c:6}")

    print("\nNombre d'avis par critère :")
    for r in config.get("regles") or []:
        print(f"  {r['id']:28} {r.get('points', 0):+3d}  {compte[r['id']]:6}   {r.get('libelle', '')}")
    print("\nNombre d'avis par mot d'exclusion :")
    for e in (config.get("exclusions") or {}).get("mots", []):
        mot = e["mot"] if isinstance(e, dict) else e
        print(f"  {mot:32} {compte['exclusion:' + mot]:6}")

    print("\nDécision :")
    for d in (A_EXAMINER, A_VERIFIER, JOURNAL):
        print(f"  {d:12} {decisions[d]:6}")

    retenus.sort(key=lambda x: (-x[1].score, x[0].idweb))
    print(f"\nAvis « {A_EXAMINER} » et « {A_VERIFIER} » ({len(retenus)}) :")
    for avis, res in retenus:
        print(f"  [{res.score:2}] {avis.idweb:10} {avis.dateparution} {avis.acheteur[:35]:35} {avis.objet[:90]}")
    return 0


def noter_avis(config: dict, client: ClientBoamp, moteur: Moteur, aujourdhui: date, ids: list[str]) -> int:
    code = 0
    for idweb in ids:
        rec = client.par_idweb(idweb)
        if not rec:
            print(f"{idweb} : introuvable")
            code = 1
            continue
        avis = lire_avis(rec)
        res = moteur.evaluer(avis, aujourdhui)
        print(f"{avis.idweb} · {avis.acheteur}\n  {avis.objet}")
        print(f"  {libelle_nature(config, avis.nature)} · parution {avis.dateparution} · "
              f"date limite {avis.datelimite or 'inconnue'} · CPV {', '.join(avis.cpv) or 'aucun'}")
        print(f"  Score {res.score} → {res.decision}")
        for r in res.raisons:
            print(f"    {r}")
        print()
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="veille_boamp", description="Veille BOAMP pour BulQ")
    p.add_argument("--dry-run", action="store_true", help="affiche l'e-mail sans l'envoyer")
    p.add_argument("--backfill", type=int, metavar="JOURS", help="rejoue les N derniers jours")
    p.add_argument("--avis", nargs="+", metavar="IDWEB", help="note un ou plusieurs avis précis")
    p.add_argument("--champs", action="store_true", help="affiche les champs réels de l'API")
    p.add_argument("--config", default=str(RACINE / "config.yaml"))
    p.add_argument("--db", default=str(RACINE / "veille.db"))
    p.add_argument("--env", default=str(RACINE / ".env"))
    p.add_argument("--date", help="date du jour à simuler (AAAA-MM-JJ)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    charger_env(Path(args.env))
    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    fuseau = ZoneInfo((config.get("dates") or {}).get("fuseau", "Europe/Paris"))
    aujourdhui = date.fromisoformat(args.date) if args.date else datetime.now(fuseau).date()
    client = ClientBoamp(config)
    moteur = Moteur(config)

    if args.champs:
        afficher_champs(client)
        return 0
    if args.avis:
        return noter_avis(config, client, moteur, aujourdhui, args.avis)

    premier_lancement = not Path(args.db).exists()
    journal = Journal(args.db)
    if premier_lancement:
        print("Premier lancement : vérification des champs de l'API.\n")
        afficher_champs(client)
        print()

    if args.backfill:
        return backfill(config, client, journal, moteur, aujourdhui, args.backfill)
    return passage_quotidien(config, client, journal, moteur, aujourdhui, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
