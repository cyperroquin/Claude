# Veille BOAMP pour BulQ

Chaque matin, le script repère les consultations du BOAMP auxquelles BulQ peut
répondre comme éditeur de logiciel. Il leur donne un score, puis envoie un
e-mail au responsable de la veille. Cette version couvre la phase 1b du cahier
des charges : le BOAMP seul, flux 1 (candidature comme éditeur).

La source est l'API open data officielle du BOAMP (DILA, Licence Ouverte),
hébergée sur OpenDataSoft. Le site boamp.fr n'est jamais aspiré.

## Installation

```sh
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # puis renseigner le destinataire et l'envoi (SMTP ou Resend)
```

## Utilisation

| Commande | Effet |
| --- | --- |
| `python -m veille_boamp --champs` | Affiche les champs réels de l'API et signale les champs attendus absents. Se lance aussi tout seul au premier lancement. |
| `python -m veille_boamp --backfill 90` | Rejoue les 90 derniers jours et affiche le nombre d'avis par critère, par mot d'exclusion et par décision, puis la liste des avis retenus. Sert à calibrer `config.yaml`. N'envoie rien. |
| `python -m veille_boamp --dry-run` | Fait le passage quotidien et affiche l'e-mail sans l'envoyer (version HTML dans `apercu_email.html`). Les avis ne sont pas marqués comme envoyés. |
| `python -m veille_boamp` | Passage quotidien avec envoi réel. |
| `python -m veille_boamp --avis 26-26177 26-18679` | Note un ou plusieurs avis précis et détaille le score. |

Options communes : `--config`, `--db`, `--env`, `--date AAAA-MM-JJ` (pour
simuler un autre jour), `-v`.

## Fonctionnement

1. **Collecte** : appel de l'API sur les avis parus ces 48 dernières heures,
   de nature « Avis de marché » (`APPEL_OFFRE`) ou « Avis informatif »
   (`PRE-INFORMATION`), dont la date limite est dans le futur. Les avis
   d'attribution, rectificatifs et annulations sont écartés. Aucun filtre
   géographique : la France entière et l'Outre-mer sont couverts.
2. **Extraction** : objet, acheteur et dates viennent des champs à plat. Le
   code CPV, la description et, pour les procédures restreintes, la date
   limite ne figurent que dans le champ JSON `donnees`, dont la structure
   change selon le formulaire (national, MAPA, eForms européen). Le script
   les cherche par nom de clé, à n'importe quelle profondeur.
3. **Score** : il suit la section 2.5 du cahier des charges et se règle dans
   `config.yaml` (voir plus bas).
4. **Décision** : 7 ou plus, « À examiner » ; de 4 à 6, « À vérifier » ; en
   dessous, l'avis est seulement enregistré dans le journal. Date limite à 7
   jours ou plus : alerte normale. Entre 3 et 6 jours : alerte « urgent ».
   Moins de 3 jours : l'avis est ignoré.
5. **Journal** : `veille.db` (SQLite) garde chaque avis analysé, avec son
   score et ses raisons. La clé `idweb` et la colonne `envoye_le` garantissent
   qu'un avis n'est jamais envoyé deux fois.
6. **E-mail** : l'objet suit le format `[Veille BOAMP] N consultations à
   examiner, JJ/MM/AAAA`. Le corps contient un tableau (avis urgents en tête,
   puis score décroissant), les avis informatifs dans une section séparée et
   la checklist go/no-go. S'il n'y a rien, un court message « rien
   aujourd'hui » part au plus une fois tous les 7 jours.

## Régler les critères (`config.yaml`)

Tout est dans `config.yaml` : natures d'avis, seuils de date et de score,
groupes de mots-clés A, B et C, règles de score avec leurs points, mots
d'exclusion et checklist. Le code n'a pas à changer.

- La recherche ignore la casse, les accents et la ponctuation. Chaque mot
  accepte un « s » ou un « x » final.
- Types de règles : `cpv`, `mots`, `mots_croises`, `cpv_et_mots`, `acheteur`.
  La hiérarchie CPV est respectée : `48000000` couvre tous les codes `48xxxxxx`.
- `neutraliser` : formules administratives effacées avant la recherche (par
  exemple « Code de la commande publique » ou « en application des
  articles »). Sans elles, presque tous les avis cumulent « application » et
  « commande publique » et obtiennent 4 points.
- `exclusions` : chaque entrée peut être limitée à l'objet (`champs:
  [objet]`) et peut neutraliser des expressions (`sauf`).

### Écarts au cahier des charges, faits pour la calibration

- **Proximité A + B** (`distance_max: 12`) : les deux mots doivent se trouver
  dans le même champ, à 12 mots au plus l'un de l'autre. Sans cette règle, le
  premier passage réel donnait 24 avis « À vérifier » sur 368, presque tous
  hors sujet (gardiennage, portage de repas…).
- **CPV faibles croisés** (`cpv_faible_croise`, +1) : le cahier des charges
  dit « à croiser avec un mot-clé » sans donner de points. Cette règle donne
  +1 point quand un CPV 48000000, 72000000 ou 79418000 (ou un de leurs
  sous-codes) s'ajoute à un mot-clé. Sans elle, l'avis OPPIC n'atteindrait
  que 6 points au lieu de 7.
- **Exclusions limitées à l'objet** : « maintenance du logiciel »,
  « restauration », « assurance », « gestion financière », « sage » et
  « tierce maintenance applicative ». La description de l'OPPIC cite
  « maintenance du logiciel » parmi les prestations attendues, et les avis
  logiciels parlent souvent de « restauration des données » ou d'« assurance
  qualité ». « bibliothèque » n'exclut pas « bibliothèque de prix » ni
  « bibliothèque de clauses ».
- **Ajouts** : « progiciel » rejoint le groupe A. Les avis sans
  `datelimitereponse` (procédures restreintes) sont gardés : leur date limite
  est prise dans `donnees` (remise des offres ou des candidatures).

## Planification (7 h 30, heure de Paris)

- **Linux (cron)** : voir `deploy/crontab.txt`. La ligne `CRON_TZ=Europe/Paris`
  fixe le fuseau.
- **macOS (launchd)** : voir `deploy/fr.bulq.veille-boamp.plist`.

Les deux appellent `deploy/lancer.sh`, qui écrit la sortie dans `veille.log`.

## Tests

```sh
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests
```

Les tests tournent sans réseau, sur de vrais avis du BOAMP enregistrés dans
`tests/fixtures` :

- les trois consultations de référence (Annecy 26-26177, CINOR 26-18679,
  OPPIC 26-69430) obtiennent 7 ou plus ;
- les avis d'assurance, de voyage scolaire et de maintenance Sage sont exclus ;
- un même avis n'est jamais envoyé deux fois ;
- les seuils de date et l'ordre de l'e-mail sont respectés.

## Limites connues

- Le BOAMP ne couvre pas tous les marchés. Une partie des marchés sous les
  seuils ne paraît que sur les profils acheteur.
- Le code CPV manque souvent. Le score repose donc d'abord sur les mots-clés.
- L'API OpenDataSoft limite `offset + limit` à 10 000. Le rejeu interroge
  donc l'API jour par jour.
