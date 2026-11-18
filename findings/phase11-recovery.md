# Phase 11 — diagnostic et reprise bornée

## Diagnostic

- **PROUVÉ :** le budget de phase 10 est `used=1`, `limit=1`, `status=in_progress`. Son empreinte SHA-256 est restée identique au cours de la reproduction et de la préparation. La session réelle phase 10 a été arrêtée ; le pont était à zéro réplique avant l'essai phase 11.
- **PROUVÉ :** dans un conteneur isolé `--network none`, l'image phase 9 avec ce même budget quitte avec le code 1 avant découverte, sans modifier le budget. **DÉDUIT :** cet état explique très probablement l'échec manuel de l'utilisateur. **NON VÉRIFIÉ :** les tâches et leurs journaux historiques ayant disparu, la cause exacte de ce démarrage et un éventuel contact caméra ne sont pas directement observables.
- **PROUVÉ :** le pilote utilise `json-file` et Python non bufferisé. Sur une stack synthétique, les messages existaient avant la réduction du pont à zéro ; ensuite `docker service ps` et `docker service logs` étaient vides. **DÉDUIT :** la suppression des tâches lors de la réduction explique probablement les journaux historiques vides, sans preuve directe de leur contenu perdu. Recueillir les journaux expurgés avant réduction à zéro.

## Corrections et tests hors caméra

[supervisor.py](../netvue_bridge/supervisor.py) distingue désormais `BUDGET_IN_PROGRESS`, `BUDGET_BLOCKED` et `BUDGET_EXHAUSTED` et annonce le lancement du travailleur. [test_supervisor.py](../tests/test_supervisor.py) teste ces messages et l'absence de découverte pour ces états. Le format, les verrous et les décisions du budget n'ont pas changé. La configuration antérieure du service a été conservée sur Debian dans `/home/mtx26/netvue-phase11-179f7438/service-before.json`.

**PROUVÉ :** 10 tests ciblés du superviseur et 37 tests complets réussissent ; les seconds ont été exécutés dans l'image en réseau `none`. Image locale `netvue-bridge:phase11-179f7438`, ID `sha256:79d0c5ac34ce03baa96fc860a1677059a4a796d2c19a4487a8f3e5f80b7fa417`, non publiée.

Une stack Swarm temporaire a utilisé une caméra fictive exclusivement loopback et MediaMTX sur `127.0.0.1:28554`. Le pont avait réseau host, UID 10001, racine en lecture seule, toutes capacités supprimées, 0,5 CPU et 256 Mio ; MediaMTX avait 0,5 CPU et 128 Mio. La chaîne fictive a publié 220 images : 160 paquets horodatés, 152 images décodées, résolutions 64 × 64 et 128 × 64, intervalle PTS 15,902 s, écart maximal 0,102 s. Les métriques du pont indiquent un changement SPS, un PPS, un changement de résolution et une réinitialisation PTS, sans erreur de décodage. Le budget fictif a été consommé une seule fois ; l'interruption a provoqué l'arrêt sur épuisement, sans reconnexion. Échantillon instantané : pont 37,93 Mio / 0,90 % CPU ; MediaMTX 17,54 Mio / 0,31 % CPU. La stack et le conteneur fictifs ont été supprimés ; les ports 8554 et 18554 préexistants ont été préservés.

## Essai réel autorisé

**PROUVÉ :** le budget phase 11 distinct a été créé sans réseau, sans toucher à celui de phase 10 : `limit=1`, `used=0`, `ready` avant activation. Le pilote a été mis à jour à zéro réplique avec la nouvelle image, ce nouveau montage et `--max-duration 600` ; `restart_policy=none`. Le watchdog est par exécution, avec arrêt anticipé jusqu'à 5 s avant l'échéance ; il ne constitue pas une échéance globale persistante.

Les journaux montrent une réservation, **une** découverte, **une** connexion TCP, **une** demande d'authentification réussie (`result_code=0`) et **une** demande LIVE réussie pour la seule caméra 2. Aucune reconnexion n'est autorisée. Le nouveau budget indique `used=1`, `limit=1`, `in_progress`. La publication RTSP a commencé ; les deux confirmations LIVE observées ne sont pas deux demandes.

**PROUVÉ :** FFmpeg sur Debian a décodé 33 images en 3,04 s. FFprobe Debian a relevé 101 paquets, 7 images clés, 7,922 s de PTS strictement croissants et un écart maximal de 0,090 s. Dans le conteneur Home Assistant, FFprobe identifie H.264 Main en 1920 × 1080 et FFmpeg décode 38 images en 3,04 s. Un raccord de PTS a été signalé par le pont. Échantillon : pont 40,6 Mio / 1,46 % CPU et MediaMTX 27,41 Mio / 0,80 % CPU. Les lecteurs écrivent vers `null` ; ces mesures courtes ne démontrent ni les pics ni la stabilité 24 h/24.

