"""Tests du correctif « notation des offres » (flux 1 et flux 2)."""

import copy
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml

from veille_boamp.criteres import part_qualitatif
from veille_boamp.db import Journal
from veille_boamp.extraction import lire_avis
from veille_boamp.mail import construire_prospects
from veille_boamp.prospects import Prospection
from veille_boamp.scoring import Moteur

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
AUJOURDHUI = date(2026, 10, 20)


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))


def charger(nom):
    return json.loads((FIXTURES / f"{nom}.json").read_text(encoding="utf-8"))


# Test 3 : avis d'attribution 26-82569, critères eForms 30 + 20 + 10 pour 40 de prix.
def test_part_qualitatif_eforms():
    assert part_qualitatif(charger("attribution_gpseo")) == 60


@pytest.mark.parametrize("texte,attendu", [
    ("Prix : 40% - Valeur technique : 30% - Délai : 20% - Environnement : 10%", 60),
    ("Valeur technique 60 % dont : a) 30 % b) 20 % c) 10 % ; Prix 40 %", 60),
    ("le prix est noté sur 40 points et la valeur technique est notée sur 60 points", 60),
    ("1. Prix des prestations (60%) 2. Valeur technique (40%)", 40),
])
def test_part_qualitatif_texte_libre(texte, attendu):
    assert part_qualitatif({"donnees": json.dumps({"criteresAttrib": texte})}) == attendu


# Test 4 : critères absents ou renvoyés au règlement → valeur nulle, sans erreur.
@pytest.mark.parametrize("donnees", ['{"criteresAttrib": "énoncé dans le Rc"}', "{}", "", None, "pas du json"])
def test_criteres_illisibles(donnees):
    assert part_qualitatif({"donnees": donnees}) is None


def test_mapa_et_formulaire_national():
    travaux = {a["idweb"]: a for a in charger("travaux")}
    assert part_qualitatif(travaux["26-100123"]) == 60  # MAPA structuré
    assert part_qualitatif(travaux["26-99541"]) is None  # « Ce référer au RC »
    assert part_qualitatif(charger("oppic")) == 60  # texte libre


# Test 1 : les trois consultations de référence du flux 1 restent à 7 ou plus.
@pytest.mark.parametrize("nom", ["annecy", "cinor", "oppic"])
def test_references_flux1(config, nom):
    assert Moteur(config).evaluer(lire_avis(charger(nom))).score >= 7


def test_nouveaux_mots_cles_flux1(config):
    a = lire_avis(charger("annecy"))
    a.cpv = []
    a.objet = "Acquisition d'un outil de notation des candidatures"
    a.description = ""
    assert "mots_groupe_c" in Moteur(config).evaluer(a).criteres
    a.objet = "Logiciel de production du rapport d'analyse des offres et des courriers de rejet"
    assert "mots_groupe_a_b" in Moteur(config).evaluer(a).criteres


# Test 5 : les nouvelles raisons apparaissent dans l'e-mail du flux 2.
def test_bonus_flux2_et_courriel(config):
    avis = copy.deepcopy(charger("travaux"))
    arac = [a for a in avis if "arac" in a["nomacheteur"].lower()]
    donnees = json.loads(arac[0]["donnees"])
    donnees["criteres"] = {"criterePondere": [{"criterePCT": "40", "critere": "Prix"},
                                              {"criterePCT": "60", "critere": "Valeur technique"}]}
    arac[0]["donnees"] = json.dumps(donnees, ensure_ascii=False)
    arac[1]["objet"] += " - accord-cadre à marchés subséquents"
    p = Prospection(config)
    acheteur = p.noter(p.agreger(avis, AUJOURDHUI), AUJOURDHUI)[0]
    assert "+3 : qualitatif 60 %" in acheteur.raisons
    assert "+2 : accord-cadre avec remise en concurrence" in acheteur.raisons
    assert "+2 : échéance dans 24 jours" in acheteur.raisons
    assert acheteur.score == 22  # 16,75 + 3 + 2, arrondi

    c = construire_prospects([acheteur], AUJOURDHUI, config, len(avis))
    for texte in ("qualitatif 60 %", "accord-cadre avec remise en concurrence", "échéance dans 24 jours"):
        assert texte in c.html and texte in c.texte
    assert "date limite 13/11 : 24 j" in c.texte


def test_tri_a_score_egal_par_echeance(config):
    p = Prospection(config)
    acheteurs = p.agreger(charger("travaux"), AUJOURDHUI)
    arac = next(a for a in acheteurs if "arac" in a.cle)
    tot, tard = copy.deepcopy(arac), copy.deepcopy(arac)
    tot.nom, tard.nom = "B acheteur", "A acheteur"
    for x in tard.avis:
        x.jours_restants = (x.jours_restants or 0) + 3  # toujours dans la fenêtre 7-28
    classement = p.noter([tard, tot], AUJOURDHUI)
    assert classement[0].score == classement[1].score
    assert [a.nom for a in classement] == ["B acheteur", "A acheteur"]


def test_ajout_de_colonne_sur_base_existante(tmp_path):
    chemin = tmp_path / "veille.db"
    cx = sqlite3.connect(chemin)
    cx.execute("CREATE TABLE avis (idweb TEXT PRIMARY KEY, objet TEXT)")
    cx.commit()
    cx.close()
    j = Journal(chemin)
    colonnes = {r["name"] for r in j.cx.execute("PRAGMA table_info(avis)")}
    assert "part_qualitatif" in colonnes
