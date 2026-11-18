# Phase 20 — simplification approfondie

## OBJECTIF

Clarifier les chemins actifs avant publication et retirer les compatibilités
expérimentales qui imposaient plusieurs formats de budget ou plusieurs
interfaces. Le code devait conserver la supervision, les reconnexions, le
traitement H.264/RTSP et l'intégration Home Assistant actuelle.

## MODIFICATIONS

- `netvue_bridge/budget.py` ne prend en charge que v4. Les migrations v2/v3,
  leurs audits et imports associés ont été supprimés ; la commande `migrate`
  a été retirée de `netvue_bridge/continuous.py`. `Budget.read()` refuse une
  autre version sans réécrire `budget.json`.
- `continuous.py` reste le seul superviseur et ne recherche plus l'ancien
  module `netvue_bridge.supervisor`. `stream.py` exécute une tentative unique ;
  les reconnexions restent décidées par le superviseur. Le module vidéo
  redondant `netvue_bridge/video.py` a été supprimé.
- `netvue_bridge/rtsp.py` appelle explicitement les fonctions de
  `netvue_bridge/h264.py`. Les anciens réexports H.264 et leurs tests ont été
  supprimés : il n'y a plus deux chemins d'import pour les mêmes fonctions.
- L'intégration HA ne déduit plus le nom d'un ancien URL, ne conserve plus
  les URL personnalisées et refuse une entrée sans configuration de flux
  gérée. Le flux est désormais déterminé par l'adresse de la caméra.
- Les tests et les documents actifs ont été adaptés. Aucune dépendance de
  production n'a été ajoutée.
- Le test HA synthétique utilise maintenant le port RTSP géré (8554) au lieu
  de contourner le validateur avec un port aléatoire ; le lancement conteneurisé
  est documenté et garde le dépôt en lecture seule.
- Les fichiers `findings/` restent ignorés par défaut pour préserver les
  analyses privées ; le rapport Phase 20 assaini est explicitement réintégré
  dans Git.

Le rapport courant est explicitement suivi par Git ; les autres éléments de
`findings/` restent ignorés pour préserver les analyses privées.

## ÉVALUATION DES BIBLIOTHÈQUES

- **Protocole LAN** — `struct`, `json` et `socket` couvrent déjà le framing
  binaire Netvue. Une bibliothèque réseau générique ne remplacerait pas les
  validations spécifiques des paquets et ajouterait une dépendance.
- **H.264/RTSP** — PyAV décode, parse les frontières vérifiées et publie déjà
  les flux ; MediaMTX fournit le serveur RTSP. Le décodage du compteur
  `first_mb_in_slice` et la correction PTS répondent aux anomalies du flux
  Netvue. Un parseur générique ne garantit pas ces règles fail-closed ; une
  bibliothèque de bits serait disproportionnée pour cette lecture limitée.
- **Supervision** — `tenacity`/équivalent pourrait fournir un backoff, mais
  pas l'état v4 verrouillé, la reprise après crash, le watchdog et le contrôle
  cgroup. Les ajouter séparerait une seule politique entre plusieurs couches.
- **Home Assistant** — l'intégration utilise déjà les API HA, `aiohttp` géré
  par HA et `voluptuous`. Aucun remplacement pertinent n'a été identifié.
- **HTTP** — `http.HTTPStatus` (bibliothèque standard) fournit les raisons
  HTTP à la place de l'ancienne table locale ; aucune dépendance n'est requise.

**Décision : aucune nouvelle bibliothèque de production.** Les utilitaires
standards et dépendances existantes correspondent aux responsabilités du code.

## FONCTIONNEMENT

- **Budget** — auparavant, `migrate_legacy()` décodait plusieurs générations,
  produisait un audit et transformait certains états ambigus. Maintenant,
  `read()` valide une seule structure v4 ; tout autre numéro de version
  déclenche une erreur explicite. Le test écrit v2 et v3 puis vérifie que les
  octets d'origine sont identiques après le refus.
- **H.264/RTSP** — auparavant, les symboles de `h264.py` étaient aussi exposés
  depuis `rtsp.py` pour compatibilité. `H264Publisher` utilise maintenant
  `h264.TimestampClock`, `h264.annexb_nals` et les helpers associés ; les
  appelants importent leur module propriétaire.
- **Home Assistant** — auparavant, `_managed_camera()` extrayait parfois un
  nom depuis une URL ancienne et laissait passer certains flux non gérés.
  Maintenant, il exige le nom et l'URL canoniques et `async_setup_entry()`
  signale explicitement une configuration non prise en charge.
- **Test HA synthétique** — auparavant, le scénario remplaçait l'URL gérée par
  un port aléatoire, incompatible avec le validateur de production. Il tourne
  maintenant sur le port contractuel 8554 et exerce les vrais helpers d'URL.

La validation v4, les verrous, l'exclusion d'une session par caméra, le
backoff progressif et les arrêts sur erreurs critiques restent en place.
Aucun quota journalier ou hebdomadaire n'a été réintroduit.

## TESTS

- Suite d'intégration Home Assistant 2026.11.2 avec les requirements du
  composant HA `stream` : **14/14 réussis** ; `pip check` ne signale aucun
  conflit.
- Image officielle Home Assistant 2026.11.2 sous Docker : scénario synthétique
  réussi sur réseau `--internal` sans ports publiés ; config flow, options,
  entités, RTSP H.264, aperçu JPEG, transition hors ligne et déchargement
  vérifiés. Le réseau temporaire a été supprimé. Le premier essai `--network
  none` ne fournissait aucune interface IPv4 à HA ; le réseau interne corrige
  cela sans route externe.
- Suite hors ligne Linux du pont (hors HA), avec MediaMTX et FFprobe
  synthétiques : **79 tests réussis**, **1 ignoré** faute de métriques cgroup
  v2. Le premier lancement global avait échoué à l'import HA ; les 14 tests
  HA ont ensuite réussi dans l'environnement isolé ci-dessus.
- RTSP synthétique normal et configuration de déploiement : réussis ;
  scénario de collision d'horodatages : réussi (80 images publiées, 39 paquets
  lus). Aucun test n'a contacté de caméra.
- `py_compile` sur les modules et tests modifiés : réussi.
- `git diff --check` : réussi.
- Dans le venv WSL, l'import HA signale un avertissement non bloquant : la
  bibliothèque native `libturbojpeg` n'est pas installée ; les 14 tests
  passent néanmoins. Le scénario Docker a réussi avec l'image HA officielle.

## ÉTAT FINAL

Le code v4, le superviseur unique, les imports canoniques H.264 et le contrat
HA courant sont implémentés ; les états non-v4 rencontrés sont refusés sans
réinitialisation ni migration. Les tests HA passent dans le venv WSL persistant
`$HOME/.venvs/netvue-ha-2026.11.2`. Aucun contact caméra ni déploiement réel
n'a été effectué.
