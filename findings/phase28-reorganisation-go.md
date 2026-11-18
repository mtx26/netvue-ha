# Phase 28 — Réorganisation autour de Go

## OBJECTIF

Faire de la passerelle Go l'implémentation principale du dépôt, garder
l'intégration Home Assistant en Python et isoler l'ancien projet Python sans
perdre les travaux ou preuves historiques.

## MODIFICATIONS

- Module Go, `go.mod`, `go.sum`, `cmd/`, `internal/` et Dockerfile déplacés à
  la racine ; les imports internes utilisent le chemin du module racine.
- L'ancien projet Python est regroupé dans `archive/python-bridge/` : ses
  sources, tests, dépendances, Dockerfile, stack et workflow historiques sont
  conservés à titre documentaire, hors des chemins actifs. Les 31 fichiers
  déplacés sont identiques aux versions du dernier commit.
- L'intégration `custom_components/netvue_local/` et ses tests restent actifs.
  Le scénario HA synthétique utilise la configuration MediaMTX actuelle au
  lieu de dépendre de l'ancien répertoire Python.
- Le workflow de publication Go, le guide Portainer et la documentation
  utilisent les chemins racine. Le `.dockerignore` limite le contexte aux
  fichiers nécessaires au build : les archives, outils, captures et rapports
  locaux ne sont pas envoyés au constructeur Docker.
- Les README, la documentation d'architecture et de développement ainsi que
  les instructions d'agents ont été alignés sur le Go. Les rapports historiques
  et les changements Git préexistants sont préservés.

## FONCTIONNEMENT

```text
go.mod, Dockerfile          module et image Go
cmd/netvue-bridge/          CLI
internal/netvue/            protocole, session, vidéo, supervision
internal/manager/           gestion multi-caméra et API locale
custom_components/          intégration Home Assistant Python
deployment/                 stack Go et MediaMTX
tests/                      tests Home Assistant
docs/                       guides et plan
archive/python-bridge/      ancienne passerelle Python, hors production
```

Le fonctionnement du pont, l'API locale, les flux RTSP et le contrat HA n'ont
pas été volontairement modifiés. Seule l'initialisation du scénario de test HA
change de fichier MediaMTX ; il utilise toujours un manager HTTP simulé.

## TESTS

- Image Docker construite depuis la racine pour Linux/amd64, puis lancement de
  `serve --help` en lecture seule et sans capacités : réussis.
- `docker stack config` avec variables fictives : réussi.
- Vingt tests HA dans l'image Home Assistant 2026.11.2 et sans réseau :
  réussis.
- Scénario HA/MediaMTX/FFmpeg synthétique sur réseau Docker interne : réussi
  avec la configuration MediaMTX actuelle ; aperçu JPEG et arrêt vérifiés.
- Tests Go MediaMTX/FFmpeg : publication/décodage et isolation multi-flux
  réussis.
- Analyse YAML du workflow, contrôle des chemins et des liens locaux actifs :
  réussis. Ses commandes de test Go ont aussi réussi dans l'image Linux du
  workflow, sans réseau, sur une copie locale minimale du module. Le workflow
  de publication n'a pas été déclenché.
- Un premier essai Linux montait tout le dépôt local, y compris les outils
  ignorés : l'exploration de ces fichiers était trop lente et le conteneur de
  test a été arrêté. Le rejeu sur une copie Go minimale a réussi.
- Un premier enchaînement Go a réussi les tests et le détecteur de courses,
  puis `go vet ./...` a échoué pendant le déplacement concurrent des dossiers
  (`netvue-go` introuvable). Après stabilisation, `go test -count=1 ./...`,
  `go test -count=1 -race ./...`, `go vet ./...` et la compilation Go ont
  tous réussi.

## ÉTAT FINAL

La passerelle Go est le code actif ; l'ancien pont Python est archivé et son
workflow n'est plus dans `.github/workflows/`. Aucun service de production,
compte ou caméra n'a été contacté et aucune image n'a été publiée. L'essai
HA reste synthétique avec un faux manager : la chaîne complète HA → Go →
MediaMTX et la stabilité sur caméras réelles restent à valider séparément.
