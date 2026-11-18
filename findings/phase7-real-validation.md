# Phase 7 — validation réelle et stabilisation RTSP

> **Rectification de phase 8 :** MediaMTX 1.21.4 active MoQ par défaut. La configuration utilisée en phase 7 ne le désactivait pas explicitement ; l'affirmation que seul RTSP écoutait n'était donc pas démontrée. Les fichiers `auto.crt` et `auto.key` ont été observés dans le répertoire temporaire après les essais. L'exposition effective des ports MoQ ou leur accessibilité externe pendant ces essais n'a pas été mesurée. La configuration locale a depuis été corrigée avec `moq: false` ; aucun nouveau contact caméra n'a été effectué.

## Conclusion

**PROUVÉ :** lors du second essai autorisé sur la caméra 2, le flux réel H.264 a été reçu, remuxé et décodé sur Debian et dans le conteneur Home Assistant. FFprobe a observé **20,978 s** entre le premier et le dernier PTS disponibles, strictement croissants, avec un écart maximal de **0,322 s**. Les deux FFmpeg ont chacun décodé **161 images** sur une durée de sortie de **21 s**, sans ligne d'erreur. Le flux d'entrée a couvert **26,644 s** entre première et dernière unité reçue. Le test complet, nettoyage compris, a duré **27,584 s**.

**Limite importante :** une réinitialisation PTS à zéro a été corrigée en estimant l'écart de raccord à partir du temps d'arrivée. La continuité mesurée est donc celle du flux RTSP reconstruit, pas une preuve d'exactitude de tous les horodatages natifs ni de l'absence de toute image perdue. Cette validation courte ne démontre pas la stabilité prolongée.

**Budget de phase :** deux découvertes UDP, deux connexions TCP, deux demandes de login et deux demandes LIVE utilisées sur deux autorisées. Les deux confirmations LIVE reçues pendant chacun des essais ne sont pas deux demandes. Aucune reconnexion automatique ni troisième tentative. Aucun contact avec la caméra 1.

## Vérifications préalables et environnement

- Rapports des [phases 3](./phase3-live-connection.md), [4](./phase4-rtsp-bridge.md), [5](./phase5-real-rtsp.md) et [6](./phase6-rtsp-stability.md), sources et tests examinés. Les deux tentatives non utilisées d'une autorisation antérieure ont été remplacées par le budget explicite de cette phase.
- Debian : Python 3.13.5, PyAV 16.1.0 conforme à [requirements.txt](../netvue_bridge/requirements.txt), MediaMTX 1.21.4 et FFmpeg/FFprobe portables n8.1.3. Conteneur Home Assistant en réseau `host` : FFmpeg/FFprobe 8.1.2 et `timeout` disponibles. Les sources corrigées ont été synchronisées et leurs empreintes comparées dans un répertoire privé temporaire `/tmp/netvue-phase7.vIUhl6` ; les outils et le venv existants sont référencés depuis l'environnement temporaire de phase 3. Ces répertoires peuvent être supprimés par le système.
- Avant les essais : port 8554 libre, aucun processus MediaMTX ou pont de test détecté. Après chaque essai, puis à la fin : aucun processus de pont ou MediaMTX, lecteur Home Assistant de test ou écouteur RTSP 8554 détecté ; Home Assistant était toujours en réseau `host`. Aucun service existant n'a été arrêté, aucun fichier de configuration Home Assistant ni conteneur modifié.
- **24 tests réussis** sur Debian et Windows avec les dépendances multimédia, sans test ignoré. Une course dans un nouveau test de processus a d'abord échoué sur Debian ; son synchronisme a été corrigé puis toute la suite a réussi.
- Reproduction synthétique avec 27 s de session, 21 s de lecture, double confirmation LIVE, changement SPS/PPS et de résolution, IDR et remise à zéro du PTS : **260 unités publiées**, un changement SPS, un PPS, une résolution et une réinitialisation PTS. FFprobe Debian et Home Assistant : **20,903 s** de PTS, écart maximal **0,103 s** ; les deux FFmpeg : **203 images** chacun. Les images synthétiques décodées ont eu les deux résolutions attendues. Un autre test d'interruption a vérifié la récupération des résultats des quatre lecteurs.

## Essais réels

Les essais ont utilisé [check_rtsp.py](../netvue_bridge/check_rtsp.py), une session limitée à 27 s, un garde-fou de processus à 28,5 s, une fenêtre lecteur de 21 s et `--max-attempts 1` transmis au pont. La publication RTSP/TCP était limitée à la boucle locale par [mediamtx.local.yml](../netvue_bridge/mediamtx.local.yml). Les images et données vidéo n'ont pas été enregistrées volontairement sur disque ; la mémoire peut toutefois être exposée au swap ou aux dumps système.

