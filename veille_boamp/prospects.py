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
    contacts: list = field(default_factory=list)  # (téléphone, nom, fonction) de l'avis


@dataclass
class Acheteur:
    cle: str
    nom: str
    avis: list[AvisTravaux] = field(default_factory=list)
    siret: str = ""
    emails: Counter = field(default_factory=Counter)
    telephones: Counter = field(default_factory=Counter)
    # Pour chaque numéro : les contacts nommés à côté dans les avis (nom, fonction).
    contacts_tel: dict = field(default_factory=dict)
    # Pour chaque numéro : les avis où il figure (idweb, lien), du plus récent au plus ancien.
    sources_tel: dict = field(default_factory=dict)
    # Numéros d'accueil officiels (Annuaire de l'administration), None si inconnus.
    standards_officiels: set | None = None
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
    def _tel_choisi(self) -> tuple[str, str, str]:
        """(numéro, nature, libellé) du numéro le plus utile pour prospecter :
        la ligne d'un contact nommé, sinon celle d'un service, sinon le standard."""
        if not self.telephones:
            return "", "", ""
        ordre = {"contact": 0, "service": 1, "standard": 2, "standard_verifie": 3}
        choix = []
        for num, nb in self.telephones.items():
            nature, libelle = nature_telephone(num, self.contacts_tel.get(num, set()), self.nom,
                                               self.standards_officiels)
            choix.append((ordre[nature], -nb, num, nature, libelle))
        _, _, num, nature, libelle = min(choix)
        return num, nature, libelle

    @property
    def telephone(self) -> str:
        return self._tel_choisi[0]

    @property
    def nature_telephone(self) -> str:
        """« contact », « service », « standard » (probable) ou « standard_verifie »
        (vide si pas de numéro)."""
        return self._tel_choisi[1]

    @property
    def source_telephone(self) -> tuple[str, str]:
        """(idweb, lien) de l'avis le plus récent où figure le numéro affiché."""
        sources = self.sources_tel.get(self.telephone) or []
        return max(sources, key=lambda x: int(re.sub(r"\D", "", x[0]) or 0)) if sources else ("", "")

    @property
    def libelle_telephone(self) -> str:
        """Nom du contact ou du service joint par ce numéro (vide pour un standard)."""
        return self._tel_choisi[2]

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
    if chiffres.startswith("33") and (len(chiffres) >= 11 or (brut or "").strip().startswith("+")):
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


def _dicts(obj):
    """Tous les dictionnaires de l'arbre JSON (pour lire un numéro avec ses voisins)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _dicts(v)


def _contacts_telephoniques(donnees) -> list[tuple[str, str, str]]:
    """(numéro, nom du contact, fonction) de l'acheteur dans un avis.

    - formulaire national : `telContact`, à côté de `nomContact` ;
    - MAPA : `coord.tel` de l'organisme, à côté du `correspondantPRM` ;
    - eForms : le contact de l'organisation acheteuse (pas la plateforme ni le
      tribunal), avec `cbc:Name` et `cbc:JobTitle`.
    """
    out = []
    for d in _dicts(donnees):
        if _texte(d.get("telContact")):
            nom, fonction = _separer_fonction(_texte(d.get("nomContact")))
            out.append((_telephone(_texte(d["telContact"])), nom, fonction))
    if out:
        return out
    for d in _dicts(donnees):
        coord = d.get("coord")
        if isinstance(coord, dict) and _texte(coord.get("tel")):
            prm = d.get("correspondantPRM")
            nom = fonction = ""
            if isinstance(prm, dict):
                nom = " ".join(_texte(prm.get(c)) for c in ("civilite", "pren", "nom") if _texte(prm.get(c)))
                if nom in ("M", "Mme", "Mlle"):
                    nom = ""
                fonction = _texte(prm.get("fonc"))
            out.append((_telephone(_texte(coord["tel"])), nom, fonction))
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
                    nom, fonction = _separer_fonction(_texte(contact.get("cbc:Name")))
                    fonction = _texte(contact.get("cbc:JobTitle")) or fonction
                    out.append((_telephone(_texte(contact["cbc:Telephone"])), nom, fonction))
    return out


def _telephones(donnees) -> list[str]:
    """Téléphones de l'acheteur dans un avis (sans le nom du contact)."""
    return [num for num, _, _ in _contacts_telephoniques(donnees)]


def _separer_fonction(texte: str) -> tuple[str, str]:
    """« Karen PERRIN - Secrétaire Générale » → (« Karen PERRIN », « Secrétaire Générale »)."""
    morceaux = re.split(r"\s+[-–—,/]\s+|\s*,\s*", texte, maxsplit=1)
    nom = morceaux[0].strip()
    return nom, (morceaux[1].strip() if len(morceaux) > 1 else "")


