# Architecture

```text
Home Assistant (configuration et entités)
    | HTTP sur loopback
    v
gestionnaire Go -- session + superviseur par caméra --> LAN Netvue
    | H.264 / RTSP TCP
    v
MediaMTX sur loopback --> Home Assistant Stream
```

- `cmd/netvue-bridge/` fournit `test`, `run` et `serve`.
- `internal/manager/` gère les sessions et l'API d'ajout, état, arrêt,
  reprise et suppression.
- `internal/netvue/` isole découverte unicast, authentification, transport
  vidéo, publication et supervision ; il ne dépend pas de HA.
- `custom_components/netvue_local/` gère les entrées et entités HA ; seul Go
  parle le protocole caméra.
- `deployment/go-stack.yaml` lance Go et MediaMTX sur le réseau host du nœud.

HA conserve les entrées ; le registre Go est volatil. Après redémarrage du
pont, HA réajoute une entrée absente, mais ne relance pas une session présente
arrêtée ou en échec. La suppression HA retire la session. L'adresse IPv4 est
saisie manuellement ; HA ne recherche pas de candidats.

Le gestionnaire expose l'état du superviseur et, en cas d'échec définitif, une
catégorie d'erreur non sensible. HA fournit un capteur de session, un bouton
de reprise explicite et un capteur RTSP indépendant : une session supervisée
ne garantit pas un flux, et les causes transitoires ne sont pas exposées.

L'API `127.0.0.1:8765` et MediaMTX `127.0.0.1:8554` n'ont pas
d'authentification distincte ; tout processus du même espace réseau peut y
accéder. Le protocole Netvue observé n'est pas chiffré. Détails et limites :
[pont](./bridge.md), [protocole](./netvue-protocol.md),
[déploiement](../deployment/go-portainer.md) et [plan Go](./go-plan.md).
