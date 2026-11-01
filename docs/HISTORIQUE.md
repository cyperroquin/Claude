# Historique du projet « Veille BOAMP pour BulQ »

Résumé de la conversation de travail (octobre 2026) qui a construit ce dépôt.
Il sert de mémoire pour reprendre le projet dans une autre session ou un autre
compte Claude.

## Contexte

BulQ est un logiciel SaaS B2B (10 000 à 100 000 € par an) pour économistes de
la construction, entreprises générales, bailleurs et maîtres d'œuvre :
chiffrage, comparaison de devis, analyse des mémoires techniques, notation des
offres. Paul Ehrhardt (BulQ) a demandé à Cyrille une veille des marchés
publics. Deux usages :

1. **Flux 1** : repérer les consultations auxquelles BulQ peut candidater comme
   éditeur de logiciel.
2. **Flux 2** : repérer les acheteurs publics qui vont devoir comparer beaucoup
   d'offres, pour les démarcher.

## Chronologie

### 12/10 — Flux 1, à partir du cahier des charges

- Source : API OpenDataSoft du BOAMP (`boamp-datadila.opendatasoft.com`, jeu
  `boamp`). Les champs réels ont été vérifiés : CPV, description et parfois
  date limite ne sont que dans le JSON `donnees` (formulaires national, MAPA,
  eForms), lus de façon tolérante.
- Score selon le cahier des charges (CPV, groupes de mots-clés A, B, C,
  acheteur lié à la construction, exclusions), seuils 7 « À examiner » et 4
  « À vérifier », date limite ≥ 7 jours (urgent entre 3 et 6, ignoré en
  dessous), journal SQLite anti-doublon, `--backfill`, `--dry-run`.
- Calibrage sur 90 jours : 102 avis retenus au départ, presque tous hors sujet.
  Corrections : formules juridiques neutralisées (« Code de la commande
  publique », « par application des prix du bordereau »…), mots A et B à
  12 mots au plus l'un de l'autre, « achats » remplacé par des expressions
  métier, CPV moyens en code exact, +1 pour un CPV informatique générique
  croisé avec un mot-clé, certaines exclusions limitées à l'objet. Résultat :
  16 avis sur 90 jours. Références : Annecy (26-26177) 10, CINOR (26-18679) 9,
  OPPIC (26-69430) 7. Seule vraie cible sur la période : Ville de Marseille,
  « système d'information dédié aux marchés publics » (26-77119, 26-83228),
  close au 17/09.

### 19-20/10 — Mise en service

- Document de synthèse « Veille BOAMP — synthèse et mode d'emploi » (Claude
  Docs) : offres repérées, méthode de sélection, mode d'emploi pour débutant.
- Envoi par Resend (clé dans les secrets GitHub, expéditeur
  `onboarding@resend.dev`).
- Lancement quotidien via GitHub Actions (deux créneaux UTC pour l'heure
  d'été et d'hiver ; une garde ne laisse passer qu'un passage par jour). Le
  fichier du workflow a été créé à la main par Cyrille (attention au point de
  `.github`).

### 20/10 — Flux 2, acheteurs de travaux actifs

- Choix de Cyrille : cibler les acheteurs (pas les entreprises) ; e-mail
  hebdomadaire.
- Appels d'offres de travaux des 90 derniers jours, regroupés par acheteur.
  Score : 1 par appel d'offres (15 max), 0,25 par lot (10 max), +2 si actif
  dans les 7 jours, +6 bailleur social, +5 aménageur/SEM/SPL, +2 collectivité.
  Au moins 2 avis. 25 acheteurs listés, marqués « Nouveau » ou « Déjà signalé ».

### 22/10 — Correctif « notation des offres » (fichier de Cyrille)

- Flux 1 : nouveaux mots-clés (rapport d'analyse des offres, rapport de
  notation, courriers de notification / de rejet, outil de notation…). Aucun
  bruit ajouté sur un rejeu de 30 jours.
- Flux 2, bonus comptés une fois par acheteur (décision de Cyrille) : +3 si
  les critères non-prix pèsent ≥ 50 % (`veille_boamp/criteres.py`, lisible
  dans environ 24 % des avis), +2 accord-cadre avec remise en concurrence,
  +1 procédure avec négociation, +2 date limite dans 7 à 28 jours ; tri à
  score égal par échéance ; colonne `part_qualitatif` ajoutée à `avis`.
- Laissés de côté : l'accroche commerciale (B4) et le bloc C optionnel
  (maîtres d'œuvre attributaires récents).

### 28/10 — Appel Cyrille / Paul

- BulQ a sorti en septembre un module « appels d'offres » (chez 3 clients) :
  les consultations du type Annecy/CINOR redeviennent pertinentes. Paul veut
  lire les DCE pour comprendre les fonctionnalités attendues ; un DCE n'est
  plus téléchargeable une fois la consultation close.
- Cible jugée la plus intéressante par Paul : les procédures lourdes à
  analyser pour un acheteur sans maître d'œuvre, typiquement les
  accords-cadres à bons de commande d'entretien et de maintenance, multi-lots,
  sur environ 4 ans (exemple : Région Grand Est).
- Paul trouve la sélection pertinente mais veut une liste à appeler, avec
  noms et téléphones, et ne comprend pas le score.
- Un plan de refonte a été proposé ; Cyrille l'a refusé : « ne rien changer
  au flux ». Seule demande retenue, faite le 31/10 : ajouter dans l'e-mail du
  flux 2 les colonnes e-mail, téléphone et profil acheteur. Les adresses
  « nepasrepondre » et « noreply » sont écartées. Sur données réelles :
  téléphone pour 22 acheteurs sur 23 lus, e-mail 21, profil acheteur 18.

## Points ouverts

- Envoyer à Paul les DCE téléchargés (action manuelle de Cyrille).
- Éventuellement, plus tard : liste d'appels par consultation, ciblage des
  accords-cadres à bons de commande (plan du 31/10, non retenu à ce jour).
- Depuis le 20/10, l'environnement cloud Claude utilisé n'arrivait plus à
  joindre l'API du BOAMP (coupures réseau) ; GitHub Actions n'est pas touché.
  Tester sur GitHub en mode `essai`.