# Nature d'un numéro de téléphone (prospection) --------------------------------
_RE_CIVILITE = re.compile(r"^(m|mr|mme|mlle|madame|monsieur|mademoiselle)\b")
# Mots qui désignent un service et non une personne.
_RE_SERVICE = re.compile(
    r"\b(service|services|direction|dir|departement|pole|cellule|bureau|mission|division|unite|"
    r"commande|marche|marches|achat|achats|juridique|secretariat|cabinet|agence|centre|site|siege|"
    r"accueil|standard|mairie|hotel|ville|commune|conseil|region|departemental|office|syndicat|"
    r"communaute|metropole|habitat|logement|sa|sem|spl|opac|oph|dst|dga|dgs|dgst|equipe|groupe|"
    r"administration|administratif|technique|techniques|patrimoine|travaux|batiment|batiments|"
    r"moyens|generaux|finances|affaires|contrats|consultation|consultations|contact|info|infos)\b"
)
# Services qui répondent au nom de tout l'organisme : c'est le standard.
_RE_STANDARD = re.compile(r"\b(accueil|standard|mairie|hotel de ville|siege|secretariat general|"
                          r"secretariat de mairie|reception)\b")
# Élus : leur numéro dans l'avis est celui de la mairie ou de la collectivité.
_RE_ELU = re.compile(r"\b(maire|president|presidente|vice president|vice presidente|adjoint|adjointe)\b")


def _est_une_personne(nom: str) -> bool:
    n = normaliser(nom)
    if not n or "@" in nom or _RE_ELU.search(n):  # « Madame la Présidente » n'est pas un contact
        return False
    if _RE_CIVILITE.match(n):
        return True
    mots = n.split()
    if _RE_SERVICE.search(n) or any(c.isdigit() for c in n) or all(len(m) <= 3 for m in mots):
        return False  # « Dpb-Maj » : un sigle, pas une personne
    # Un nom de famille seul (« CHANSIGAUD ») ou prénom + nom.
    return 1 <= len(mots) <= 4 and (len(mots) > 1 or len(mots[0]) >= 4)


def _mots_du_nom(nom: str) -> frozenset:
    return frozenset(m for m in normaliser(nom).split()
                     if m not in ("m", "mr", "mme", "mlle", "madame", "monsieur", "mademoiselle"))


def _libelle(nom: str, fonction: str) -> str:
    return f"{nom} ({fonction})" if nom and fonction else nom or fonction


_RE_MOBILE = re.compile(r"^0(6|7)\d{8}$")
_RE_ACHATS = re.compile(r"\b(marche|marches|achat|achats|commande publique|consultation|consultations|contrats)\b")


def _numero_d_accueil(chiffres: str) -> bool:
    """Forme typique d'un standard : se termine par 00, ou par deux paires identiques
    (… 20 20, … 48 48, … 80 80)."""
    return chiffres.endswith("00") or (len(chiffres) == 10 and chiffres[6:8] == chiffres[8:10])


def nature_telephone(numero: str, contacts, nom_acheteur: str = "",
                     standards_officiels: set | None = None) -> tuple[str, str]:
    """Classe un numéro pour la prospection : (« contact » | « service » | « standard »
    | « standard_verifie », libellé).

    - « standard_verifie » : le numéro est le numéro d'accueil officiel de
      l'organisme dans l'Annuaire de l'administration (service-public.fr) ;

    - « contact » : numéro de portable, ou numéro donné avec le nom d'une personne ;
    - « service » : avec le nom d'un service (service des marchés, direction…) ou
      une adresse e-mail de service ;
    - « standard » : forme typique d'un numéro d'accueil (… 00, … 20 20), sans
      contact nommé, avec « accueil », « mairie » ou le seul nom de l'organisme,
      au nom d'un élu (maire, président), ou partagé par plusieurs personnes.
    C'est une estimation à partir de l'avis : le BOAMP ne dit pas si c'est un standard.
    """
    chiffres = re.sub(r"\D", "", numero)
    if standards_officiels and chiffres in standards_officiels:
        return "standard_verifie", ""
    contacts = [(n, f) for n, f in contacts if n or f]
    acheteur = normaliser(nom_acheteur)
    # Un élu (maire, président) peut apparaître dans un autre avis sans sa fonction :
    # on le reconnaît à ses mots de nom (« Mme Catherine Trautmann » = « TRAUTMANN Catherine »).
    elus = {_mots_du_nom(n) for n, f in contacts if _RE_ELU.search(normaliser(f))}
    personnes = {normaliser(n): (n, f) for n, f in contacts
                 if "@" not in n and _est_une_personne(n) and not _RE_ELU.search(normaliser(f))
                 and _mots_du_nom(n) not in elus}
    if _RE_MOBILE.match(chiffres):
        return "contact", (_libelle(*sorted(personnes.values())[0]) if len(personnes) == 1 else "portable")
    if _numero_d_accueil(chiffres) or len(personnes) >= 2:
        return "standard", ""
    if personnes:
        return "contact", _libelle(*sorted(personnes.values())[0])

    def est_service(n: str, f: str) -> bool:
        nn = normaliser(n)
        return bool(n) and not _RE_STANDARD.search(nn) and not _RE_ELU.search(normaliser(f)) \
            and _mots_du_nom(n) not in elus \
            and not (acheteur and (nn == acheteur or nn in acheteur))

    services = [(n, f) for n, f in contacts if est_service(n, f)]
    if services:
        # Le service des marchés ou des achats d'abord.
        services.sort(key=lambda c: (not _RE_ACHATS.search(normaliser(c[0])), c))
        return "service", _libelle(*services[0])
    return "standard", ""


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
                contacts=_contacts_telephoniques(donnees),
            ))
            a.siret = a.siret or _siret(donnees)
            a.emails.update(_emails(donnees, self.plateformes))
            for num, nom, fonction in _contacts_telephoniques(donnees):
                a.telephones[num] += 1
                a.contacts_tel.setdefault(num, set()).add((nom, fonction))
                if (avis.idweb, avis.url) not in a.sources_tel.setdefault(num, []):
                    a.sources_tel[num].append((avis.idweb, avis.url))
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


