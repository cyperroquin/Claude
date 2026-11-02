"""Tests hors ligne de la vérification des standards (Annuaire de l'administration)."""

from veille_boamp.annuaire import Annuaire, numeros_du_texte
from veille_boamp.prospects import Acheteur, nature_telephone


class Reponse:
    def __init__(self, donnees):
        self.donnees = donnees

    def raise_for_status(self):
        pass

    def json(self):
        return self.donnees


class Session:
    def __init__(self, fiches):
        self.fiches, self.headers, self.appels = fiches, {}, []

    def get(self, url, params, timeout):
        self.appels.append(params)
        return Reponse({"results": self.fiches})


FICHES = [
    {"nom": "Région Grand Est - Hôtel de Région", "siret": "20005226400013",
     "telephone": '[{"valeur": "03 87 33 60 00", "description": "Standard"}]'},
    {"nom": "Région Grand Est - Maison de région de Troyes", "siret": "20005226400099",
     "telephone": '[{"valeur": "+33 3 26 70 31 31"}]'},
]


def test_numeros_lus_dans_l_annuaire():
    assert numeros_du_texte('[{"valeur": "03 87 33 60 00"}, {"valeur": "+33 3 26 70 31 31"}]') == \
        {"0387336000", "0326703131"}


def test_standard_de_l_etablissement_exact():
    s = Session(FICHES)
    assert Annuaire({}, session=s).standards("20005226400013", "Région Grand Est") == {"0387336000"}
    assert s.appels[0]["where"] == 'startswith(siret, "200052264")'
    # Pas de SIRET : pas de requête.
    assert Annuaire({}, session=s).standards("", "SPL Grand Ouest") is None and len(s.appels) == 1


def test_annuaire_muet_ou_en_panne():
    class Panne(Session):
        def get(self, url, params, timeout):
            import requests
            raise requests.ConnectionError("coupure")
    assert Annuaire({}, session=Panne([])).standards("20005226400013", "Région Grand Est") is None
    assert Annuaire({}, session=Session([])).standards("20005226400013", "Région Grand Est") is None


def test_standard_verifie_dans_le_classement():
    officiels = {"0387336000"}
    # Même avec un nom de personne à côté, le numéro officiel d'accueil est le standard.
    assert nature_telephone("03 87 33 60 00", {("Mme Durand", "")}, "", officiels) == ("standard_verifie", "")
    assert nature_telephone("03 87 61 66 10", {("Mme Durand", "")}, "", officiels) == ("contact", "Mme Durand")
    # Un acheteur avec le standard officiel et une ligne directe : la ligne directe est affichée.
    a = Acheteur(cle="r", nom="Région Grand Est", standards_officiels=officiels)
    a.telephones.update({"03 87 33 60 00": 5, "03 87 61 66 10": 1})
    a.contacts_tel = {"03 87 33 60 00": {("", "")}, "03 87 61 66 10": {("Service des achats travaux", "")}}
    assert (a.telephone, a.nature_telephone) == ("03 87 61 66 10", "service")
    a.telephones = a.telephones.__class__({"03 87 33 60 00": 5})
    assert (a.telephone, a.nature_telephone) == ("03 87 33 60 00", "standard_verifie")
