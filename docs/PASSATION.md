# Passation — Veille BOAMP pour BulQ

*Rédigé le 23/11/2026 par Cyrille Perroquin, avec Claude, pour la personne
qui reprend le projet.*

Ce document dit ce que fait le projet, où il en est, quels comptes il utilise
et comment les transférer pas à pas. L'historique détaillé des décisions
(y compris ce qui a été refusé) est dans [`HISTORIQUE.md`](HISTORIQUE.md).
Les commandes techniques sont dans le [`README.md`](../README.md).

---

## 1. Le projet en bref

Deux e-mails automatiques, construits à partir de l'**API open data du BOAMP**
(jamais le site boamp.fr, qui interdit l'accès automatique) :

| | Flux 1 — Veille quotidienne | Flux 2 — Prospects acheteurs |
|---|---|---|
| **But** | Repérer les consultations auxquelles BulQ peut répondre comme éditeur de logiciel | Repérer les acheteurs publics qui comparent beaucoup d'offres de travaux, pour les démarcher par téléphone |
| **Quand** | Tous les jours vers 7 h 30 (Paris) | Chaque lundi vers 7 h |
| **Contenu** | Les avis notés 4 ou plus (« À vérifier ») et 7 ou plus (« À examiner ») ; un message « rien aujourd'hui » une fois par semaine s'il n'y a rien | Une **liste d'appels** (numéro, personne à demander, consultation dont parler) et un **fichier Excel** joint : onglets « Appels », « Acheteurs », « Mode d'emploi » |
| **Workflow GitHub** | `Veille BOAMP` | `Prospects acheteurs` |

Tout tourne gratuitement sur **GitHub Actions**. Aucun serveur, aucun
ordinateur à laisser allumé.

---

## 2. Ce qui a été fait

| Date | Étape |
|---|---|
| 12/10/2026 | Flux 1 construit d'après le cahier des charges, puis calibré sur 90 jours : de 102 avis retenus (presque tous hors sujet) à 16 |
| 19-20/10 | Mise en service : envoi par Resend, lancement quotidien sur GitHub Actions ; document « Veille BOAMP — synthèse et mode d'emploi » (Claude Docs) |
| 20/10 | Flux 2 créé : les 25 acheteurs de travaux les plus actifs, e-mail hebdomadaire |
| 22/10 | Correctif « notation des offres » : nouveaux mots-clés (flux 1), nouveaux bonus (flux 2) |
| 28/10 | Appel avec Paul Ehrhardt : sélection jugée pertinente, mais il veut des noms et des téléphones. Plan de refonte proposé et **refusé** (« ne rien changer au flux ») |
| 31/10 | Colonnes e-mail, téléphone et profil acheteur dans le flux 2 |
| 02/11 | Destinataire changé ; nature de chaque numéro (ligne directe, service, standard) ; standards vérifiés dans l'annuaire officiel de service-public.fr ; **liste d'appels** et fichier Excel joint |
| 09/11 | Nouveaux acheteurs nommés et marqués « Nouveau » dans la liste d'appels |

**Ce qui n'a pas changé depuis le 22/10 : la sélection des avis et les scores.**

---

## 3. État actuel (23/11/2026)

- **Les deux flux tournent normalement.** Chaque lundi, l'e-mail des
  prospects part vers 7 h 05. La veille quotidienne passe vers 7 h 30 et
  n'envoie quelque chose que si un avis passe le seuil.
- **Les tests** (`python -m pytest tests`) passent : 58 tests hors ligne, sur
  de vrais avis enregistrés dans `tests/fixtures`.
- **Les e-mails arrivent dans une seule boîte** : celle de Cyrille. Ils
  partent de l'adresse d'essai de Resend (`onboarding@resend.dev`). Gmail les
  range souvent dans l'onglet « Mises à jour » : créer un filtre pour les
  faire arriver dans « Principale » (voir étape 4).

---

## 4. Les comptes et les accès

| Élément | Où | À qui aujourd'hui | Ce qu'il faut faire |
|---|---|---|---|
| **Le code** | Dépôt GitHub `cyperroquin/claude` (**public**) | Compte GitHub de Cyrille | Le transférer (étape 1) |
| **Branche qui tourne** | `claude/new-session-gt8ebp` (branche par défaut) | — | Rien. Ne pas la supprimer |
| **La mémoire** | Branche `veille-etat` : fichier `veille.db` (avis déjà envoyés, acheteurs déjà signalés) | — | **Ne jamais la supprimer**, sinon tout est renvoyé comme nouveau |
| **Les 3 secrets** | GitHub → Settings → Secrets and variables → Actions : `RESEND_API_KEY`, `EMAIL_DESTINATAIRE`, `EMAIL_EXPEDITEUR` | Valeurs liées au compte Resend de Cyrille | Les remplacer (étape 2) |
| **L'envoi des e-mails** | Compte Resend (resend.com) | Compte de Cyrille, ouvert avec son adresse bulq.ai | Le repreneur ouvre le sien (étape 2) |
| **Claude** | Claude Code (claude.ai/code), avec ce dépôt | Compte Claude de Cyrille | Le repreneur se connecte avec son propre compte (étape 3) |
| **Ancien document de synthèse** | Claude Docs : « Veille BOAMP — synthèse et mode d'emploi » (19/10) | Cyrille | Le partager si utile ; il est daté (pas de flux 2, pas de liste d'appels). Ce document-ci le remplace |

