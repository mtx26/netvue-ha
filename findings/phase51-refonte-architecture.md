# Phase 51 — Début de refonte architecturale

## Objectif et décisions

Reprendre la migration par composants en conservant les changements existants.
Le [suivi de migration](../docs/refactor-progress.md) rattache les sources aux
modules et distingue les examens antérieurs des validations encore requises.
Le protocole et les comportements expérimentaux ne sont pas modifiés.

## Lot WebRTC

Le démarrage du pair mélangeait l'allocation du socket et du multiplexeur ICE
avec RTSP, SDP et les workers média. L'allocation ICE et le transfert de
propriété du multiplexeur et du pair Pion sont isolés dans
`createPeerConnection`. L'ordre, les fermetures sur erreur ou annulation, les
délais et la configuration des candidats sont conservés.

Un test de régression vérifie qu'une source ICE invalide ne réserve pas la
caméra et qu'une nouvelle offre réussit après correction. Les cas de
négociation directe et via TURN local restent couverts.

Les callbacks RTSP AAC et H.264 sont isolés dans `webrtc_rtsp.go`, sans
modifier l'ordre de configuration audio, vidéo puis lecture. Le détecteur de
courses a révélé une mutation concurrente de l'URL dans la fixture du test
de transcodage : une barrière attend maintenant le démarrage du superviseur
fictif avant la mutation, sans changer le code de production.

| Fichier | Examen et modification | Validation |
|---|---|---|
| `internal/manager/webrtc.go` | Allocation ICE et callbacks RTSP extraits du démarrage | Go, race et E2E synthétique |
| `internal/manager/webrtc_rtsp.go` | Nouveau fichier : configuration et callbacks RTSP audio/vidéo | E2E WebRTC, audio et vidéo avec race |
| `internal/manager/webrtc_test.go` | Échec ICE et nouvelle tentative vérifiés | WebRTC direct et TURN avec race |
| `internal/manager/h264_transcode_test.go` | Course préexistante de fixture synchronisée | Test de transcodage H.264 avec race et FFmpeg |
| `docs/architecture.md`, `docs/roadmap.md`, `docs/refactor-progress.md` | Architecture effective et suivi mis à jour | Relecture documentaire |

## Vérification et limites

- Tests WebRTC ciblés avec détecteur de courses : réussis avant et après le lot.
- `go test ./... -count=1`, `go test -race ./... -count=1`, `go vet ./...` et
  `go build ./...` : réussis. Les deux suites Go ont utilisé
  `NETVUE_REQUIRE_INTEGRATION=1`, MediaMTX 1.21.4 et FFmpeg Windows n8.1.3
  isolés, avec le bridge compilé dans le dossier de session.
- E2E synthétique Go–MediaMTX : lecture RTSP, isolation multi-caméra et
  WebRTC/talkback réussis, avec et sans race. Les deux tests supplémentaires
  AAC vers Opus et Opus vers AAC avec signal synthétique ont réussi avec race
  et `NETVUE_TEST_FFMPEG_FULL`. Les scénarios E2E ont aussi réussi avec un
  bridge compilé avec `-race`, mais leur nettoyage ignore son code de sortie
  et ne montre ses logs qu'en cas d'échec du test : cela ne démontre donc pas
  l'absence de course dans ce processus enfant.
- Home Assistant : 89 tests et 59 sous-tests réussis, avec 37 avertissements ;
  Ruff réussi. Actionlint réussi sur les deux workflows ; ShellCheck absent
  lors de cette exécution avait été utilisé lors de la correction CI antérieure.
- Pas de gain de performance revendiqué sans mesure.
- La refonte globale est incomplète. Docker est indisponible : le parcours
  HA–Go–MediaMTX conteneurisé reste non exécuté. Les tests synthétiques ne
  démontrent pas le fonctionnement sur caméra réelle.
- Aucune caméra réelle ni installation de production n'a été contactée.
