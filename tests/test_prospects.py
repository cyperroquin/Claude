"""Tests hors ligne du flux 2 (acheteurs de travaux actifs)."""

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from veille_boamp.db import Journal
from veille_boamp.extraction import charger_json
from veille_boamp.mail import construire_prospects
from veille_boamp.prospects import Prospection, _emails, _nb_lots, _siret

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
AUJOURDHUI = date(2026, 10, 20)


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def avis():
    return json.loads((FIXTURES / "travaux.json").read_text(encoding="utf-8"))


def par_id(avis, idweb):
    return next(a for a in avis if a["idweb"] == idweb)


def test_lots_selon_le_formulaire(avis):
    assert _nb_lots(charger_json(par_id(avis, "26-99504")["donnees"])) == 19  # eForms
    assert _nb_lots(charger_json(par_id(avis, "26-100123")["donnees"])) == 7  # MAPA
    assert _nb_lots(charger_json(par_id(avis, "26-99586")["donnees"])) == 11  # national
    assert _nb_lots(charger_json(par_id(avis, "26-99541")["donnees"])) == 1


def test_siret_et_contact(avis, config):
    assert _siret(charger_json(par_id(avis, "26-100545")["donnees"])) == "53396945700035"
    # eForms : la plateforme de publication et le tribunal administratif sont écartés.
    plateformes = config["prospects"]["domaines_plateformes"]
    assert _emails(charger_json(par_id(avis, "26-99504")["donnees"]), plateformes) == ["contact@arac-occitanie.fr"]


def test_agregation_et_score(avis, config):
    p = Prospection(config)
    acheteurs = p.noter(p.agreger(avis, AUJOURDHUI), AUJOURDHUI)
    # Les deux graphies de l'ARAC sont regroupées ; les acheteurs à un seul avis sont écartés.
    assert [a.nom for a in acheteurs] == ["Spl Arac Occitanie"]
    arac = acheteurs[0]
    assert len(arac.avis) == 2
    assert arac.nb_lots == 23
    assert arac.type_acheteur == "Aménageur, SEM, SPL"
    assert arac.nb_recents == 2
    # 2 avis + 23 lots x 0,25 + 2 (actif cette semaine) + 5 (SPL)
    # + 2 (date limite du 13/11, dans 24 jours) = 16,75
    assert arac.score == 17
    assert "+2 : échéance dans 24 jours" in arac.raisons


def test_types_d_acheteur(config):
    p = Prospection(config)
    types = {nom: next((t for t, _, m in p.types if any(x.search(nom) for x in m)), "")
             for nom in ["rennes metropole habitat", "ville de lyon", "sid sud est"]}
    assert types == {"rennes metropole habitat": "Bailleur social",
                     "ville de lyon": "Collectivité", "sid sud est": ""}


def test_courriel_et_suivi(tmp_path, avis, config):
    p = Prospection(config)
    acheteurs = p.noter(p.agreger(avis, AUJOURDHUI), AUJOURDHUI)
    c = construire_prospects(acheteurs, AUJOURDHUI, config, len(avis))
    assert c.sujet == "[Prospects BOAMP] 1 acheteur de travaux actif, semaine du 19/10/2026"
    assert "Nouveau" in c.html and "https://www.boamp.fr/avis/detail/26-100545" in c.html

    j = Journal(tmp_path / "veille.db")
    assert not j.envoi_de_type_depuis("prospects", date(2026, 10, 19))
    j.noter_signalements([(a.cle, a.nom) for a in acheteurs], AUJOURDHUI)
    j.noter_envoi("prospects", 1, c.sujet)
    assert j.envoi_de_type_depuis("prospects", date(2026, 10, 19))
    assert j.premiers_signalements([acheteurs[0].cle]) == {acheteurs[0].cle: "2026-10-20"}
    # L'e-mail des prospects ne compte pas pour le message « rien aujourd'hui » de la veille quotidienne.
    assert not j.dernier_envoi_depuis(7, AUJOURDHUI)


def test_telephone_et_profil_acheteur(avis):
    from veille_boamp.prospects import _profil_acheteur, _telephone, _telephones
    # Formulaire national : `telContact`, numéro remis au format français.
    d = charger_json(par_id(avis, "26-99586")["donnees"])
    assert _telephones(d) == ["05 46 37 00 35"]
    assert _profil_acheteur(d) == "https://www.marches-securises.fr"
    # eForms : le numéro de l'acheteur, pas celui de la plateforme ni du tribunal.
    d = charger_json(par_id(avis, "26-99504")["donnees"])
    assert _telephones(d) == ["05 67 72 71 00"]
    assert _profil_acheteur(d) == "http://marches-publics.info"
    # Rien dans l'avis : champs vides, sans erreur.
    assert _telephones({}) == [] and _profil_acheteur({}) == ""
    assert _telephone("+33 442659054") == "04 42 65 90 54"


def test_colonnes_contact_dans_le_courriel(avis, config):
    p = Prospection(config)
    acheteurs = p.noter(p.agreger(avis, AUJOURDHUI), AUJOURDHUI)
    c = construire_prospects(acheteurs, AUJOURDHUI, config, len(avis))
    for entete in ("E-mail", "Téléphone", "Profil acheteur"):
        assert f">{entete}</th>" in c.html
    assert 'href="tel:0499524524"' in c.html
    assert 'href="http://marches-publics.info"' in c.html
    assert "Téléphone  : 04 99 52 45 24" in c.texte


