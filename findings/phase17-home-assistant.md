# Phase 17 — Intégration Home Assistant Netvue Local

## OBJECTIF

Permettre à Home Assistant de découvrir ou d'ajouter manuellement des caméras,
de demander au pont de lancer leurs sessions Netvue et d'exposer une entité
caméra par appareil, sans imposer un nombre maximal arbitraire et sans
changer l'accès RTSP loopback ni l'authentification LAN Netvue.

## MODIFICATIONS

- Ajout de `netvue_bridge/manager.py` : API HTTP limitée à
  `127.0.0.1:8765`, lancement/arrêt des superviseurs existants et allocation
  d'un budget v4 indépendant par caméra, sans plafond logiciel de caméras.
- Ajout de `custom_components/netvue_local/bridge_api.py` et adaptation du
  formulaire, du cycle de vie, de la caméra et du capteur binaire. Les flux
  sont générés depuis l'adresse IPv4 ; aucun identifiant RTSP n'est ajouté.
- Mise à jour de la stack et du workflow GHCR vers un tag `phase17` et des
  tags SHA. L'image de stack doit désormais être explicitement épinglée sur
  le tag SHA publié, pour éviter de lancer silencieusement l'ancienne image.
- Mise à jour des guides Portainer et Home Assistant ainsi que de
  `docs/architecture.md`.
- Correction du test synthétique : il utilise un port MediaMTX libre, tout en
  remplaçant seulement dans le processus de test l'URL de production en
  `:8554`. La suppression d'une session est sérialisée avec sa recréation,
  afin d'éviter deux démarrages concurrents pour la même caméra.

Fichiers concernés : `.github/workflows/publish-bridge-image.yml`,
`netvue_bridge/manager.py`,
`custom_components/netvue_local/{__init__.py,bridge_api.py,config_flow.py,const.py,manifest.json,rtsp.py,strings.json,translations/en.json,translations/fr.json,README.md}`,
`deployment/Dockerfile`,
`deployment/stack.yaml`, `deployment/README.md`, `docs/architecture.md`,
`tests/test_bridge_manager.py`, `tests/test_home_assistant_integration.py`,
`tests/test_rtsp_bridge.py` et `tests/ha_integration_synthetic.py`.

## FONCTIONNEMENT

La découverte est déclenchée par l'utilisateur, sur une interface privée
sélectionnée ; elle envoie une diffusion UDP unique. L'ajout manuel reste
disponible. Après validation, Home Assistant demande au gestionnaire une
session par caméra. Le gestionnaire réutilise le superviseur et le budget v4,
avec un répertoire d'état distinct pour chaque caméra et un nom RTSP dérivé
de son adresse. MediaMTX reste unique et n'écoute que sur loopback.

Home Assistant expose une entité caméra Stream et un capteur de disponibilité
RTSP/H.264. La suppression ou le changement d'adresse demande l'arrêt de
l'ancienne session. Les entrées historiques avec URL loopback continuent à
être lues ; les URL personnalisées ne sont pas réaffectées automatiquement.

Le dépôt source et le package GHCR sont distincts. Le dépôt source reste
privé. Le package `ghcr.io/mtx26/netvue-ha` est encore privé : l'accès GitHub
disponible n'a pas le droit `write:packages`. Une requête anonyme au manifest
GHCR a renvoyé HTTP 401. L'image Phase 17 n'a donc pas été publiée.

## TESTS

- `tests/test_home_assistant_integration.py` : **14 tests réussis** sous
  Home Assistant 2026.11.2.
- `tests/test_bridge_manager.py` : **4/4 réussis**, dont un scénario à trois
  caméras avec budgets indépendants, l'état bloqué et l'exclusion
  arrêt/redémarrage.
- `tests/test_continuous.py` : **28/28 réussis**.
- `tests/test_rtsp_bridge.py` : **34 exécutés, 33 réussis, 1 ignoré** ;
  les tests MediaMTX/FFprobe utilisent des flux synthétiques. Les premières
  assertions d'image ont révélé des références Phase 16 obsolètes ; elles ont
  été adaptées au tag SHA obligatoire Phase 17, puis la suite a réussi.
- `tests/ha_integration_synthetic.py` : réussi avec MediaMTX 1.21.4 et FFmpeg.
  Le scénario valide l'ajout manuel, les entités, l'annonce RTSP/H.264, un
  JPEG décodé par Home Assistant, l'indisponibilité, puis le déchargement.
  Aucune caméra réelle n'est contactée.
- Les dépendances HA se sont installées en 16 secondes après déplacement du
  venv vers le système de fichiers Linux persistant WSL ; le fallback de
  hardlink de `uv` était un avertissement non bloquant. Le test a nécessité
  PyAV, `ha-ffmpeg` et `libturbojpeg`. Cette dernière bibliothèque a été
  extraite dans le cache WSL, sans installation système ni modification du
  dépôt. Le bootstrap pip a journalisé des dépendances manquantes pour
  quelques intégrations HA non utilisées ; les composants `stream` et
  `netvue_local` requis par le scénario ont fonctionné.
- Le YAML de stack a été analysé : deux services, réseau host, une réplique
  chacun et aucun port publié. La commande Docker Swarm n'a pas pu être lancée,
  car Docker n'est pas disponible dans l'environnement.
- Le pull GHCR anonyme a été essayé et refusé (HTTP 401). Aucun déploiement
  Portainer ni changement de service de production n'a été effectué.

## ÉTAT FINAL

Le code Home Assistant, la gestion multi-caméra avec ressources bornées et le
scénario vidéo synthétique sont validés hors ligne. La publication publique
GHCR reste bloquée par l'absence du droit GitHub `write:packages` ; l'image
Phase 17 nécessite une publication avant usage de la stack. La validation
Docker/Swarm et l'essai sur le serveur restent à autoriser et effectuer
séparément.

Prochaine action nécessaire : accorder un accès permettant de rendre public
le seul package et de publier l'image Phase 17, puis vérifier un pull anonyme.
