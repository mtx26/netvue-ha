# Développement

Go 1.26+ ; depuis la racine :

```sh
go build ./cmd/netvue-bridge
go test ./...
```

Toute la stratégie de tests (Go, Home Assistant, simulateur, E2E, endurance, CI)
est décrite dans [testing.md](./testing.md). Le test vidéo MediaMTX/FFmpeg est
aussi présenté dans le [guide du pont](./bridge.md).

Les tests utilisent un réseau isolé, des données fictives et des ports libres ;
ils ne prouvent ni la compatibilité matérielle ni la stabilité prolongée.
Expurger les journaux avant partage.

MediaMTX : utiliser `deployment/mediamtx.yml` (seul RTSP/TCP sur loopback) plutôt
que les paramètres par défaut.

## Docker

Le [guide Portainer](../deployment/go-portainer.md) explique l'image et le
rendu local de stack, sans dÃ©ploiement. Le workflow valide les pull requests
et publie uniquement depuis `main`. `test_portainer_config.py` compare les
variables MediaMTX Ã  la rÃ©fÃ©rence et protÃ¨ge les principaux rÃ©glages de
sÃ©curitÃ© de la stack. `.dockerignore` limite le contexte aux sources Go
requises.
