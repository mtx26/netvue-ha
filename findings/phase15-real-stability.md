# Phase 15 — stabilité et reprise

## Verdict : BLOQUÉ

**PROUVÉ :** aucun test réel n'a été lancé pendant cette phase : zéro découverte,
connexion, authentification ou demande LIVE vers la caméra. Le budget réel n'a
pas été modifié. L'autorisation spécifique de phase 15 reste inutilisée.

**NON VÉRIFIÉ :** la correction des PTS sur cette caméra, la lecture RTSP réelle
avec cette version, son accès depuis Home Assistant et la stabilité 24 h/24.
Les résultats synthétiques ci-dessous ne remplacent pas ces vérifications.

## A. Diagnostic initial

- **PROUVÉ :** sur Debian, le service du pont demande une réplique mais sa
  dernière tâche est en échec : 0 tâche en cours ; politique de redémarrage
  `none`. MediaMTX a une tâche active ; Home Assistant est actif en réseau
  host. Ces constats sont ponctuels, pas un inventaire de tous les clients
  possibles sur le réseau.
- **PROUVÉ :** les journaux historiques de cette tâche contiennent une
  découverte, une connexion, une demande d'authentification réussie, une
  demande LIVE et deux confirmations LIVE ; puis `Duplicate video timestamp`
  et `continuous: non-retryable session failure`. Les métriques finales
  rapportent 9 561 unités vidéo, 9 560 images publiées, 640 IDR et 764,806 s
  entre première et dernière vidéo ; un changement SPS, aucun PPS, un
  changement de résolution, une réinitialisation PTS et zéro erreur de
  décodage signalée. Ces chiffres ne concernent pas un nouveau test.
- **PROUVÉ :** la lecture en montage Docker strictement `readonly` donne un
  budget v3 `blocked/BLOCKED`, `total=1`, une réservation en historique, une
  dans chaque fenêtre 24 h et 7 jours. Même boot, horloges cohérentes selon
  le contrôle existant. L'empreinte du budget est identique avant et après
  les essais. Pas de sauvegarde nouvelle ni de modification du budget.
- **PROUVÉ par lecture de** [continuous.py](../netvue_bridge/continuous.py) :
  une erreur de session non réessayable provoque `block()`. Ensuite
  `supervise_continuous()` refuse tout état non `ready` avant de contacter
  le serveur RTSP ou de réserver. Les quotas sont 3/24 h et 8/7 jours ;
  être sous ces plafonds ne débloque pas l'état. Le fichier `blocked` ne
  conserve pas la cause du blocage.
- **DÉDUIT :** l'erreur vidéo historique explique le blocage observé. Il ne
  s'agit pas d'un refus d'authentification observé ; le code Docker a bien
  conservé une tâche en échec, et le superviseur refuse ensuite la reprise
  conformément à sa protection. La cause exacte des deux PTS identiques
  reste inconnue : les charges et valeurs correspondantes manquent.
- **PROUVÉ :** aucun conteneur utilisant une image du pont n'était en cours
  sur le serveur lors des contrôles. Cela ne démontre ni l'absence de clients
  sur d'autres hôtes ou espaces réseau, ni l'impossibilité d'un démarrage
  concurrent après le contrôle.

## B. Code, réconciliation et limites

Les sources Python de l'image testée correspondent octet pour octet aux 14
fichiers Python et de dépendances de la copie Git actuelle. Dans
[h264.py](../netvue_bridge/h264.py) et [rtsp.py](../netvue_bridge/rtsp.py),
la correction expérimentale attend des images distinctes et une ancre
temporelle, puis interpole sous conditions de cadence et de taille. Les
situations ambiguës restent bloquantes ; aucune image n'est supprimée au
seul motif d'un PTS égal. Les détails et limites figurent dans
[phase12b-timestamp-recovery.md](phase12b-timestamp-recovery.md).

**PROUVÉ :** aucune réconciliation auditée de `blocked` ou d'une réservation
interrompue n'est implémentée. Le test existant de
[test_continuous.py](../tests/test_continuous.py) vérifie que le budget
bloqué refuse la réservation ; un autre modélise une interruption propre
déjà attestée avec `finish_retryable`, sans démontrer la réconciliation
d'un blocage. Les tests existants couvrent notamment verrou concurrent,
crash, quotas, fichier incohérent et horloge ambiguë. Ils ne valident pas
une transaction de réconciliation ni ses interruptions.

