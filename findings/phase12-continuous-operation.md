# Phase 12 — fonctionnement continu

## Décision et état

**PROUVÉ :** le service Swarm continu de la caméra 2 a démarré, publié du H.264 et fourni des images décodées sur Debian et dans Home Assistant. La première observation a duré environ deux minutes ; elle ne prouve pas une stabilité 24 h/24. Le pont et MediaMTX sont laissés actifs. Une tentative initiale a été consommée ; aucune reconnexion réelle n'a été provoquée ni observée pendant cette fenêtre.

**NON VÉRIFIÉ :** affichage VLC par l'utilisateur, intégration graphique Generic Camera, stabilité prolongée, reconnexion réelle et comportement au redémarrage effectif de Docker ou Debian. Une interruption ambiguë ou un redémarrage peut bloquer le service au lieu de le rétablir automatiquement. Aucune intervention sur Home Assistant, go2rtc, le pare-feu, le firmware ou les autres stacks.

## Architecture et protection

- [continuous.py](../netvue_bridge/continuous.py) ajoute un superviseur distinct du mode borné de [supervisor.py](../netvue_bridge/supervisor.py). Le parent réserve chaque tentative sous verrou, puis lance un travailleur par session ; celui-ci réutilise [stream.py](../netvue_bridge/stream.py) et [rtsp.py](../netvue_bridge/rtsp.py). Un signal de décès du parent arrête le travailleur ; SIGTERM ferme la session, ou le parent force l'arrêt de ce seul travailleur après cinq secondes.
- Le parent reçoit les états et les compteurs vidéo par un tube local. Une absence de progrès ou de publication pendant 30 s provoque un arrêt pour examen ; une absence de vidéo est aussi détectée par la lecture Netvue sans dépendre d'un lecteur RTSP. Toutes les cinq secondes, le parent vérifie la mémoire cgroup v2 ; à 95 % de la limite, il arrête le travailleur et conserve un état ambigu. Les métriques CPU cumulées et mémoire cgroup sont publiées périodiquement. Une erreur de publication H.264/RTSP non récupérable bloque la reprise.
- Les délais après interruption récupérable sont de 30, 120, 300 secondes, puis croissants jusqu'à 1 800 secondes, avec variation positive jusqu'à 20 % et plafond de 1 800 s. Le délai et la réservation sont persistants. Authentification refusée et erreurs non récupérables bloquent. Un travailleur dont le résultat est ambigu, un arrêt volontaire pendant une session ou un crash laissent `in_progress`.
- Le budget phase 12 compte les réservations **avant découverte**, donc de façon plus conservatrice que les seules connexions TCP. Maximum : **3 par 24 h glissantes et 8 par 7 jours glissants** ; plafond atteint = état durable `blocked` et intervention nécessaire, sans attente automatique du renouvellement. Les timestamps de quota utilisent `CLOCK_BOOTTIME` et sont comparés à l'horloge réelle ; après changement de boot ou saut d'horloge suspect, la reprise est bloquée. Les tentatives antérieures aux sept jours glissants peuvent être retirées de la liste à la réservation ; le compteur total reste conservé.
- Le verrou `flock`, les écritures atomiques et `fsync` protègent les instances utilisant **le même répertoire**. Une copie du budget, un répertoire alternatif, la restauration d'un ancien instantané ou un autre programme réseau pourraient contourner cette protection : ils sont interdits. Une seule réplique, placement fixe et `restart_policy: none` limitent les relances ; ils ne remplacent pas le budget. Aucune procédure automatisée de réconciliation après reboot ou de déblocage n'est actuellement fournie : ne pas éditer ou supprimer le budget pour reprendre.

## Fichiers et image

