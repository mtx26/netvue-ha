# Phase 48 — Fiabilité des tests et retours CI

## Objectif

Corriger l'échec HA observé dans le dernier run, renforcer une assertion E2E
HA, éviter la répétition des mêmes tests d'endurance et réduire l'attente des
PR documentaires sans affaiblir la validation avant publication.

## Modifications

- Le test WebRTC HA couvre le handoff autorisé entre offres et vérifie qu'une
  seconde session n'est pas envoyée avant la fermeture de la première. Il
  conserve la vérification qu'un arrêt annulé attend la fin du DELETE bloqué.
- L'E2E HA valide que l'image décodée correspond à l'image rouge déterministe
  du simulateur, au-delà de la seule signature JPEG.
- La CI classe les changements : sur PR, `build`, `integration` et `docker`
  sont ignorés uniquement si tous les fichiers modifiés sont documentaires.
  Les tests Go/HA restent exécutés à chaque PR; tous les jobs restent requis
  pour un push sur `main` avant publication. Les renommages sont désactivés
  dans le diff pour qu'un déplacement de fichier code ne contourne pas le filtre.
- `actionlint` valide les workflows dans le job Go.
- Le job race répété de l'endurance exclut précisément
  `TestManagerEndurance`; ces tests restent exécutés dix fois par le job
  multi-caméra.

## Vérifications

- `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7 .github/workflows/ci.yml .github/workflows/endurance.yml` : réussi.
- `go test -race -run '^TestManagerEndurance' -count=1 ./internal/manager` : réussi.
- `go test -race -count=1 -skip '^TestManagerEndurance' ./internal/manager` : réussi.
- `go test -count=1 ./...` : réussi.
- `go test -race -count=1 ./...` : réussi.
- `go vet ./...` : réussi.
- Analyse AST des deux fichiers Python modifiés et `git diff --check` : réussis.
- Un premier lancement Go a rencontré un crash natif transitoire dans le test
  WebRTC/TURN de `internal/manager`; le test TURN isolé puis les suites Go
  complètes relancées ont réussi.

## Limites restantes

- Les tests Python HA n'ont pas pu être exécutés localement : Python 3.13 ne
  dispose pas de Home Assistant; Python 3.14.3 disponible n'a ni pytest ni
  Home Assistant. Le test modifié du provider n'a donc pas été relancé ici.
- Docker et les binaires FFmpeg/ffprobe/MediaMTX ne sont pas disponibles dans
  cet environnement. L'E2E HA → Go → MediaMTX et l'assertion de couleur ajoutée
  doivent encore être confirmés sur le runner CI Linux.
- Aucun run GitHub postérieur à ces changements locaux n'a été lancé. Aucune
  caméra réelle ni aucun service de production n'a été contacté.
