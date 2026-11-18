# Phase 6 — stabilité RTSP

## Résultat et périmètre

**PROUVÉ — hors ligne :** la suite Debian finale réussit (19 tests), ainsi qu'un essai synthétique borné à 30 secondes avec changement SPS, PPS et résolution. FFprobe et FFmpeg lisent ce flux synthétique sur Debian et dans le conteneur Home Assistant. La durée entre première et dernière unité vidéo synthétique est **29,060 s**, et non 30 s ; la durée du processus est 30,160 s.

**NON VÉRIFIÉ — caméra :** aucune tentative d'authentification ni connexion réelle n'a été effectuée en phase 6 : les restrictions applicables à cette session interdisent le contact direct avec les caméras. Le budget accordé par le propriétaire pour cette phase n'a donc pas été consommé. Les lecteurs, mesures de performance, durée vidéo et erreurs réels de phase 6 ne sont pas disponibles. L'objectif d'au moins 30 secondes de vidéo réelle n'est pas atteint.

## Diagnostic des phases précédentes

- **Observé :** en phase 5, le premier pont rejetait systématiquement un changement SPS/PPS. Le second a échoué avec un message regroupant absence d'image décodée, B-frame ou changement de résolution. Ses sorties partielles de lecteurs n'ont pas été conservées. Voir le [rapport phase 5](./phase5-real-rtsp.md).
- **Non vérifié :** la cause exacte du second échec réel, notamment la présence de B-frames, une résolution modifiée, une IDR manquante ou un assemblage inadéquat. Les tests synthétiques ne prouvent pas laquelle s'est produite.
- Le [code officiel MediaMTX 1.21.4](https://github.com/bluenviron/mediamtx/blob/v1.21.4/internal/stream/format_updater.go) met à jour les paramètres H.264 à partir des unités reçues ; il s'agit d'une observation de code, pas d'une preuve de compatibilité de tous les lecteurs avec tout changement de résolution.

## Corrections

- [rtsp.py](../netvue_bridge/rtsp.py) attend une IDR après changement SPS/PPS, ajoute les paramètres, décode pour validation et remuxe sans transcodage. Le tampon d'attente est limité à 4 Mio / 128 unités et l'attente à 5 s ; les erreurs de décodage récupérables pendant une modification sont limitées à trois. Les changements et durées sont comptés sans exposer de vidéo.
- Les dimensions mises à jour sont comptées après décodage. La sortie conserve néanmoins le contexte RTSP initial : la compatibilité des autres lecteurs avec les changements de résolution doit être vérifiée séparément.
- Les horodatages du protocole sont interprétés en millisecondes d'après l'analyse Android et les essais antérieurs ; les doublons, retours arrière et sauts de plus de 10 s sont rejetés distinctement. Un rebouclage 32 bits est testé.
- Les B-frames détectées restent rejetées : ici PTS = DTS et aucun réordonnancement n'est calculé. Ce rejet, plutôt qu'une prise en charge complète des B-frames, évite de publier de faux horodatages. La détection repose sur le décodeur utilisé au démarrage ou à la reconfiguration, pas sur une surveillance permanente de toutes les images.
- [stream.py](../netvue_bridge/stream.py) distingue notamment confirmation LIVE manquante, absence vidéo, SPS, PPS ou IDR manquants et changement non résolu. [check_rtsp.py](../netvue_bridge/check_rtsp.py) récupère séparément les diagnostics partiels des lecteurs et les métriques même après arrêt prématuré.

## Tests synthétiques

Les tests dans [test_rtsp_bridge.py](../tests/test_rtsp_bridge.py) couvrent les paramètres constants, SPS et PPS modifiés, changement de résolution et nouvelle IDR, IDR absente, horodatages irréguliers, erreur de décodage récupérable, B-frames rejetées, interruption et fermeture. **19 tests ont réussi** sur Debian, outils multimédia inclus.

| Mesure | Flux synthétique avec reconfiguration |
|---|---|
| Entrée et publication | 290 paquets vidéo / 290 unités publiées ; 29 IDR |
| Changements | 1 SPS, 1 PPS, 1 résolution, 1 mise à jour ; 0 erreur de décodage détectée |
| Durée vidéo | 29,060 s entre première et dernière unité reçue ; 28 900 ms selon PTS |
| FFprobe Debian | H.264, dimensions initiales 64 × 64, 201 paquets, 20 images clés, 200 PTS ; 10 images/s selon les PTS, écart maximal 0,1 s |
| FFprobe Home Assistant | H.264, dimensions initiales 64 × 64, 201 paquets, 20 images clés ; aucune ligne d'erreur |
| FFmpeg Debian / Home Assistant | 193 images décodées par chacun, aucune ligne d'erreur |
| Dimensions décodées séparément | 64 × 64 et 128 × 64 |

Chaque lecteur s'est connecté et a terminé avec un code de retour nul. La fenêtre de lecture était de 20 s après 3 s sans lecteur ; ce résultat ne démontre pas 30 s de lecture continue. Les dimensions initiales de flux annoncées par FFprobe ne prouvent pas à elles seules la prise en charge du changement ; elles ont été vérifiées sur les images synthétiques décodées.

Un autre test a omis une image synthétique non IDR : 89 unités publiées, 30 paquets FFprobe et 22 images décodées par chacun des deux FFmpeg, sans ligne d'erreur ; écart PTS maximal 0,2 s. Cela ne garantit pas la récupération de toute perte ou corruption. Un test d'interruption synthétique distinct a vérifié la conservation des résultats partiels des quatre lecteurs, même lorsque la lecture est interrompue.

Sur l'essai synthétique long, les échantillons `/proc` indiquent environ **0,6 % CPU et 48 356 Kio RSS** pour le pont, **0,5 % CPU et 45 520 Kio RSS** pour MediaMTX. Ce sont des maxima RSS échantillonnés et des CPU approximatifs sur la fenêtre ; les processus `docker exec` ne mesurent pas les ressources des lecteurs dans le conteneur. Aucune mesure de latence de bout en bout n'a été effectuée.

## Home Assistant et déploiement

Le conteneur Home Assistant est en réseau `host`. La configuration [mediamtx.local.yml](../netvue_bridge/mediamtx.local.yml) utilise RTSP/TCP sur la boucle locale, chemin `netvue2` ; les autres protocoles et l'API sont désactivés. L'accès synthétique depuis le conteneur est prouvé, pas l'intégration Generic Camera avec une caméra réelle. La [documentation officielle Generic Camera](https://www.home-assistant.io/integrations/generic/) prévoit une source RTSP. Après validation réelle et autorisation de déploiement, envisager `rtsp://127.0.0.1:8554/netvue2`, transport TCP, puis un service supervisé avec budget de reconnexion et surveillance ; aucune configuration Home Assistant ni aucun service permanent n'a été modifié.

La boucle locale n'isole pas les utilisateurs locaux et n'empêche pas Home Assistant de rendre une image accessible aux utilisateurs autorisés à distance. Le transport caméra utilisé ici n'est pas chiffré par le pont. L'audio, la stabilité longue, les reconnexions réelles et la seconde caméra ne sont pas validés. Les outils portables et le venv sont dans un répertoire temporaire Debian susceptible d'être nettoyé par le système.

## Commandes et reproductibilité

Prérequis des tests : `ssh home`, environnement isolé Debian `/tmp/netvue-phase3.YUfIYT`, PyAV 16.1.0, MediaMTX 1.21.4, FFprobe/FFmpeg n8.1.3, ports de boucle locale nécessaires libres ; pour `NETVUE_TEST_HA=1`, conteneur Home Assistant en réseau host avec FFprobe et FFmpeg. Ces commandes produisent uniquement des métriques et flux synthétiques ; elles démarrent temporairement MediaMTX et des lecteurs locaux et ne contactent pas de caméra. Elles ne modifient pas de service existant.

```sh
cd /tmp/netvue-phase3.YUfIYT
NETVUE_MEDIAMTX=tools/mediamtx-1.21.4/mediamtx \
NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe \
tools/venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tests -p 'test*bridge.py' -v

NETVUE_MEDIAMTX=tools/mediamtx-1.21.4/mediamtx \
NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe NETVUE_TEST_DURATION=30 \
NETVUE_TEST_HA=1 NETVUE_TEST_RECONFIG=1 NETVUE_TEST_RESIZE=1 NETVUE_TEST_PPS=1 \
tools/venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tests -p test_rtsp_bridge.py -k bounded_check -v
```

Le même test ciblé a aussi été exécuté séparément avec `NETVUE_TEST_DROP=1 NETVUE_TEST_HA=1`, puis avec `NETVUE_TEST_INTERRUPT=1 NETVUE_TEST_HA=1` à la place des options de reconfiguration. La disponibilité du port 8554, le mode réseau et les versions ont été contrôlés sans contacter les caméras. Une première commande d'inspection a échoué sur des guillemets, puis une seconde a affiché les versions et le mode `host`; son dernier contrôle a échoué à cause des fins de ligne, mais la présence du fichier a été confirmée séparément. Après les tests, aucun écouteur n'a été signalé sur 8554 et aucun processus de test correspondant n'a été trouvé ; la commande de contrôle a également affiché une erreur de fin de ligne après ces résultats.

## Bilan

- **Prouvé :** les tests synthétiques et la lecture synthétique depuis Home Assistant réussissent dans les limites mesurées ci-dessus ; aucune tentative réelle de phase 6.
- **Probable :** l'attente d'IDR et la mise à jour SPS/PPS peuvent corriger certains changements ; leur effet sur la caméra réelle reste inconnu.
- **Prochaine action sûre :** ne pas contacter la caméra dans cette session. Une validation réelle reste nécessaire dans un contexte où le contact est permis, avec compteur d'authentifications, collecte des résultats partiels et mesure séparée de la durée vidéo. Le plafond de 30 s inclut découverte et login : il peut être incompatible avec l'exigence d'au moins 30 s entre première et dernière unité vidéo ; clarifier cette durée avant de conclure.
