"""Construction et envoi de l'e-mail quotidien."""

from __future__ import annotations

import base64
import io
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .extraction import Avis
from .scoring import Resultat

DOSSIER_GABARITS = Path(__file__).parent / "templates"


@dataclass
class Ligne:
    avis: Avis
    res: Resultat
    libelle_nature: str


@dataclass
class Courriel:
    sujet: str
    texte: str
    html: str
    pieces_jointes: list = field(default_factory=list)  # (nom de fichier, contenu en octets)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(DOSSIER_GABARITS),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["jjmmaaaa"] = lambda d: d.strftime("%d/%m/%Y") if d else "non indiquée"
    env.filters["jjmm"] = lambda d: (date.fromisoformat(d) if isinstance(d, str) else d).strftime("%d/%m") if d else ""
    return env


def trier(lignes: list[Ligne]) -> list[Ligne]:
    """Urgents en tête, puis score décroissant, puis date limite la plus proche."""
    return sorted(
        lignes,
        key=lambda l: (not l.res.urgent, -l.res.score,
                       l.avis.datelimite or date.max, l.avis.idweb),
    )


def construire(lignes: list[Ligne], aujourdhui: date, config: dict) -> Courriel:
    lignes = trier(lignes)
    marches = [l for l in lignes if l.avis.nature != "PRE-INFORMATION"]
    signaux = [l for l in lignes if l.avis.nature == "PRE-INFORMATION"]
    n = len(lignes)
    sujet = (f"[Veille BOAMP] {n} consultation{'s' if n > 1 else ''} à examiner, "
             f"{aujourdhui.strftime('%d/%m/%Y')}")
    ctx = dict(
        sujet=sujet, aujourdhui=aujourdhui, marches=marches, signaux=signaux, n=n,
        checklist=config.get("checklist") or [],
        seuils=config.get("seuils") or {},
    )
    env = _env()
    return Courriel(sujet, env.get_template("email.txt.j2").render(**ctx),
                    env.get_template("email.html.j2").render(**ctx))


def construire_rien(aujourdhui: date, config: dict, nb_analyses: int) -> Courriel:
    sujet = f"[Veille BOAMP] Rien aujourd'hui, {aujourdhui.strftime('%d/%m/%Y')}"
    ctx = dict(sujet=sujet, aujourdhui=aujourdhui, nb_analyses=nb_analyses,
               jours=(config.get("email") or {}).get("rien_tous_les_jours", 7))
    env = _env()
    return Courriel(sujet, env.get_template("rien.txt.j2").render(**ctx),
                    env.get_template("rien.html.j2").render(**ctx))