| Mesure | Essai 1 | Essai 2 |
|---|---:|---:|
| Découverte / TCP / login / demande LIVE | 1 / 1 / 1 / 1 | 1 / 1 / 1 / 1 |
| Résultat login / LIVE | 0 / 0 | 0 / 0 |
| Durée découverte / TCP / authentification | 0,019 / 0,004 / 0,031 s | 0,009 / 0,006 / 0,034 s |
| Première unité vidéo, depuis début de session | 0,293 s | 0,306 s |
| Publication prête, depuis lancement du pont | 0,403 s | 0,505 s |
| Durée entre première et dernière unité reçue | 11,128 s | 26,644 s |
| Durée du processus pont / contrôle total | 11,549 / 12,088 s | 27,138 / 27,584 s |
| Unités reçues / publiées | 92 / 91 | 222 / 222 |
| IDR reçues / publiées | 8 / 7 | 17 / 17 |
| Changements SPS / PPS / résolution | 1 / 0 / 0 | 1 / 0 / 1 |
| Mises à jour de paramètres / erreurs de décodage | 0 / 0 | 1 / 0 |
| Premier / dernier PTS brut reçu | 0 / 0 | 0 / 15 598 |
| Durée PTS reconstruite du pont | 10 808 ms avant erreur | 26 725 ms, raccord estimé inclus |
| Résultat | Arrêt : retour arrière du PTS | Succès du contrôle |

La première unité reçue n'est pas nécessairement une image décodée ; le délai de publication prête inclut l'initialisation du processus et du décodeur. Les PTS bruts de l'essai 2 appartiennent à deux époques après remise à zéro et ne peuvent pas être soustraits directement.

### Lecteurs RTSP

| Mesure | Essai 1 | Essai 2 |
|---|---:|---:|
| FFprobe Debian : codec / dimensions initiales | H.264 / 640 × 360 | H.264 / 640 × 360 |
| FFprobe Debian : paquets / images clés | 91 / 7 | 175 / 13 |
| FFprobe Debian : premier / dernier PTS disponible | 0,124 / 10,808 s | 0,121 / 21,099 s |
| FFprobe Debian : durée PTS / écart maximal / FPS selon PTS | 10,684 s / 0,136 s / 8,33 | 20,978 s / 0,322 s / 8,25 |
| FFmpeg Debian : images / durée de sortie | 91 / 10,8 s | 161 / 21 s |
| FFprobe Home Assistant : paquets / images clés | 89 / 6 | 175 / 13 |
| FFprobe Home Assistant : premier / dernier PTS disponible | 0,321022 / 10,758022 s | 0,121 / 21,099 s |
| FFprobe Home Assistant : durée PTS / écart maximal / FPS selon PTS | 10,437 s / 0,136 s / 8,34 | 20,978 s / 0,322 s / 8,25 |
| FFmpeg Home Assistant : images / durée de sortie | 76 / 10,56 s | 161 / 21 s |

Tous ces lecteurs ont retourné le code 0 et aucune ligne d'erreur, mais leur code seul n'aurait pas suffi à déclarer le premier essai réussi : le pont s'est arrêté prématurément. Pour l'essai 2, 174 des 175 paquets FFprobe Debian avaient un PTS ; les PTS disponibles étaient strictement croissants. Un FFprobe supplémentaire sur Debian a observé des images décodées **640 × 360 et 1920 × 1080**. Les lecteurs Home Assistant ont décodé le flux réel, mais leurs dimensions par image après changement n'ont pas été mesurées séparément.

### Ressources Debian

Échantillons `/proc` approximatifs, pourcentage CPU rapporté à la fenêtre de mesure et pic RSS observé par processus ; les mémoires partagées ne doivent pas être additionnées.

| Processus | Essai 1 CPU / RSS max | Essai 2 CPU / RSS max |
|---|---:|---:|
| Pont Python/PyAV | 1,0 % / 50 228 Kio | 0,9 % / 51 284 Kio |
| MediaMTX | 0,8 % / 45 248 Kio | 0,9 % / 54 284 Kio |

Les métriques des processus `docker exec` ne mesurent pas les ressources des décodeurs exécutés dans le conteneur. Les zéros issus de `/proc` sous Windows ne constituent pas des mesures.

## Diagnostic et corrections

- **Observé :** au premier essai, une seconde confirmation LIVE `0` a précédé l'erreur `Video timestamp moved backwards` ; un changement SPS était en cours et le dernier PTS brut reçu était revenu à `0`. Les compteurs indiquent une IDR reçue de plus que publiée. Aucun diagnostic ne prouve pourquoi le firmware a remis son horloge à zéro.
- **Correction puis preuve réelle limitée :** [stream.py](../netvue_bridge/stream.py) signale une confirmation LIVE répétée après début de publication. [rtsp.py](../netvue_bridge/rtsp.py) n'accepte un PTS ramené à zéro que sur une IDR pendant un changement SPS/PPS avec cette confirmation ; le raccord monotone est calculé d'après l'écart entre arrivées, borné à 10 s et rapporté. Les autres retours arrière, doublons et discontinuités restent rejetés. À l'essai 2, **un** retour à zéro a été traité avec un raccord estimé à **322 ms**, puis le flux a continué. Le changement de résolution et la reprise ont été observés sur cette caméra ; leur fonctionnement sur d'autres firmwares n'est pas établi.
- [check_rtsp.py](../netvue_bridge/check_rtsp.py) collecte les sorties et codes des lecteurs après leur arrêt, y compris les objets de paquets JSON complets d'une sortie FFprobe tronquée. Un garde-fou peut terminer le pont si la durée est dépassée ; les lecteurs du conteneur sont eux-mêmes bornés par `timeout`. Une sortie incomplète ou une terminaison forcée ne garantit pas la récupération de toutes les métriques.
- Les tests dans [test_rtsp_bridge.py](../tests/test_rtsp_bridge.py) couvrent notamment ce retour à zéro, le rejet sans confirmation ou sans changement, les autres retours arrière, les B-frames détectées, l'absence d'IDR, la récupération après erreur de décodage, les sorties partielles et l'arrêt des lecteurs. La détection des B-frames reste limitée aux phases de validation du décodeur, pas à une surveillance permanente.