Fichiers introduits : [continuous.py](../netvue_bridge/continuous.py), [stack-continuous.yaml](../deployment/stack-continuous.yaml), [test_continuous.py](../tests/test_continuous.py), [docker_continuous_synthetic.py](../tests/docker_continuous_synthetic.py) et [stack-continuous-synthetic.yaml](../tests/stack-continuous-synthetic.yaml). [stream.py](../netvue_bridge/stream.py) ajoute des callbacks facultatifs ; [README.md](../deployment/README.md) documente le nouveau mode. Le mode borné et les anciens fichiers de budget n'ont pas été modifiés.

Image locale testée et utilisée : `sha256:518813f6ccbac6f3be3818fc8abed1184fdbbd9f22c514da09c411391c9d322e`, étiquetée `netvue-bridge:phase12c-179f7438`, architecture amd64, Python 3.13.5 et PyAV 16.1.0. Elle n'a pas été publiée. L'utilisation de son identifiant immuable a été testée dans un service Swarm fictif. La configuration précédente du pont est conservée sur Debian dans `/home/mtx26/netvue-phase12-179f7438/pilot-before-phase12.json`. Les fichiers et journaux synthétiques du dossier de développement sont distincts du budget réel.

## Tests hors caméra

**PROUVÉ :** 50 tests automatisés réussis dans l'image finale avec réseau `none`, y compris changements SPS/PPS et résolution, PTS, blocage vidéo sans lecteur, verrou concurrent, quotas, état incohérent, état après crash, horloge, redémarrage logique, backoff, SIGTERM, blocage du travailleur et limite mémoire. Les tests de temps utilisent une horloge contrôlée ; ils ne simulent pas une coupure réelle de courant.

Une stack Swarm temporaire, limitée à `127.254.9.9` et au port RTSP loopback 28554, a effectué trois sessions fictives et deux reconnexions, avec pauses de **30,3 s** et **135,0 s**, puis s'est arrêtée sur `BUDGET_EXHAUSTED` avec `total=3`, `blocked`. FFprobe a relevé **120 paquets**, PTS croissants sur **11,901 s** ; FFmpeg a décodé **112 images**, avec dimensions **64 × 64 et 128 × 64**. Le pont a signalé un changement SPS, un PPS, une résolution et une réinitialisation PTS. La stack et le conteneur fictifs ont été supprimés ; le port 28554 a été libéré.

Dans cette stack, le pont utilisait réseau host, UID 10001, racine en lecture seule, toutes capacités supprimées, 0,5 CPU et 256 Mio ; échantillon 37,8 Mio / 0,49 % CPU. MediaMTX : 17,51 Mio / 0,39 % CPU, limite 128 Mio / 0,5 CPU.

**Limite observée :** deux exécutions initiales d'un ancien contrôle RTSP synthétique ont échoué avec PTS non monotones et erreurs FFprobe. La cause n'a pas été déterminée. La suite complète a ensuite réussi, puis dix répétitions isolées du contrôle ont réussi ; cela ne démontre pas la disparition de l'intermittence. Le scénario continu distinct ci-dessus a également réussi sur la dernière image.

## Déploiement et observation réelle

**PROUVÉ :** avant activation, l'ancien pont était à 0/0 et aucun de ses conteneurs n'était actif. Les budgets historiques phases 10 et 11 étaient inchangés, chacun à `used=1`, `limit=1`, `in_progress`. Un répertoire neuf phase 12 a été initialisé sans réseau ; le pont a été mis à jour à zéro réplique avant activation, avec son image immuable, ce seul budget et la politique `restart_policy: none`.

Les journaux montrent **1 découverte**, **1 connexion TCP**, **1 authentification réussie** et **1 demande LIVE** pour la caméra 2, sans reconnexion pendant la fenêtre initiale. Budget : `total=1`, une réservation conservée, `status=in_progress`, `stage=STREAMING`. Deux confirmations LIVE ont été reçues pour une seule demande.