COLONNES_APPELS = [
    "Rang", "Acheteur", "Téléphone", "Nature du numéro", "Demander", "Fonction", "Consultation",
    "Avis", "Lien de l'avis", "Statut", "E-mail de l'acheteur", "Profil acheteur",
    "Appelé le", "Résultat", "À rappeler le",
]
COLONNES_ACHETEURS = [
    "Rang", "Acheteur", "Type d'acheteur", "Nouveau ?", "Score", "Raisons du score",
    "Appels d'offres de travaux", "Lots", "Publiés cette semaine", "E-mail", "Téléphone",
    "Nature du numéro", "Contact du numéro", "Avis source du numéro", "Profil acheteur",
    "Fiche entreprise", "Dernières consultations",
]
MODE_D_EMPLOI = [
    ("Onglet « Appels »", "Une ligne par contact nommé dans un avis : le numéro, la personne (ou le service) "
     "à demander et la consultation dont parler. Même si le numéro est un standard, demandez la personne par "
     "son nom. Les colonnes « Appelé le », « Résultat » et « À rappeler le » servent à suivre vos appels."),
    ("Ligne directe", "Numéro donné dans l'avis avec le nom d'une personne, ou portable."),
    ("Ligne du service", "Numéro du service des marchés ou de la commande publique : demandez le responsable "
     "des achats de travaux, ou la personne nommée."),
    ("Standard (vérifié)", "Numéro d'accueil officiel de l'organisme dans l'annuaire de l'administration "
     "(service-public.fr) : demandez la personne nommée."),
    ("Standard (probable)", "Le numéro en a la forme, sans confirmation par l'annuaire."),
    ("Ordre des appels", "Par acheteur (rang de l'onglet « Acheteurs ») : personnes nommées d'abord, puis "
     "consultations closes récemment (l'acheteur compare les offres), puis consultations ouvertes."),
    ("Score", "1 point par appel d'offres de travaux (15 au plus), 0,25 par lot (10 au plus), 2 si l'acheteur a "
     "publié cette semaine, 6 pour un bailleur social, 5 pour un aménageur, une SEM ou une SPL, 2 pour une "
     "collectivité ; puis, une fois par acheteur : 3 si les critères qualitatifs pèsent 50 % ou plus, 2 pour "
     "un accord-cadre avec remise en concurrence, 1 pour une procédure avec négociation, 2 si une date limite "
     "tombe dans 7 à 28 jours. À score égal, la date limite la plus proche passe devant."),
    ("Prospection par e-mail", "Adresse professionnelle, sujet lié à son métier, et toujours un moyen simple "
     "de ne plus être contacté."),
]


def _consultations(a) -> str:
    lignes = []
    for x in a.derniers_avis:
        etat = ""
        if x.datelimite:
            etat = f", date limite {x.datelimite:%d/%m}" + (
                f" : {x.jours_restants} j" if x.jours_restants is not None and x.jours_restants >= 0 else " : close")
        parue = f"parue le {x.dateparution:%d/%m}" if x.dateparution else ""
        lignes.append(f"{x.objet} ({parue}{etat}) {x.url}")
    return "\n".join(lignes)