### Conception à réaliser, non opérationnelle

1. Identifier le budget canonique et tous les producteurs possibles ;
   neutraliser préalablement les démarrages concurrents de manière autorisée.
   Une attestation couvrant les autres hôtes et clients est nécessaire :
   `flock` ne coordonne que les utilisateurs du même fichier de verrou.
2. Sous ce verrou exclusif, relire et valider strictement l'état, l'identité
   de la caméra, l'historique, le boot et les horloges ; refuser corruption,
   historique incomplet, clôture non prouvée, horloge douteuse ou quota
   atteint. Conserver intégralement les réservations et le total.
3. Créer une sauvegarde hors chemin actif, sans écrasement, synchronisée et
   relue avec vérification d'empreinte. Ne jamais la restaurer comme budget
   actif pour contourner les quotas.
4. Prévoir une transition explicite enregistrant la décision, les preuves
   expurgées, l'autorisation et l'état avant/après dans un audit durable.
   Une transition exceptionnelle après erreur non réessayable ne doit pas
   être présentée comme une interruption réseau réessayable. Elle doit
   respecter le backoff et recalculer les fenêtres glissantes.
5. Concevoir une transaction résistante aux interruptions : intention d'audit
   synchronisée avant mutation, remplacement atomique et `fsync`, puis
   clôture vérifiable ; au redémarrage, tout état incomplet ou incohérent
   bloque avant réseau. Les anciens exécutables doivent également échouer
   avant toute connexion, par exemple avec un format de budget versionné
   qu'ils refusent. Une simple marque externe ignorée par l'ancien code ne
   suffit pas. Ne pas effacer l'historique, même pour réessayer après crash.
6. Tester sur budgets fictifs : disponible, bloqué, épuisé, réservation
   interrompue, concurrence, verrou, corruption, historique incomplet, quotas,
   redémarrage et interruption à chaque étape de synchronisation. Vérifier
   l'audit, la conservation de chaque réservation et le refus avant réseau.
7. Ajouter et tester une exécution bornée utilisant ce même budget avec au
   plus une réservation et sans reconnexion. Le superviseur continu actuel
   boucle après une erreur réessayable ; son watchdog de santé n'est pas le
   watchdog global de 120 s demandé. Le mode borné existant utilise un budget
   v2 distinct : le substituer au budget v3 contournerait le blocage.

**NON VÉRIFIÉ :** cette conception n'a été ni implémentée ni testée comme
transaction ; aucune transition réelle n'est donc autorisée sur cette base.
De plus, les observations du serveur ne prouvent pas l'absence de sessions
sur d'autres machines. Aucun logiciel de production, budget ou configuration
de service n'a été modifié. La sauvegarde réelle conditionnelle, la
réconciliation et le test réel n'ont pas été effectués.

## C. Tests hors ligne

**PROUVÉ :** dans des conteneurs temporaires Docker `--network none`, sans
montage de budget ou de configuration HA réels :

- 73 tests du pont réussis, avec PyAV et les lecteurs portables ; 13 tests
  de l'intégration Home Assistant réussis. Une première exécution du pont
  échouait car le montage de configuration synthétique manquait ; après
  correction du montage, la suite entière réussit. Une première exécution HA
  échouait à cause des permissions de la copie temporaire ; elle réussit
  après correction des permissions.
- Scénario RTSP fictif de 30 s : 290 images publiées ; FFprobe lit
  201 paquets avec PTS croissants, écart maximal 0,102 s ; FFmpeg décode
  192 images sans erreur signalée. Les deux résolutions 64×64 et 128×64,
  les changements SPS/PPS et la réinitialisation PTS sont observés.
- Deux simulations accélérées publient et décodent chacune 20 000 images ;
  FFprobe vérifie 1 999,9 s de PTS simulés strictement croissants. Quatre
  collisions corrigées, un changement SPS, PPS et de résolution, une
  réinitialisation PTS. Durées réelles 9,11 et 9,08 s. Ce ne sont pas
  33 minutes de fonctionnement réel.
- HA fictif : configuration, options, entités, disponibilité RTSP, image
  Stream et déchargement validés. HA isolé signale une erreur
  Zeroconf/analytics liée à l'absence d'interface multicast ; la fin du
  flux volontaire est également journalisée. Cela ne valide pas l'interface
  HA installée.

