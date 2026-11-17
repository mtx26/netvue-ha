# Netvue Bridge

Passerelle locale **Netvue LAN → H.264/RTSP → Home Assistant**. Une seule
application Go gère plusieurs caméras, avec une session supervisée indépendante
par caméra et un serveur MediaMTX partagé. L'intégration Home Assistant reste
en Python ; elle conserve les entrées de configuration et communique avec
l'API locale du pont.

## Installation

Suivre le [guide Docker/Portainer](deployment/go-portainer.md), puis le
[guide Home Assistant](custom_components/netvue_local/README.md). La stack
utilise le réseau host sur le même nœud que Home Assistant et ne publie aucun
port. L'ajout d'une caméra déclenche immédiatement une connexion réelle et
nécessite une autorisation préalable.

## Développement

Prérequis : Go 1.26 ou supérieur. Depuis la racine :

```sh
go test ./...
go test -race ./...
go vet ./...
go build ./cmd/netvue-bridge
```

Le [guide du pont](docs/bridge.md) décrit les commandes et les limites ;
le [guide de développement](docs/development.md) explique les tests Go,
Docker et Home Assistant. Voir aussi l'[architecture](docs/architecture.md),
le [plan](docs/go-plan.md) et les [faits sur le protocole](docs/netvue-protocol.md).

## Organisation

- `cmd/netvue-bridge/` : interface de commande.
- `internal/netvue/` : protocole, sessions, vidéo et supervision.
- `internal/manager/` : gestion multi-caméra et API HTTP locale.
- `custom_components/netvue_local/` : intégration Home Assistant en Python.
- `deployment/` : stack Portainer et configuration MediaMTX.
- `tests/` : tests et scénario synthétique Home Assistant.
- `docs/` : architecture, développement et plan.
- `archive/python-bridge/` : ancien pont Python et ses fichiers historiques,
  hors du fonctionnement et des workflows actifs.

Les rapports historiques conservent les connaissances utiles. Les dossiers
`findings/`, `input/`, `tools/` et les captures peuvent contenir des données
privées et sont ignorés par Git.

## Limites et sécurité

Les tests synthétiques ne démontrent ni la compatibilité de toutes les
caméras ni la stabilité prolongée. L'API HTTP et RTSP sont limités au loopback,
mais restent accessibles aux processus qui partagent cet espace réseau.
Le protocole Netvue observé ne chiffre pas le login : utiliser un LAN de
confiance. Ne jamais publier de secrets ni exposer les flux sur Internet.