def tableur_prospects(acheteurs: list, appels: list) -> bytes:
    """Classeur Excel : onglets « Appels », « Acheteurs » et « Mode d'emploi »."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    from .prospects import NATURE_NUMERO

    wb = Workbook()

    def feuille(ws, colonnes, lignes, largeurs):
        ws.append(colonnes)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="EEF6F1")
            c.alignment = Alignment(vertical="top", wrap_text=True)
        for ligne in lignes:
            ws.append(ligne)
        for i, largeur in enumerate(largeurs, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = largeur
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(c.value, str) and c.value.startswith("http"):
                    c.hyperlink, c.style = c.value, "Hyperlink"
                    c.alignment = Alignment(vertical="top", wrap_text=True)
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = "Appels"
    feuille(ws, COLONNES_APPELS, [[
        x.rang, x.acheteur.nom, x.numero, x.nature_libelle, x.demander, x.fonction, x.avis.objet,
        x.avis.idweb, x.avis.url, x.statut, x.acheteur.contact, x.acheteur.profil_acheteur,
        "", "", "",
    ] for x in appels], [6, 28, 16, 18, 26, 24, 45, 11, 20, 28, 28, 20, 12, 30, 14])

    feuille(wb.create_sheet("Acheteurs"), COLONNES_ACHETEURS, [[
        i, a.nom, a.type_acheteur or "Autre acheteur public",
        "Nouveau" if not a.deja_signale else f"Déjà signalé le {a.deja_signale}", a.score,
        "\n".join(a.raisons), len(a.avis), a.nb_lots, a.nb_recents, a.contact, a.telephone,
        NATURE_NUMERO.get(a.nature_telephone, ""), a.libelle_telephone, a.source_telephone[1],
        a.profil_acheteur, a.lien_annuaire, _consultations(a),
    ] for i, a in enumerate(acheteurs, 1)], [6, 30, 20, 16, 7, 40, 12, 7, 10, 28, 16, 18, 26, 20, 20, 20, 70])

    ws = wb.create_sheet("Mode d'emploi")
    for titre, texte in MODE_D_EMPLOI:
        ws.append([titre, texte])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 24, 110
    for row in ws.iter_rows():
        row[0].font = Font(bold=True)
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)

    f = io.BytesIO()
    wb.save(f)
    return f.getvalue()


def construire_prospects(acheteurs: list, aujourdhui: date, config: dict, nb_avis: int) -> Courriel:
    from .prospects import liste_appels

    p = config.get("prospects") or {}
    reglages = p.get("appels") or {}
    appels = liste_appels(acheteurs, int(reglages.get("max_par_acheteur", 3)),
                          int(reglages.get("jours_clos_max", 60)))
    lundi = aujourdhui - timedelta(days=aujourdhui.weekday())
    n = len(acheteurs)
    sujet = (f"[Prospects BOAMP] {n} acheteur{'s' if n > 1 else ''} de travaux actif{'s' if n > 1 else ''}, "
             f"semaine du {lundi.strftime('%d/%m/%Y')}")
    ctx = dict(sujet=sujet, aujourdhui=aujourdhui, acheteurs=acheteurs, nb_avis=nb_avis,
               jours=p.get("jours", 90), jours_recents=p.get("jours_recents", 7),
               nouveaux=sum(1 for a in acheteurs if not a.deja_signale), appels=appels,
               nb_appelables=len({x.rang for x in appels}))
    env = _env()
    fichier = f"prospects-{lundi.isoformat()}.xlsx"
    return Courriel(sujet, env.get_template("prospects.txt.j2").render(**ctx),
                    env.get_template("prospects.html.j2").render(**ctx),
                    [(fichier, tableur_prospects(acheteurs, appels))] if acheteurs else [])


def envoyer(c: Courriel) -> None:
    """Envoi via Resend si RESEND_API_KEY est défini, sinon via SMTP."""
    dest = [a.strip() for a in os.environ.get("EMAIL_DESTINATAIRE", "").split(",") if a.strip()]
    if not dest:
        raise RuntimeError("EMAIL_DESTINATAIRE n'est pas défini dans .env")
    expediteur = os.environ.get("EMAIL_EXPEDITEUR") or os.environ.get("SMTP_USER")
    if not expediteur:
        raise RuntimeError("EMAIL_EXPEDITEUR (ou SMTP_USER) n'est pas défini dans .env")

    if os.environ.get("RESEND_API_KEY"):
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
            json={"from": expediteur, "to": dest, "subject": c.sujet, "text": c.texte, "html": c.html,
                  **({"attachments": [{"filename": nom, "content": base64.b64encode(contenu).decode("ascii")}
                                      for nom, contenu in c.pieces_jointes]} if c.pieces_jointes else {})},
            timeout=30,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Envoi Resend refusé ({r.status_code}) : {r.text[:300]}")
        return

    hote = os.environ.get("SMTP_HOST")
    if not hote:
        raise RuntimeError("Ni RESEND_API_KEY ni SMTP_HOST ne sont définis dans .env")
    port = int(os.environ.get("SMTP_PORT", "587"))
    msg = EmailMessage()
    msg["Subject"] = c.sujet
    msg["From"] = expediteur
    msg["To"] = ", ".join(dest)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=expediteur.split("@")[-1].strip(">"))
    msg.set_content(c.texte)
    msg.add_alternative(c.html, subtype="html")
    for nom, contenu in c.pieces_jointes:
        msg.add_attachment(contenu, maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=nom)

    contexte = ssl.create_default_context()
    if port == 465:
        serveur = smtplib.SMTP_SSL(hote, port, context=contexte, timeout=30)
    else:
        serveur = smtplib.SMTP(hote, port, timeout=30)
    with serveur:
        if port != 465 and os.environ.get("SMTP_STARTTLS", "1") != "0":
            serveur.starttls(context=contexte)
        if os.environ.get("SMTP_USER"):
            serveur.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        serveur.send_message(msg)
