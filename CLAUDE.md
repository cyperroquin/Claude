# Veille BOAMP pour BulQ — consignes pour Claude

Répondre et écrire en français (messages, e-mails, commentaires, commits).
L'utilisateur est Cyrille Perroquin ; il travaille pour Paul Ehrhardt (BulQ).
Il n'est pas développeur : expliquer pas à pas, en termes simples.

Avant toute modification, lire `docs/HISTORIQUE.md` : il retrace tout ce qui a
été décidé depuis le cahier des charges initial, y compris ce qui a été refusé.

## Ce que fait le projet

Deux e-mails automatiques, construits à partir de l'API open data du BOAMP
(jamais le site boamp.fr) :

- **Flux 1, veille quotidienne** (`python -m veille_boamp`) : consultations
  auxquelles BulQ peut répondre comme éditeur de logiciel. Tous les jours vers
  7 h 30 (Paris).
- **Flux 2, prospects acheteurs** (`python -m veille_boamp --prospects`) : les
  25 maîtres d'ouvrage qui publient le plus d'appels d'offres de travaux, avec
  e-mail, téléphone et profil acheteur. Chaque lundi vers 7 h.

Tous les critères sont dans `config.yaml`. Le README détaille les commandes.

## Où ça tourne

- Sur **GitHub Actions** du dépôt `cyperroquin/claude` (branche
  `claude/new-session-gt8ebp`, la seule et donc la branche par défaut) :
  `.github/workflows/veille-boamp.yml` et `prospects-acheteurs.yml`.
- Secrets GitHub : `RESEND_API_KEY`, `EMAIL_DESTINATAIRE`, `EMAIL_EXPEDITEUR`.
- La mémoire (`veille.db` : avis déjà envoyés, acheteurs déjà signalés) est
  conservée sur la branche **`veille-etat`** : ne jamais la supprimer.
- Lancement manuel : onglet Actions → workflow → Run workflow, mode
  `essai` (rien n'est envoyé), `envoi` (e-mail de test forcé) ou, pour la
  veille quotidienne, `rejeu-30-jours`.

## Règles de travail

- Lancer `python -m pytest tests` avant chaque commit (tests hors ligne sur de
  vrais avis enregistrés dans `tests/fixtures`).
- Ne pas changer la sélection ni les scores sans demande explicite : la
  dernière consigne de l'utilisateur est « ne rien changer au flux ».
- Pour vérifier sur des données réelles, lancer le workflow en mode `essai`
  et lire le journal ; ne pas envoyer d'e-mail sans le demander.
