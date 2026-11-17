# Netvue Bridge

Passerelle locale Netvue LAN → H.264/RTSP → Home Assistant. Une application
Go gère plusieurs caméras et publie vers MediaMTX ; l'intégration HA Python
conserve la configuration et pilote l'API locale.

## Installation

Suivre le [guide Docker/Portainer](deployment/go-portainer.md). Le dépôt GitHub
doit être public pour installer Netvue Local avec HACS ; l'image GHCR doit
également être publique pour que Portainer la télécharge sans authentification.

## Développement

Go 1.26+ ; depuis la racine :

```sh
go test ./...
go test -race ./...
go vet ./...
go build ./cmd/netvue-bridge
```

Voir le [guide du pont](docs/bridge.md), les
[tests et outils de développement](docs/development.md),
l'[architecture](docs/architecture.md), le [protocole observé](docs/netvue-protocol.md)
et la [roadmap](docs/roadmap.md).

## Organisation et limites

`cmd/netvue-bridge/` : commandes ; `internal/netvue/` : protocole et vidéo ;
`internal/manager/` : sessions multi-caméra et API ; `custom_components/` :
intégration HA ; `deployment/` : stack et MediaMTX ; `tests/` : tests
synthétiques.
`findings/`, `input/`, `tools/` et les captures peuvent contenir des données
privées et sont ignorés par Git.

Les tests synthétiques ne prouvent ni compatibilité universelle ni stabilité
prolongée. L'API et RTSP sont accessibles aux processus partageant le réseau
hôte ; le protocole Netvue observé n'est pas chiffré. Utiliser un LAN de
confiance et ne jamais exposer les services.