**PROUVÉ :** le watchdog s'est déclenché ; le travailleur s'est arrêté, puis la tâche s'est terminée avec le code **124**, sans OOM, après environ **595,6 s** depuis le démarrage du conteneur, sous la limite de 600 s. Les journaux filtrés ont été conservés sur Debian dans `/home/mtx26/netvue-phase11-179f7438/phase11-safe-log.txt` avant la réduction du pont à zéro. Le pont rapporte **7 427 unités vidéo reçues et images publiées**, dont **497 IDR**, sur **594,037 s** de réception ; un changement SPS, aucun PPS, un changement de résolution, une réinitialisation PTS avec raccord de 273 ms, et aucune erreur de décodage. Ces compteurs couvrent des fenêtres différentes de celles des lecteurs ; ils ne permettent pas de quantifier les pertes.

Seize échantillons pendant la surveillance montrent environ **40,59–40,85 Mio / 1,03–1,85 % CPU** pour le pont et **27,24–29,79 Mio / 0,33–0,99 % CPU** pour MediaMTX. Ce ne sont pas des pics garantis.

**État final vérifié :** `netvue-pilot_bridge` à **0/0**, `netvue-pilot_mediamtx` à **1/1**. Le flux réel n'est plus disponible. Le budget phase 11 est `used=1`, `limit=1`, `in_progress` ; celui de phase 10 reste intact. Le conteneur Home Assistant reste actif en réseau host, les ports préexistants 8554 et 18554 restent sur loopback et le port synthétique 28554 a été libéré. Aucune reconnexion n'a été effectuée.

**NON VÉRIFIÉ :** visualisation VLC par l'utilisateur, affichage Generic Camera, fonctionnement effectif de Stream et comportement permanent. Le changement PPS réel n'a pas été observé pendant cet essai ; il a été exercé par le test synthétique.

## Accès et sécurité

Pendant une session active autorisée uniquement, depuis PowerShell Windows, `ssh -N -L 8554:127.0.0.1:8554 home` crée un tunnel vers le RTSP loopback Debian ; prérequis : SSH fonctionnel et port local 8554 libre. VLC peut ensuite ouvrir `rtsp://127.0.0.1:8554/netvue2` en TCP. Le tunnel reste actif jusqu'à interruption ; il permet à des processus locaux d'accéder au flux, donc ne le laisser ouvert que pendant l'essai. Aucune nouvelle authentification caméra n'est nécessaire pour un lecteur supplémentaire. Après expiration du watchdog le flux n'est plus disponible.

Plus tard, après autorisation de modification de Home Assistant, configurer Generic Camera avec `rtsp://127.0.0.1:8554/netvue2`, transport TCP ; vérifier Stream et l'aperçu dans l'interface. Aucun changement Home Assistant n'a été effectué. RTSP et transport Netvue local ne sont pas chiffrés ; les processus locaux et conteneurs en réseau host peuvent lire le flux. Aucun port Swarm n'est publié et MediaMTX écoute uniquement sur loopback. Les tests ne constituent pas une preuve d'absence de rétention par le swap, les dumps ou Home Assistant.

## Exploitation et suite

Pour diagnostiquer sans connexion caméra, avec accès SSH et Docker : `docker stack services netvue-pilot`, `docker service ps netvue-pilot_bridge` et `docker service logs --tail 100 netvue-pilot_bridge`. Ces commandes produisent état et journaux potentiellement sensibles : expurger avant partage. Pour arrêter le seul pont, `docker service scale --detach=true netvue-pilot_bridge=0` interrompt une session active et peut laisser son budget `in_progress` ; conserver les journaux avant cette opération. Ne pas arrêter les autres services.

Pour une reprise, préserver les budgets et journaux, obtenir une nouvelle autorisation précisant caméra, opérations, nombre de tentatives et durée, confirmer l'arrêt de toute ancienne session et préparer un budget séparé sans modifier la protection ; suivre [README.md](../deployment/README.md). Ne jamais répéter automatiquement cette tentative ni restaurer un ancien budget sur le chemin actif.

Le fonctionnement 24 h/24 n'est pas activé. Il reste à définir et tester une politique avec budget durable, backoff borné, échéance persistante, alertes et arrêt sur état ambigu ou refus, ainsi que l'observation des pics, pertes Wi-Fi, redémarrages et réinitialisations de PTS. La validation longue, les essais de reconnexion réels, la mise en production et la modification Home Assistant nécessitent des autorisations distinctes.
