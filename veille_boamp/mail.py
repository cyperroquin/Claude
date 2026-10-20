"""Construction et envoi de l'e-mail quotidien."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
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


def construire_prospects(acheteurs: list, aujourdhui: date, config: dict, nb_avis: int) -> Courriel:
    p = config.get("prospects") or {}
    lundi = aujourdhui - timedelta(days=aujourdhui.weekday())
    n = len(acheteurs)
    sujet = (f"[Prospects BOAMP] {n} acheteur{'s' if n > 1 else ''} de travaux actif{'s' if n > 1 else ''}, "
             f"semaine du {lundi.strftime('%d/%m/%Y')}")
    ctx = dict(sujet=sujet, aujourdhui=aujourdhui, acheteurs=acheteurs, nb_avis=nb_avis,
               jours=p.get("jours", 90), jours_recents=p.get("jours_recents", 7),
               nouveaux=sum(1 for a in acheteurs if not a.deja_signale))
    env = _env()
    return Courriel(sujet, env.get_template("prospects.txt.j2").render(**ctx),
                    env.get_template("prospects.html.j2").render(**ctx))


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
            json={"from": expediteur, "to": dest, "subject": c.sujet, "text": c.texte, "html": c.html},
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
