# Refonte architecturale — suivi

**État : en cours.** L'inventaire détaillé existant est conservé dans le
[suivi de phase 50](../findings/phase50-refactorisation-exhaustive-suivi.md).
Les examens de cette phase ne constituent pas une validation complète de la
nouvelle architecture. Les modifications Git préexistantes restent en place.

## Périmètre et migration

Les chemins ci-dessous rattachent les sources et tests actifs à un module ;
les fichiers archivés restent séparés. Les ressources générées et dépendances
externes sont exclues. Une migration ne déplace un contrat ou une ressource
qu'après identification de ses propriétaires et tests de régression.

| Module | Fichiers examinés / périmètre | Problèmes identifiés | Refonte effectuée | Tests | Statut |
|---|---|---|---|---|---|
| Protocole et sessions Netvue | `internal/netvue/*.go`, dont leurs tests | Revue antérieure dans le suivi de phase 50 ; validation des dépendances à poursuivre | `liveReceiver` préexistant conservé | Go et race, E2E synthétique du bridge | Examiné précédemment ; refonte non clôturée |
| Gestionnaire de sessions et API | `internal/manager/manager*.go`, `http.go`, tests associés ; `cmd/netvue-bridge/` | Validation d'adresse dupliquée ; tri du snapshot sous verrou ; interactions concurrentes à réexaminer | Normalisation commune ; invariant du verrou documenté ; tri du snapshot hors verrou | Adresses IPv4 mappées, snapshot, API, cycle de vie et concurrence avec race ; E2E synthétique multi-caméra antérieure | Refonte en cours ; autres interactions à poursuivre |
| RTSP et H.264 | `internal/netvue/video*.go`, `media*.go`, tests RTSP et `deployment/mediamtx.yml` | Horodatage et changements de format sensibles | Refactorisation H.264 préexistante conservée | Publication et décodage synthétiques avec MediaMTX et FFmpeg ; adaptation H.264 WebRTC | Examiné précédemment ; refonte non clôturée |
| WebRTC et signalisation | `internal/manager/webrtc*.go`, tests associés | Démarrage mêlant ICE, RTSP et workers ; la réservation pouvait être libérée avant la fermeture effective du mux ICE | L'arrêt complet attend désormais les workers et la fermeture des ressources ; libération partagée et protégée par identité du pair | Régression avec fermeture du mux bloquée ; suite Go race complète réussie après un crash Windows intermittent ; crash Pion/TURN réduit toujours non expliqué | Refonte en cours |
| Audio et talkback | `internal/netvue/audio*.go`, `live_talkback*.go`, `talkback*.go` ; `internal/manager/aac_opus*.go`, `opus_aac*.go`, `h264_transcode*.go` et tests | `aac_opus.go` n'utilise que le premier timestamp source ; les discontinuités AAC suivantes ne sont pas propagées à Opus | Callbacks RTSP audio isolés ; synchronisation de la fixture corrigée ; tests synthétiques des discontinuités conservés ; aucune politique de timestamp inventée | Sauts AAC avant/arrière et wrap testés avec FFmpeg ; monotonie Opus vérifiée ; synchronisation A/V réelle non mesurée | Refonte non clôturée ; politique à définir après validation de la correspondance des unités et des horloges |
| Home Assistant | `custom_components/netvue_local/` ; `tests/ha/`, `tests/test_home_assistant_*.py`, `tests/ha*_synthetic.py` | Cycle de vie et contrat Go/HA à préserver ; aucun changement de production précédemment justifié | Harnais synthétique : logs et arrêt des processus contrôlés | 92 tests et 61 sous-tests en conteneur isolé, 33 avertissements ; Ruff ; E2E HA conteneurisée synthétique réussie | Examiné précédemment ; réévaluation à poursuivre |
| Simulateur et E2E | `internal/netvue/netvuetest/`, `tests/simulator/`, `tests/e2e/` | Les courses et sorties prématurées du bridge enfant étaient ignorées ; crash Windows intermittent sous `-race` | Instrumentation race et contrôle des logs et sorties du bridge enfant ; tests de régression | `go test ./... -count=1` réussi ; premier `go test -race ./... -count=1` interrompu par un crash Windows, scénario isolé réussi 3 fois puis suite race complète réussie à la relance | Refonte en cours ; crash intermittent non résolu |
| Tests, CI et déploiement | `tests/conftest.py`, `tests/test_portainer_config.py`, `tests/requirements-homeassistant.txt`, `scripts/`, `.github/workflows/`, `Dockerfile`, `.dockerignore`, `deployment/`, `go.mod`, `go.sum`, `hacs.json` | SC2034 corrigé avant cette phase ; harnais HA insuffisamment testé | Régressions du harnais HA ajoutées à la CI ; correction CI préexistante conservée | Actionlint réussi ; ShellCheck utilisé précédemment, absent lors de la dernière exécution | Examiné précédemment ; validation de migration à poursuivre |
| Outils et archive | `findings/*.py`, `archive/python-bridge/` | Archive historique isolée ; ne pas réintroduire le bridge Python | Aucune | Self-tests antérieurs des outils ; archive non exécutée | Examiné précédemment ; pas de migration décidée |
| Documentation | `docs/`, `findings/`, `README.md`, `uml/`, README des composants | Décrire l'architecture effectivement implémentée sans confondre synthétique et réel | Suivi initial créé | Vérifications documentaires à poursuivre | En cours |

## Ordre et reprise

1. Continuer l'examen du cycle de vie manager/sessions, en particulier
   annulation, reconnexion et isolation multi-caméra ; examiner ensuite
   `http.go` et les frontières média et Home Assistant.
2. Garder la conversion AAC→Opus en best-effort documenté tant qu'une méthode
   de propagation des PTS par unité n'est pas validée de bout en bout ; ne pas
   confondre monotonie RTP avec synchronisation A/V.
3. Continuer à réduire le crash Windows sous `-race`. Les piles observées
   diffèrent (Pion mDNS/ICE dans un scénario TURN réduit, `runtime.deferconvert`
   pendant `os/exec` dans un E2E multi-caméra) ; aucune cause commune ou racine
   n'est établie.
4. Poursuivre les lots incomplets sans présenter les tests synthétiques comme
   une validation réelle. Aucune caméra réelle ni production autorisée.

Ne pas déclarer un module terminé ou validé en conditions réelles sur la seule
base d'un examen antérieur ou de tests synthétiques.

Les résultats, les conditions Docker et les échecs intermittents Windows sont
détaillés dans le [rapport de phase 52](../findings/phase52-validation-et-refonte.md).
Le parcours HA–Go–MediaMTX fonctionne désormais dans un réseau Docker isolé ;
il ne remplace pas une validation sur caméra réelle.
Les investigations en cours sont détaillées dans le
[rapport de phase 53](../findings/phase53-timestamps-et-refonte.md).
Les résultats WebRTC, AAC et crash Windows de la phase suivante sont détaillés
dans le [rapport de phase 54](../findings/phase54-crashes-cycle-webrtc.md).
