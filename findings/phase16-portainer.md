# Phase 16 — Installation Portainer

## Verdict

**Stack et guide prêts pour une configuration manuelle dans Portainer ; aucun
déploiement n'a été effectué.** La stack utilise les volumes bind existants et
les variables d'environnement saisies dans Portainer. Aucun fichier `.env`
n'est requis.

**Basculement bloqué pour l'instant :** le contrôle passif du Debian a observé
un listener sur `127.0.0.1:8554`. Une seconde instance MediaMTX ne peut pas
occuper cette même adresse et ce port. L'identité du processus n'a pas été
examinée. L'instruction de ne pas remplacer ou modifier la stack existante et
de ne pas contacter de caméra a été respectée ; le bouton **Deploy** n'a pas
été utilisé.

## Architecture retenue

- `mediamtx` et `bridge` partagent le réseau `host` et sont épinglés sur le
  nœud de Home Assistant. Cela permet la découverte LAN depuis l'hôte et
  l'accès HA à `rtsp://127.0.0.1:8554/netvue2`.
- MediaMTX conserve l'écoute loopback ; la stack ne publie aucun port. Le
  bridge reste non-root, en racine read-only, sans capacités ajoutées, et
  limité à 0,5 CPU / 256 Mio ; MediaMTX à 0,5 CPU / 128 Mio.
- Les fichiers d'état et de configuration sont des volumes bind dont les
  chemins source sont fournis par `NETVUE_STATE_DIR` et
  `NETVUE_MEDIAMTX_CONFIG` dans les variables de stack Portainer.
- Le pont garde le superviseur et le budget v4. Une panne de processus est
  relancée au plus trois fois par Swarm ; aucune initialisation ou migration
  automatique ne peut remplacer un état existant.

Le choix `host` est cohérent avec l'URL loopback et le protocole LAN, mais
partage le réseau de l'hôte. Le RTSP reste anonyme pour les processus locaux ;
ne pas changer l'adresse d'écoute vers `0.0.0.0`.

## Modifications

- Modifiés : [stack.yaml](../deployment/stack.yaml),
  [Dockerfile](../deployment/Dockerfile),
  [README Portainer](../deployment/README.md),
  [architecture.md](../docs/architecture.md) et
  [test_rtsp_bridge.py](../tests/test_rtsp_bridge.py).
- Ajouté : [publish-bridge-image.yml](../.github/workflows/publish-bridge-image.yml),
  workflow manuel qui construit l'image Linux amd64 et la publie dans GHCR.
- Supprimé : [example.env](../deployment/example.env). Les variables sont
  désormais documentées comme entrées Portainer ; aucun fichier local de
  variables n'est demandé.
- L'image du pont a été construite et publiée avec succès par GitHub Actions
  run `43922543802`. Tags : `phase16` et
  `sha-73fe8737048ca2912d89e4885dbe354116bbc204`. Digest de l'index publié :
  `sha256:8d30fbf89078e0b437302c2d23a0c58a43bb1b20be8fd73de20cc6c7ffc1cbd2`.
  Le package `ghcr.io/mtx26/netvue-ha` est **privé**, car le dépôt source est
  privé. Portainer doit être configuré avec une registry GHCR autorisée en
  lecture pour pouvoir tirer l'image.
- MediaMTX et la base Python sont épinglés sur des digests d'index vérifiés
  dans les métadonnées Docker Hub le 16 novembre 2026. L'image du pont est
  publiée pour Linux amd64, architecture observée sur le Debian cible.
- Aucun code du pont, budget, volume, service ou configuration Home Assistant
  installé n'a été modifié.

## Tests et observations

**Réussis :**

- Tests Linux/WSL du pont, du superviseur, du budget v4 et RTSP/H.264
  synthétique : **68 exécutés, 67 réussis, 1 ignoré**.
- Tests Windows ciblés après ajout du workflow : écoute loopback, absence de
  ports publiés, variables Portainer et configuration GHCR, **3/3 réussis**.
- Le YAML a été analysé localement avec PyYAML : deux services, réseau host,
  une réplique par service ; `git diff --check` est propre.
- Les métadonnées officielles Docker Hub indiquent MediaMTX 1.21.4 actif avec
  manifestes amd64, arm64 et arm/v7, et un index Python multi-architecture.
- Contrôles Debian en lecture seule : Docker Engine 29.7.2, Swarm actif à
  un nœud, architecture x86_64 et cgroup v2. La liste d'images contient
  notamment `netvue-bridge:phase15-179f7438` et
  `bluenviron/mediamtx:1.21.4`.

**Non exécutés :** `docker stack config` sur la nouvelle stack, pull de la
nouvelle image depuis Debian, démarrage/arrêt Docker isolé et validation via
interface Portainer. Le transfert de test temporaire a été annulé et son
archive retirée ; aucun service n'a été créé ou remplacé. Les tests HA en
production, la lecture RTSP réelle et les permissions effectives des volumes
ne sont pas validés.

## Installation prévue

Dans Portainer : **Stacks → Add stack → Upload**, charger
[stack.yaml](../deployment/stack.yaml), puis renseigner les cinq variables
obligatoires : `NETVUE_NODE`, `NETVUE_SOURCE`, `NETVUE_TARGET`,
`NETVUE_MEDIAMTX_CONFIG` et `NETVUE_STATE_DIR`. L'image du pont a un tag SHA
par défaut ; `NETVUE_BRIDGE_IMAGE` reste remplaçable. Les chemins des volumes
doivent déjà exister. Le fichier MediaMTX doit être lisible et le répertoire
d'état inscriptible par UID/GID 10001. Le package GHCR étant privé,
configurer dans Portainer une registry avec un compte/token GitHub `read:packages` ;
ne pas mettre le token dans la stack.

Le budget v4 présent dans le volume doit correspondre aux adresses source et
cible. Un volume neuf nécessite une initialisation explicite ; un état v2/v3
nécessite la migration auditée. La stack ne le fait jamais automatiquement.
Le déploiement démarrerait le pont et contacterait la caméra.

Avant le déploiement réel, il faut identifier le détenteur du port 8554,
planifier avec autorisation le basculement de la stack existante, et confirmer
les chemins de volumes et l'image à employer. Tant que ces conditions ne sont
pas réunies, ne pas déployer cette stack.
