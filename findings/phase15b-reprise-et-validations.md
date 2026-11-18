# Phase 15B — reprise et validations hors ligne

## Résultat

**Reprise v4 : implémentée et testée hors ligne.** Après un redémarrage normal,
un état `in_progress` n'est récupéré qu'après contrôle du boot/de l'horloge et
vérification qu'aucune ancienne session connue ne tourne pour la caméra. La
tentative interrompue redevient réessayable avec backoff ; un backoff déjà
enregistré est conservé lors d'un reboot.

`blocked`, un fichier d'état corrompu, une horloge ambiguë ou un travailleur
encore actif ne sont pas débloqués. Les migrations v2/v3 restent explicites ;
aucun ancien budget n'a été migré ou remplacé.

## Modifications

- [budget.py](../netvue_bridge/budget.py) : ajout de `recover_after_restart`,
  avec reprise d'un `in_progress` et rebase du délai monotone après reboot.
- [continuous.py](../netvue_bridge/continuous.py) : contrôle de `/proc` pour
  les sessions `continuous`, l'ancien superviseur, `stream` et `client`,
  filtrées par adresse cible ; refus de démarrer si l'absence de session ne
  peut pas être établie.
- [test_continuous.py](../tests/test_continuous.py) : tests de reprise après
  reboot Debian et redémarrage de conteneur, backoff, session encore active,
  horloge reculée, état bloqué et état corrompu.
- [test_continuous_timestamp_integration.py](../tests/test_continuous_timestamp_integration.py) :
  MediaMTX est configuré sur un port libre, évitant la collision RTSP 8554.
- Documentation mise à jour dans [architecture.md](../docs/architecture.md),
  [development.md](../docs/development.md) et
  [README.md](../deployment/README.md).

Aucun fichier n'a été supprimé dans cette phase. Le fichier
`docs/phase1-checklist.md` apparaissait déjà supprimé dans Git avant ces
modifications ; cette suppression préexistante n'a pas été restaurée.

## Vérifications réussies

Toutes les vérifications ci-dessous utilisent des environnements Linux et des
caméras fictives ; aucune caméra réelle ni aucun budget de production n'a été
contacté.

- [test_continuous.py](../tests/test_continuous.py) : **25/25**.
- [test_continuous_timestamp_integration.py](../tests/test_continuous_timestamp_integration.py) :
  **2/2**, reconnexion et collisions PTS avec MediaMTX et source vidéo fictive.
- [test_rtsp_bridge.py](../tests/test_rtsp_bridge.py) : **29/29**, avec
  MediaMTX, FFmpeg, FFprobe et PyAV portables ; aucun échec intermittent RTSP
  pendant cette exécution.
- `git diff --check` : réussi.

## Blocages et limites

**Non vérifié :** les tests Home Assistant 2026.7.1, ses entités et son aperçu
JPEG, ainsi que le scénario Docker isolé de démarrage/arrêt. Le téléchargement
de `ghcr.io/home-assistant/home-assistant:2026.7.1` a échoué pendant
l'extraction d'une couche (`read-only file system`). Le disque C: ne disposait
alors que d'environ 455 Mo libres ; `docker system df` signalait 4,054 Go
d'images et 1,543 Go de cache de build. Aucun nettoyage Docker n'a été effectué
pour ne pas supprimer des données potentiellement utilisateur. L'installation
Home Assistant par pip a été interrompue à la demande après 16 minutes.

Le code de reprise et les reconnexions synthétiques hors Docker sont validés ;
la validation HA/Docker reste à refaire lorsque l'espace de stockage ou une
image HA déjà disponible le permettra. Aucun déploiement réel, redémarrage de
service de production ou test caméra n'a été effectué.
