# Phase 15C — RTSP local sans authentification

## Résultat

**Code RTSP simplifié et vérifié hors ligne.** La publication et la lecture ne
demandent plus d'identifiants RTSP. L'authentification LAN Netvue reste en
place. Aucun fichier `.storage`, service de production ou budget réel n'a été
modifié ; aucune caméra réelle n'a été contactée.

## Modifications

- Home Assistant ne demande plus d'identifiants RTSP. Le nom de flux produit
  `rtsp://127.0.0.1:8554/{stream_name}` ; la caméra et la sonde utilisent cette
  URL sans en-tête d'autorisation. Les anciens champs enregistrés sont ignorés,
  sans migration directe de `.storage`.
- Le pont publie vers l'URL locale sans paramètres ni secrets RTSP.
- Les comptes et variables d'environnement RTSP ont été retirés des
  configurations MediaMTX, de la stack et de l'exemple d'environnement.
- MediaMTX conserve `127.0.0.1:8554` et RTSP/TCP. La stack n'a pas de
  publication de ports ; les limites de ressources et autres restrictions
  restent en place.

Fichiers concernés : [config_flow.py](../custom_components/netvue_local/config_flow.py),
[camera.py](../custom_components/netvue_local/camera.py),
[coordinator.py](../custom_components/netvue_local/coordinator.py),
[rtsp.py HA](../custom_components/netvue_local/rtsp.py),
[const.py](../custom_components/netvue_local/const.py),
[strings.json](../custom_components/netvue_local/strings.json),
[traduction FR](../custom_components/netvue_local/translations/fr.json),
[traduction EN](../custom_components/netvue_local/translations/en.json),
[rtsp.py du pont](../netvue_bridge/rtsp.py),
[stream.py](../netvue_bridge/stream.py),
[check_rtsp.py](../netvue_bridge/check_rtsp.py),
[configuration MediaMTX locale](../netvue_bridge/mediamtx.local.yml),
[configuration MediaMTX de déploiement](../deployment/mediamtx.yml),
[stack.yaml](../deployment/stack.yaml),
[example.env](../deployment/example.env),
[tests RTSP](../tests/test_rtsp_bridge.py) et
[tests HA](../tests/test_home_assistant_integration.py).

## Vérifications

**PROUVÉ :**

- Tests RTSP/H.264 sous Linux/WSL avec MediaMTX, FFprobe, FFmpeg et PyAV :
  **31 réussis, 1 ignoré**. Les deux configurations MediaMTX ont accepté la
  publication et la lecture synthétiques sans identifiants ; chacune a publié
  55 images et lu 19 paquets avec six images-clés.
- Tests du pont sous Windows : **39 réussis, 4 ignorés**. Tests du superviseur
  et du budget v4 sous Linux/WSL : **28 réussis** ; l'authentification LAN
  reste testée.
- La génération de l'URL HA, la sonde RTSP sans en-tête `Authorization`, les
  traductions et la syntaxe des modules HA ont été vérifiées directement.
- Les tests statiques confirment l'adresse loopback des configurations
  MediaMTX et l'absence de ports publiés dans la stack.
- `git diff --check` ne signale pas d'erreur.

Reproduction RTSP : avec les outils portables indiqués et les ports locaux
disponibles, depuis WSL à la racine du dépôt :

```sh
NETVUE_MEDIAMTX=tools/mediamtx-1.21.4/mediamtx \
NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe \
tools/venv-phase14-linux/bin/python -B -m unittest discover \
  -s tests -p 'test_rtsp_bridge.py' -v
```

Cette commande lance MediaMTX et des lecteurs synthétiques sur loopback,
produit les résultats des tests et n'envoie aucun trafic à une caméra. Elle
utilise CPU et mémoire pendant l'exécution.

## Limites

**NON VÉRIFIÉ :** la suite complète Home Assistant n'a pas pu démarrer :
l'environnement HA disponible échoue à l'import (`homeassistant.util`
manquant). Le test de compatibilité des anciennes entrées existe, mais n'a
pas été exécuté dans Home Assistant. Docker n'étant pas disponible, la stack
n'a pas été déployée ; l'écoute externe n'a pas été sondée sur un serveur.

**DÉDUIT :** les anciennes entrées restent utilisables car l'intégration ne
lit plus leurs anciennes valeurs d'identifiants et conserve l'URL de flux.
Leur comportement reste à confirmer dans l'environnement HA cible.

Le RTSP sans authentification est volontairement accessible à tout processus
ayant accès à la boucle locale : la protection repose sur l'écoute
`127.0.0.1`, l'absence de ports publiés et les restrictions réseau existantes.
La validation de la stack sur le serveur et tout accès caméra restent hors de
cette phase.