Mesures synthétiques : le scénario RTSP échantillonne environ 48 996 Kio
RSS et 1,3 % CPU pour le pont, 40 664 Kio et 0,3 % pour MediaMTX. Les deux
simulations accélérées ont une crête cgroup d'environ 138,1–138,3 Mo et
50 % d'un cœur, pour le conteneur entier limité à 512 Mio et 0,5 CPU.
Échantillons et pics cgroup ne sont pas comparables et ne prédisent pas
l'utilisation réelle 1080p.

Reproduction : transférer une copie Git sans données privées et les binaires
MediaMTX, FFmpeg et FFprobe vérifiés sur un hôte Docker. Exécuter les six
modules `test_netvue_bridge`, `test_supervisor`, `test_continuous`,
`test_rtsp_bridge`, `test_timestamp_recovery` et
`test_continuous_timestamp_integration` avec `python -B -W
error::ResourceWarning -m unittest -q`, et le module HA séparément dans une
image HA compatible. Monter tests, configuration fictive et exécutables en
lecture seule, fournir `NETVUE_MEDIAMTX` et `NETVUE_FFPROBE`, utiliser
`--network none`, aucun port publié, aucun budget réel, et limiter CPU/RAM.
Le script [timestamp_long_synthetic.py](../tests/timestamp_long_synthetic.py)
utilise les mêmes lecteurs pour la simulation accélérée. Prérequis : PyAV et
libx264 ; sorties : résultats et métriques synthétiques ; risques : charge
CPU/RAM et fichiers temporaires fictifs.

## D. Image et déploiement

Image distincte `netvue-bridge:phase15-179f7438`, ID immuable
`sha256:411d0134677b706fbfdf2feb84c58fca6695f616be3a889623d0a29988b19027`.
Elle reprend l'image locale de test, y copie les sources actuelles et a été
construite avec le constructeur Docker classique et `--network none` ; PyAV
16.1.0 et `pip check` sont vérifiés. Une première tentative BuildKit a
échoué à résoudre l'image de base par ID ; la seconde a réussi à partir de
son étiquette locale. Cette construction dérivée n'est pas une reconstruction
complète du Dockerfile d'origine.

Le service réel utilise toujours son ancienne image immuable
`sha256:518813f6ccbac6f3be3818fc8abed1184fdbbd9f22c514da09c411391c9d322e`,
UID 10001, racine lecture seule et limites 0,5 CPU/256 Mio. Aucun port
Swarm n'est publié pour le pont. MediaMTX 1.21.4 et les outils portables
ont été vérifiés ; les empreintes des trois outils transférés correspondent
aux fichiers locaux. L'image MediaMTX par étiquette n'étant pas présente, la
vérification de version a déclenché son téléchargement ; aucun service n'a
été modifié. L'image phase 15 reste locale et n'a pas été déployée.

Un éventuel retour arrière devra conserver la spécification initiale et le
budget courant, sans restaurer un ancien budget. Ne pas réactiver
automatiquement l'ancienne spécification : elle pourrait relancer un pont.

## E. État final et suite

**PROUVÉ :** pont 0/1, MediaMTX 1/1, Home Assistant actif lors du dernier
contrôle ; budget inchangé, une tentative conservée et état bloqué. Aucun
flux ou image privés n'ont été enregistrés par les tests. Les seuls
fichiers modifiés dans le projet pour cette phase sont ce rapport ; les
sources, services et budgets installés restent inchangés.

**DÉDUIT :** une réconciliation appliquée maintenant ne satisferait pas les
conditions de sécurité : aucune transaction auditée et testée n'existe, et
les autres clients éventuels ne sont pas identifiés.

**NON VÉRIFIÉ :** absence de sessions sur les autres hôtes, arrêt futur
concurrent, véritable cause du doublon H.264, lecture réelle et affichage HA.
L'absence de données de test réelles ne constitue pas un échec vidéo.

**PROCHAINE ÉTAPE :** implémenter et tester hors ligne la transaction et le
mode à tentative unique décrits ci-dessus, puis obtenir les éléments
permettant de confirmer et empêcher les accès concurrents sur l'ensemble des
hôtes concernés. Ne modifier le budget et ne lancer l'unique session qu'une
fois toutes les conditions explicitement établies. La phase 16 et le
fonctionnement permanent ne sont pas déployés.