# Liste d'appels ---------------------------------------------------------------
NATURE_NUMERO = {
    "contact": "ligne directe",
    "service": "ligne du service",
    "standard_verifie": "standard (vérifié)",
    "standard": "standard (probable)",
}


SANS_NOM = "aucun nom dans les avis"


@dataclass
class Appel:
    """Un appel à passer : un numéro, la personne (ou le service) à demander,
    et la consultation dont parler, tous lus dans le même avis."""
    acheteur: Acheteur
    rang: int                 # rang de l'acheteur dans l'e-mail
    numero: str
    nature: str               # clé de NATURE_NUMERO
    demander: str             # nom de la personne, ou du service
    fonction: str
    personne: bool
    avis: AvisTravaux

    @property
    def nature_libelle(self) -> str:
        return NATURE_NUMERO.get(self.nature, "")

    @property
    def objet_court(self) -> str:
        o = " ".join(self.avis.objet.split())
        return o if len(o) <= 80 else o[:79].rstrip() + "…"

    @property
    def statut(self) -> str:
        j, d = self.avis.jours_restants, self.avis.datelimite
        if j is None:
            return "date limite non indiquée"
        if j < 0:
            return f"close le {d:%d/%m} : analyse des offres en cours"
        return f"offres attendues le {d:%d/%m} (dans {j} j)"


def liste_appels(acheteurs: list[Acheteur], max_par_acheteur: int = 3,
                 jours_clos_max: int = 60) -> list[Appel]:
    """Pour chaque acheteur (dans l'ordre de l'e-mail), les contacts nommés de ses
    avis : une personne (sinon un service), avec son numéro et la consultation.

    - pas d'élu (maire, président), pas de contact sans nom ;
    - une même personne une seule fois, avec son avis le plus récent ;
    - consultations closes depuis plus de `jours_clos_max` jours écartées ;
    - personnes avant services, puis consultations récemment closes (l'acheteur
      compare les offres), puis ouvertes, puis du plus récent au plus ancien.
    """
    appels = []
    for rang, a in enumerate(acheteurs, 1):
        tous = [(n, f) for x in a.avis for _, n, f in x.contacts]
        elus = {_mots_du_nom(n) for n, f in tous if _RE_ELU.search(normaliser(f))}
        nom_acheteur = normaliser(a.nom)
        retenus: dict = {}
        for x in a.avis:
            if x.jours_restants is not None and x.jours_restants < -jours_clos_max:
                continue
            for num, nom, fonction in x.contacts:
                nn = normaliser(nom)
                if (not nn or "@" in nom or _RE_ELU.search(nn) or _RE_ELU.search(normaliser(fonction))
                        or _mots_du_nom(nom) in elus):
                    continue
                personne = _est_une_personne(nom)
                if not personne and (_RE_STANDARD.search(nn) or nn == nom_acheteur or nn in nom_acheteur):
                    continue
                # Une personne une fois ; un service une fois par numéro (graphies variables).
                cle = ("p", _mots_du_nom(nom)) if personne else ("s", num)
                garde = retenus.get(cle)
                if garde and (garde[0].dateparution or date.min) >= (x.dateparution or date.min):
                    continue
                retenus[cle] = (x, num, nom, fonction, personne)

        def ordre(v):
            x, _, _, _, personne = v
            j = x.jours_restants
            etat = 0 if (j is not None and j < 0) else 1 if j is not None else 2
            return (not personne, etat, -(x.dateparution or date.min).toordinal())

        for x, num, nom, fonction, personne in sorted(retenus.values(), key=ordre)[:max_par_acheteur]:
            nature, _ = nature_telephone(num, a.contacts_tel.get(num, set()), a.nom, a.standards_officiels)
            appels.append(Appel(a, rang, num, nature, nom, fonction, personne, x))
        if not retenus and a.telephone:
            # Aucun nom dans les avis : le meilleur numéro, et le service des marchés à demander.
            idweb = a.source_telephone[0]
            x = next((v for v in a.avis if v.idweb == idweb), None)
            if x:
                appels.append(Appel(a, rang, a.telephone, a.nature_telephone,
                                    "service des marchés publics", SANS_NOM, False, x))
    return appels