Les **valeurs** des secrets ne sont jamais visibles, même pour le propriétaire
du dépôt : on peut seulement les remplacer. Il n'y a donc rien à « copier » :
le repreneur crée ses propres valeurs.

---

## 5. Transférer le projet, pas à pas

### Étape 1 — Donner le dépôt GitHub

Le repreneur a besoin d'un compte GitHub (gratuit, github.com).

**Option A — Le transférer entièrement (recommandé si Cyrille part)**
1. Cyrille ouvre https://github.com/cyperroquin/Claude/settings
2. Tout en bas, zone **« Danger Zone »** → **« Transfer ownership »**.
3. Il saisit le nom du compte GitHub du repreneur, ou d'une organisation
   GitHub BulQ si elle existe (c'est le mieux : le projet n'appartient plus à
   une personne).
4. Le repreneur accepte le transfert dans l'e-mail que GitHub lui envoie.

Après le transfert, GitHub redirige les anciens liens. Les branches, les
workflows et normalement les secrets suivent le dépôt. **Vérifier** dans
Settings → Secrets and variables → Actions que les trois secrets sont là ;
sinon, les recréer à l'étape 2.

**Option B — L'ajouter comme collaborateur (si Cyrille reste propriétaire)**
1. https://github.com/cyperroquin/Claude/settings/access → **« Add people »**.
2. Saisir le compte GitHub du repreneur ; il accepte l'invitation.

Il pourra tout modifier, lancer les workflows et changer les secrets, mais le
dépôt restera sur le compte de Cyrille.

### Étape 2 — Faire arriver les e-mails chez le repreneur

Avec l'adresse d'essai `onboarding@resend.dev`, Resend n'envoie **qu'à
l'adresse du titulaire du compte Resend**. Le repreneur doit donc avoir le
sien.

1. Le repreneur crée un compte sur https://resend.com **avec l'adresse où il
   veut recevoir les e-mails**.
2. https://resend.com/api-keys → **« Create API Key »** → nom
   `Veille BOAMP`, permission **« Sending access »** → copier la clé
   (commence par `re_`, affichée une seule fois).
3. Dans GitHub → Settings → Secrets and variables → Actions, modifier
   (crayon ✏️) :
   - `RESEND_API_KEY` → la nouvelle clé ;
   - `EMAIL_DESTINATAIRE` → l'adresse du repreneur ;
   - `EMAIL_EXPEDITEUR` → laisser `onboarding@resend.dev`.
4. Cyrille peut ensuite supprimer sa clé dans son compte Resend.