FFmpeg Debian a décodé **26 images en 3,02 s**. FFprobe Debian a observé **102 paquets**, **7 images clés**, une durée PTS de **7,993 s**, des PTS strictement croissants, un écart maximal de **0,117 s** et **12,51 fps** calculés. Dans Home Assistant, FFprobe identifie **H.264 1920 × 1080** et FFmpeg décode **39 images en 3,04 s**. Les sorties vidéo ont été dirigées vers `null`.

Les métriques périodiques ont montré la progression de **317 à 1 458 unités vidéo publiées**, **24 à 100 IDR** et **25,339 à 116,610 s** de réception. Elles signalent un changement SPS, aucun PPS, un changement de résolution et une réinitialisation PTS avec raccord estimé à 334 ms, sans erreur de décodage. La preuve du changement PPS est synthétique, pas réelle. Les fenêtres des compteurs et des lecteurs diffèrent et ne permettent pas de mesurer les pertes.

Trois échantillons : pont environ **40,05 Mio / 0,96–1,26 % CPU** ; MediaMTX environ **30,36–31,81 Mio / 0,32–0,40 % CPU**. Ce ne sont ni des pics garantis ni une validation de stabilité longue.

**État vérifié après la fenêtre initiale :** `netvue-pilot_bridge` et `netvue-pilot_mediamtx` à **1/1**, sur le nœud prévu. Pont en réseau host, UID 10001, racine lecture seule, capacités supprimées, 0,5 CPU et 256 Mio ; aucune publication de port Swarm. MediaMTX écoute `127.0.0.1:8554`, go2rtc reste sur `127.0.0.1:18554` et Home Assistant est actif en réseau host. Ce constat est ponctuel.

## Accès, surveillance et reprise

Depuis Windows, pour visualiser le flux pendant que le service fonctionne : `ssh -N -L 8554:127.0.0.1:8554 home`, puis ouvrir `rtsp://127.0.0.1:8554/netvue2` dans VLC avec RTSP/TCP. Prérequis : SSH fonctionnel, port Windows 8554 libre et pont actif. Le tunnel permet l'accès au flux depuis le PC et doit être fermé lorsqu'il n'est plus nécessaire. Il ne crée pas de nouvelle session caméra.

Avec accès SSH et Docker, `docker stack services netvue-pilot`, `docker service ps netvue-pilot_bridge`, `docker service logs --tail 100 netvue-pilot_bridge` et `docker stats --no-stream <conteneur>` diagnostiquent les services sans tenter de connexion caméra. Ils peuvent révéler des informations d'infrastructure ; expurger les journaux avant partage. Le budget réel est dans `/home/mtx26/netvue-phase12-179f7438/real-state` ; le lire uniquement, sans le copier sur un chemin actif ni le modifier.

Pour arrêter le pont après décision de l'utilisateur : `docker service scale --detach=true netvue-pilot_bridge=0`. Cette commande interrompt la vidéo et laisse une tentative active `in_progress` ; conserver les journaux avant réduction, car les tâches et leurs journaux peuvent disparaître. Elle ne modifie pas MediaMTX.

En cas d'arrêt, plafond, crash, changement d'horloge ou reboot : inspecter les journaux, confirmer l'absence d'ancienne session et préserver tous les budgets. Ne pas relancer ou réinitialiser manuellement le budget. Une réconciliation sûre et auditée des tentatives restantes doit être développée et validée avant toute reprise ; obtenir une autorisation supplémentaire si elle dépasse les limites de phase 12. La configuration Portainer peut différer de la mise à jour Swarm directe : ne pas redéployer une ancienne définition de stack. Voir [README.md](../deployment/README.md).

Home Assistant peut lire le RTSP loopback en TCP ; son interface Generic Camera et Stream ne sont pas configurées ni testées. La modification de Home Assistant nécessite une autorisation distincte. RTSP et transport Netvue local ne sont pas chiffrés ; les processus locaux et conteneurs host peuvent accéder au flux. L'enregistrement MediaMTX reste désactivé ; swap, dumps et mécanismes de Home Assistant peuvent néanmoins conserver des données.