## Reproductibilité et risques

Les commandes réelles ne doivent **pas** être rejouées : le budget est épuisé. Pour les essais effectués, les variables ci-dessous représentent les adresses LAN fournies par le propriétaire ; les chemins et exécutables sont ceux du répertoire temporaire Debian.

- Suite hors ligne : `NETVUE_MEDIAMTX=tools/mediamtx-1.21.4/mediamtx NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe tools/venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tests -p 'test*bridge.py' -v`. Elle utilise une caméra synthétique et des sockets loopback temporaires ; les ports doivent être libres.
- Pour la reproduction synthétique longue, ajouter `NETVUE_TEST_DURATION=27 NETVUE_TEST_PROBE_DURATION=21 NETVUE_TEST_BRIDGE_TIMEOUT=28.5 NETVUE_TEST_HA=1 NETVUE_TEST_RECONFIG=1 NETVUE_TEST_RESIZE=1 NETVUE_TEST_PPS=1 NETVUE_TEST_PTS_RESET=1` et cibler `test_rtsp_bridge.py -k bounded_check`. Elle lance temporairement des lecteurs dans Home Assistant et nécessite leur autorisation et `timeout` ; aucune caméra réelle n'est contactée.
- Chaque essai réel a lancé une seule fois `tools/venv/bin/python -B -m netvue_bridge.check_rtsp --source SOURCE_LAN --target CAMERA_2 --mediamtx tools/mediamtx-1.21.4/mediamtx --ffprobe tools/ffmpeg-btbn/ffprobe --ffmpeg tools/ffmpeg-btbn/ffmpeg --duration 27 --bridge-timeout 28.5 --probe-duration 21 --reader-delay 0 --home-assistant-check --frame-probe`. La commande ouvre une session caméra, reçoit une vidéo privée et démarre des lecteurs ; elle requiert une nouvelle autorisation et un nouveau budget pour toute utilisation future.

## Limites et plan de déploiement proposé

- **NON VÉRIFIÉ :** stabilité au-delà de cette session courte, reconnexions réelles, audio, caméra 1, horodatage exact au raccord, prise en charge complète des B-frames et affichage dans Generic Camera. La lecture depuis le conteneur ne prouve pas l'intégration dans l'interface Home Assistant.
- **PROBABLE, à valider avant déploiement :** deux conteneurs Docker supervisés, le pont Python et MediaMTX, en réseau `host` sur le serveur ; configuration MediaMTX RTSP/TCP liée à `127.0.0.1:8554`, autres protocoles désactivés, et Generic Camera utilisant cette source locale. Le réseau host facilite la découverte LAN et l'accès de Home Assistant mais ne fournit pas d'isolation entre utilisateurs locaux. Home Assistant peut également rendre le flux visible à ses utilisateurs distants.
- Remuxer sans transcodage, séparer les processus, lancer avec privilèges minimaux, système de fichiers en lecture seule lorsque possible, sans port publié sur Internet ni vidéo persistée. Ne journaliser que les métriques expurgées ; aucun secret dans les arguments ou journaux.
- Prévoir un superviseur avec attente progressive, plafond global de reconnexions persistant entre redémarrages et alerte explicite. Le `--max-attempts` actuel est **par exécution** : un redémarrage Docker automatique non coordonné pourrait contourner un budget global. La politique de reconnexion réelle reste à concevoir et tester ; ne pas activer de redémarrage permanent avant accord.
- Limites initiales **à expérimenter**, non garanties par ces essais courts : par exemple 0,5 cœur et 256 Mio pour le pont, 0,5 cœur et 128 Mio pour MediaMTX, avec surveillance et ajustement après mesure des pics. Les décodeurs Home Assistant ne sont pas compris dans ces plafonds.
- La piste API/relais cloud figure dans [phase1-report.md](./phase1-report.md) ; aucun compte cloud n'a été utilisé ici et l'indépendance totale au cloud reste non vérifiée.

**Prochaine étape :** présenter ce plan et obtenir une autorisation distincte pour tout déploiement permanent et tout nouveau budget de tests caméra. Aucun déploiement ni intégration permanente n'a été effectué.