**Pour envoyer à plusieurs personnes** (par exemple le repreneur et Paul),
il faut faire valider le domaine `bulq.ai` dans Resend (Domains → Add
domain). Cela demande d'ajouter quelques enregistrements DNS chez
l'hébergeur du domaine : c'est la personne qui gère les DNS de BulQ qui le
fait. Ensuite :
- `EMAIL_EXPEDITEUR` → par exemple `veille@bulq.ai` ;
- `EMAIL_DESTINATAIRE` → plusieurs adresses séparées par des virgules.

### Étape 3 — Vérifier que tout marche

Dans GitHub, onglet **Actions** :
1. **Veille BOAMP** → **Run workflow** → mode **`envoi`** → Run.
2. **Prospects acheteurs** → **Run workflow** → mode **`envoi`** → Run.
3. Deux minutes plus tard, les deux e-mails doivent arriver. S'ils ne sont
   pas dans la boîte de réception, regarder dans les spams et dans l'onglet
   « Mises à jour ».
4. En cas d'échec, cliquer sur l'exécution en rouge pour lire le journal.
   Un message « You can only send testing emails to your own email address »
   veut dire que `EMAIL_DESTINATAIRE` n'est pas l'adresse du compte Resend.

Dans Gmail, pour être notifié : ouvrir un des e-mails → ⋮ → **« Filtrer les
messages similaires »** → De : `onboarding@resend.dev` → **« Créer un
filtre »** → cocher **« Catégoriser comme : Principale »** et **« Ne jamais
envoyer dans le dossier Spam »**.

Les modes des workflows :
- **`essai`** : fait tout le calcul et affiche l'e-mail dans le journal,
  **sans rien envoyer**. À utiliser pour vérifier une modification.
- **`envoi`** : envoie un e-mail de test, même s'il est déjà parti.
- **`rejeu-30-jours`** (veille quotidienne seulement) : compte ce qui aurait
  été retenu sur 30 jours, sans e-mail.

### Étape 4 — Reprendre le travail avec Claude

Le projet a été construit avec Claude Code, et il est fait pour être
continué de la même façon, sans être développeur.

1. Le repreneur ouvre https://claude.ai/code avec son compte Claude et
   connecte son compte GitHub quand c'est demandé.
2. Si le dépôt n'apparaît pas : installer l'application Claude sur le compte
   GitHub qui possède le dépôt, via
   https://github.com/apps/claude/installations/select_target
3. Il lance une session sur le dépôt, branche `claude/new-session-gt8ebp`.
4. Premier message conseillé : *« Lis CLAUDE.md, docs/PASSATION.md et
   docs/HISTORIQUE.md, puis résume-moi où en est le projet. »*

Claude lit automatiquement `CLAUDE.md` : ce fichier contient les règles du
projet (langue, ne pas changer la sélection sans demande, lancer les tests,
tester en mode `essai`…). Le mettre à jour avec le nom du repreneur.

### Étape 5 — Cyrille rend la main

- [ ] Dépôt transféré (ou repreneur ajouté comme collaborateur).
- [ ] Les trois secrets pointent vers le compte Resend du repreneur.
- [ ] Les deux e-mails de test sont arrivés chez le repreneur.
- [ ] Le repreneur a ouvert une session Claude sur le dépôt.
- [ ] `CLAUDE.md` mis à jour (nom du repreneur, interlocuteurs).
- [ ] Le fichier Excel de suivi des appels déjà passés est remis au
      repreneur (voir point 8).
- [ ] Cyrille supprime sa clé Resend et, si le dépôt est transféré, n'a plus
      rien à faire.

---

## 6. Modifier le projet

### Comment
Le plus simple : demander à Claude, en français, ce qu'on veut changer. Il
modifie le code, lance les tests, enregistre et publie sur la branche qui
tourne. Ensuite, toujours vérifier avec le mode **`essai`** avant de
demander un e-mail de test.

### Où sont les choses