def test_adresses_sans_reponse_ecartees():
    d = {"adresseMailContact": "nepasrepondre@grandest.fr", "cac:Contact": {"cbc:ElectronicMail": "marches@grandest.fr"}}
    assert _emails(d, []) == ["marches@grandest.fr"]
    assert _emails({"adresseMailContact": "no-reply@ville.fr"}, []) == []


def test_nature_du_telephone(avis):
    """Prospection : distinguer le standard d'un numéro de contact ou de service."""
    from veille_boamp.prospects import _contacts_telephoniques, nature_telephone

    def nature(idweb):
        num, nom, fonction = _contacts_telephoniques(charger_json(par_id(avis, idweb)["donnees"]))[0]
        return nature_telephone(num, {(nom, fonction)})

    # Numéro donné avec le nom d'une personne.
    assert nature("26-99541") == ("contact", "FEMENIAS Stéphane")
    # MAPA : le numéro de l'organisme, avec le correspondant et sa fonction.
    assert nature("26-100123") == ("contact", "M Damien PELLEN (Directeur des Achats, de la Logistique et de l'Hôtellerie)")
    # Numéro d'un service.
    assert nature("26-100545") == ("service", "Service Marchés")
    # Numéro terminé par 00 : accueil typique.
    assert nature("26-99504") == ("standard", "")
    # Sans contact, à l'accueil, au nom d'un élu, ou partagé par plusieurs personnes : standard.
    assert nature_telephone("04 77 12 34 56", set()) == ("standard", "")
    assert nature_telephone("04 77 12 34 56", {("Accueil de la mairie", "")}) == ("standard", "")
    assert nature_telephone("05 62 13 48 10", {("CORTIJO Denise", "Maire")}) == ("standard", "")
    assert nature_telephone("04 77 12 34 56", {("Mme Durand", ""), ("M. Martin", "")}) == ("standard", "")
    assert nature_telephone("04 77 71 24 60", {("Karen PERRIN", "Secrétaire Générale")}) == \
        ("contact", "Karen PERRIN (Secrétaire Générale)")


def test_telephone_le_plus_utile_dans_le_courriel(avis, config):
    p = Prospection(config)
    arac = p.noter(p.agreger(avis, AUJOURDHUI), AUJOURDHUI)[0]
    # Deux numéros pour l'ARAC : la ligne du service marchés passe avant le standard.
    assert set(arac.telephones) == {"04 99 52 45 24", "05 67 72 71 00"}
    assert (arac.telephone, arac.nature_telephone, arac.libelle_telephone) == \
        ("04 99 52 45 24", "service", "Service Marchés")
    c = construire_prospects([arac], AUJOURDHUI, config, len(avis))
    assert "Ligne du service" in c.html and "Numéro de standard" in c.html  # colonne et légende
    assert "Téléphone  : 04 99 52 45 24 — ligne du service : Service Marchés" in c.texte


def test_nature_du_telephone_cas_reels():
    """Cas relevés sur l'essai du 02/11/2026 (données réelles)."""
    from veille_boamp.prospects import nature_telephone
    # Deux paires identiques à la fin : standard, même avec un nom.
    assert nature_telephone("03 22 71 80 80", {("Pauline Lefevre", "")})[0] == "standard"
    assert nature_telephone("02 98 76 20 20", {("Département du Finistère", "")})[0] == "standard"
    # Seul le nom de l'organisme : standard.
    assert nature_telephone("03 87 61 66 10", {("Région Grand Est", "")}, "Région Grand Est")[0] == "standard"
    # Une adresse e-mail à la place du nom : ligne du service.
    assert nature_telephone("02 35 19 44 46", {("secretariatcao@lehavremetro.fr", "")}) == \
        ("service", "secretariatcao@lehavremetro.fr")
    # Portable : contact direct.
    assert nature_telephone("06 77 32 26 65", {("Alexandre Dechavanne", "")}) == ("contact", "Alexandre Dechavanne")
    assert nature_telephone("06 92 11 35 53", set()) == ("contact", "portable")
    # Plusieurs services : celui des marchés d'abord.
    assert nature_telephone("04 99 52 45 24", {("EST SITE", ""), ("Service Marchés", "")}) == \
        ("service", "Service Marchés")


def test_elu_reconnu_sans_sa_fonction_et_source_du_numero(avis, config):
    from veille_boamp.prospects import nature_telephone
    # Strasbourg (essai du 02/11) : la présidente apparaît une fois sans sa fonction.
    assert nature_telephone("03 68 98 51 19", {("Mme Catherine Trautmann", ""),
                                               ("TRAUTMANN Catherine", "Présidente")})[0] == "standard"
    p = Prospection(config)
    arac = p.noter(p.agreger(avis, AUJOURDHUI), AUJOURDHUI)[0]
    assert arac.source_telephone == ("26-100545", "https://www.boamp.fr/avis/detail/26-100545")
    c = construire_prospects([arac], AUJOURDHUI, config, len(avis))
    assert "Source : avis 26-100545" in c.html
    assert "source : https://www.boamp.fr/avis/detail/26-100545" in c.texte
