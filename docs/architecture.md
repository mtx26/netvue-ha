# Architecture Netvue Bridge

Une seule application Go gère les caméras et un MediaMTX partagé :

```text
Home Assistant (config entries, entités)
    | HTTP loopback
    v
gestionnaire Go -- une session et un superviseur par caméra --> LAN Netvue
    | H.264 / RTSP TCP
    v
MediaMTX loopback --> Home Assistant Stream
```

- [`cmd/netvue-bridge/`](../cmd/netvue-bridge/) fournit `test`, `run` et
  `serve` ; `serve` démarre le gestionnaire HTTP.
- [`internal/manager/`](../internal/manager/) gère les sessions distinctes et
  les opérations HTTP d'ajout, consultation, arrêt, reprise et suppression.
- [`internal/netvue/`](../internal/netvue/) réalise découverte unicast,
  authentification, réception H.264, publication RTSP et supervision avec
  backoff. Il ne dépend pas de Home Assistant.
- [`custom_components/netvue_local/`](../custom_components/netvue_local/)
  fournit les config entries et entités HA et sonde le RTSP ; il n'implémente
  pas le protocole caméra.
- [`deployment/go-stack.yaml`](../deployment/go-stack.yaml) déploie une
  instance Go et MediaMTX sur le réseau host du même nœud.

HA est la source persistante de configuration ; le registre Go est volatile.
Après redémarrage du pont, HA ajoute à nouveau une entrée absente, sans
relancer une session existante arrêtée ou en échec. La suppression HA retire
la session. La saisie de l'IPv4 caméra est manuelle ; il n'y a pas de
recherche de candidats LAN depuis HA.

L'API écoute sur `127.0.0.1:8765` et MediaMTX sur `127.0.0.1:8554`, sans
authentification distincte : les processus partageant le réseau hôte peuvent
y accéder. Le protocole Netvue observé n'est pas chiffré. Voir le
[guide Portainer](../deployment/go-portainer.md), le
[protocole](./netvue-protocol.md) et le [plan Go](./go-plan.md).

Le code Python historique du pont est isolé dans
[`archive/python-bridge/`](../archive/python-bridge/), hors des workflows et
du déploiement actifs. Les observations et rapports historiques restent des
preuves, pas une description du fonctionnement actuel.