| Pour changer… | Fichier |
|---|---|
| Mots-clés, codes CPV, points, seuils, nombre d'acheteurs, règles de la liste d'appels | `config.yaml` (commenté, sans programmation) |
| Texte et mise en page des e-mails | `veille_boamp/templates/*.j2` |
| Fichier Excel joint | `veille_boamp/mail.py` (`tableur_prospects`) |
| Calcul du flux 1 / du flux 2 | `veille_boamp/scoring.py` / `veille_boamp/prospects.py` |
| Vérification des standards | `veille_boamp/annuaire.py` |
| Horaires d'envoi | `.github/workflows/veille-boamp.yml`, `prospects-acheteurs.yml` (heures en UTC, deux créneaux pour l'heure d'été et d'hiver) |
| Tests | `tests/` |

### Règles à garder
- Lancer `python -m pytest tests` avant chaque enregistrement.
- **Ne pas changer la sélection ni les scores sans décision explicite**
  (dernière consigne de Cyrille : « ne rien changer au flux »).
- Ne jamais supprimer la branche `veille-etat`.
- Ne jamais écrire de clé, de mot de passe ou d'adresse e-mail dans les
  fichiers du dépôt : **il est public**. Les mettre dans les secrets GitHub.

---

## 7. Points de vigilance

1. **Le dépôt est public.** N'importe qui peut lire le code et l'historique
   (aucune clé n'y figure ; les secrets GitHub restent invisibles). On peut le
   rendre privé dans Settings → General → Danger Zone → « Change
   visibility ». GitHub Actions reste gratuit en privé dans la limite de
   2 000 minutes par mois ; le projet en consomme moins de 150.
2. **Arrêt automatique après 60 jours sans activité.** Sur un dépôt public,
   GitHub désactive les tâches planifiées si personne n'a rien modifié depuis
   60 jours, et prévient le propriétaire par e-mail. Pour relancer : onglet
   Actions → choisir le workflow → « Enable workflow ». Rendre le dépôt privé
   supprime ce risque.
3. **Avertissement « Node.js 20 is deprecated »** dans les journaux : sans
   effet aujourd'hui. Le jour où GitHub l'imposera, demander à Claude de
   mettre à jour `actions/checkout` et `actions/setup-python` dans les deux
   workflows.
4. **Les sessions Claude dans le cloud ne joignent pas l'API du BOAMP**
   (coupures réseau constatées depuis le 20/10). GitHub Actions n'est pas
   touché. Pour vérifier sur des données réelles : lancer un workflow en mode
   `essai` et lire le journal.
5. **Les numéros de téléphone sont recopiés tels que l'acheteur les a
   écrits** dans ses avis, erreurs comprises (chiffre en trop ou en moins). La
   mention « ligne directe / ligne du service / standard » est une estimation ;
   « standard (vérifié) » est confirmé par l'annuaire officiel, qui ne couvre
   que les administrations (pas les SPL, aménageurs, bailleurs).
6. **« Déjà signalé le 02/11 »** : les e-mails de test envoyés le 02/11 ont
   compté comme de vrais signalements.

---

## 8. Ce qui reste ouvert

- **Envoyer à Paul les DCE déjà téléchargés** (action manuelle de Cyrille,
  à faire avant de partir ou à confier au repreneur).
- **Suivi des appels** : chaque lundi apporte un nouveau fichier Excel, le
  suivi est donc éparpillé. Pour l'instant, tenir un seul fichier de suivi
  (par exemple une feuille Google Sheets) et y coller chaque semaine les
  lignes « Nouveau ». Piste proposée et non lancée : un suivi partagé que
  l'e-mail du lundi relirait pour afficher « appelé le… ».
- **Recevoir les e-mails à plusieurs** (Paul, le repreneur) : passe par la
  validation du domaine bulq.ai dans Resend (étape 2).
- **Pistes du 31/10, non retenues à ce jour** : liste d'appels par
  consultation, ciblage des accords-cadres à bons de commande d'entretien et
  de maintenance, multi-lots, sur environ 4 ans (la cible préférée de Paul).
  À ne lancer que sur décision.

## 9. Contacts

- **Paul Ehrhardt** (BulQ) : commanditaire de la veille, destinataire des DCE.
- **Cyrille Perroquin** : a mené le projet d'octobre à novembre 2026.
