"""Tests hors ligne, sur des avis réels du BOAMP enregistrés dans tests/fixtures."""

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from veille_boamp.db import Journal
from veille_boamp.extraction import lire_avis, normaliser
from veille_boamp.mail import Ligne, construire
from veille_boamp.scoring import A_EXAMINER, DATE_OK, DATE_TROP_TARD, DATE_URGENT, JOURNAL, Moteur

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def moteur(config):
    return Moteur(config)


def avis(nom):
    return lire_avis(json.loads((FIXTURES / f"{nom}.json").read_text(encoding="utf-8")))


# Critère d'acceptation 2 : les trois consultations de référence ressortent à 7 ou plus.
@pytest.mark.parametrize("nom", ["annecy", "cinor", "oppic"])
def test_consultations_de_reference(moteur, nom):
    res = moteur.evaluer(avis(nom))
    assert res.score >= 7, res.raisons
    assert res.decision == A_EXAMINER


# Critère d'acceptation 3 : assurance, voyage scolaire et maintenance Sage restent hors de l'e-mail.
@pytest.mark.parametrize("nom", ["sage", "assurance", "voyage"])
def test_exclusions(moteur, nom):
    res = moteur.evaluer(avis(nom))
    assert res.decision == JOURNAL, res.raisons
    assert any("exclusion" in r for r in res.raisons)


def test_extraction_cpv_et_date(moteur):
    a = avis("annecy")
    assert a.cpv == ["48490000"]
    assert a.datelimite == date(2026, 4, 13)
    e = avis("joue_eforms")  # formulaire eForms sans datelimitereponse
    assert e.cpv == ["48921000"]
    assert e.datelimite == date(2026, 11, 4)


def test_normalisation():
    assert normaliser("Marchés PUBLICS d’Œuvre") == "marches publics d oeuvre"


def test_formules_juridiques_neutralisees(config):
    m = Moteur(config)
    a = avis("annecy")
    a.objet = "Prestations de gardiennage"
    a.description = "Marché passé en application du Code de la commande publique."
    a.cpv = []
    assert m.evaluer(a).score == 0


def test_proximite_groupes_a_b(config):
    m = Moteur(config)
    a = avis("annecy")
    a.cpv = []
    a.objet = "Logiciel de passation des contrats"
    a.description = ""
    assert "mots_groupe_a_b" in m.evaluer(a).criteres
    a.objet = "Logiciel " + "mot " * 20 + "passation"
    assert "mots_groupe_a_b" not in m.evaluer(a).criteres


def test_bibliotheque_de_prix_non_exclue(moteur):
    a = avis("annecy")
    a.description = "Logiciel avec bibliothèque de prix"
    assert not any("bibliotheque" in c for c in moteur.evaluer(a).criteres)
    a.description = "Logiciel de prêt de la bibliothèque municipale"
    assert "exclusion:bibliotheque" in moteur.evaluer(a).criteres


@pytest.mark.parametrize("jours,statut", [(10, DATE_OK), (7, DATE_OK), (6, DATE_URGENT),
                                          (3, DATE_URGENT), (2, DATE_TROP_TARD)])
def test_seuils_de_date(moteur, jours, statut):
    a = avis("annecy")
    aujourdhui = date.fromordinal(a.datelimite.toordinal() - jours)
    res = moteur.evaluer(a, aujourdhui)
    assert res.statut_date == statut
    assert res.jours_restants == jours
    assert res.a_envoyer == (statut != DATE_TROP_TARD)


# Critère d'acceptation 4 : un même avis n'est jamais envoyé deux fois.
def test_deduplication(tmp_path, moteur):
    j = Journal(tmp_path / "veille.db")
    a = avis("cinor")
    res = moteur.evaluer(a)
    j.enregistrer(a, res)
    assert j.deja_envoyes([a.idweb]) == set()
    j.marquer_envoyes([a.idweb])
    j.enregistrer(a, res)  # nouvelle analyse le lendemain
    assert j.deja_envoyes([a.idweb]) == {a.idweb}


def test_rien_une_fois_par_semaine(tmp_path):
    j = Journal(tmp_path / "veille.db")
    aujourdhui = date.today()
    assert not j.dernier_envoi_depuis(7, aujourdhui)
    j.noter_envoi("rien", 0, "test")
    assert j.dernier_envoi_depuis(7, aujourdhui)


def test_courriel(config, moteur):
    aujourdhui = date(2026, 3, 15)
    lignes = []
    for nom in ["annecy", "cinor", "oppic"]:
        a = avis(nom)
        lignes.append(Ligne(a, moteur.evaluer(a, aujourdhui), "Avis de marché"))
    c = construire(lignes, aujourdhui, config)
    assert c.sujet == "[Veille BOAMP] 3 consultations à examiner, 15/03/2026"
    assert "https://www.boamp.fr/avis/detail/26-26177" in c.html
    assert "Paul valide avant tout dépôt." in c.texte
    # CINOR (date limite le 20/03, 5 jours) est urgente : elle passe en tête.
    assert c.texte.index("CINOR") < c.texte.index("Annecy")
