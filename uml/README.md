# Schémas UML du pont Netvue

Ces schémas décrivent l'architecture active : Home Assistant, le pont Go,
MediaMTX et le service IA Python optionnel, ainsi que les parcours caméra,
enregistrement et WebRTC. Les versions et bibliothèques indiquées sont celles
du dépôt. Les diagrammes décrivent le code présent, pas une validation
automatique sur caméra réelle.

## Diagrammes

| Fichier | Ce qu'il montre |
|---|---|
| [01-architecture.puml](./01-architecture.puml) | Pile Go/Python actuelle, intégration HA, MediaMTX, enregistrements et détection IA locale. |
| [02-session-camera.puml](./02-session-camera.puml) | Session Netvue, contrôles du protocole, publication gortsplib/RTSP et supervision. |
| [03-cycle-home-assistant.puml](./03-cycle-home-assistant.puml) | Découverte, configuration, réconciliation, suivi de l'enregistrement et de l'IA, puis retrait. |
| [04-webrtc.puml](./04-webrtc.puml) | Signalisation HA, Pion, adaptation FFmpeg partagée, audio et push-to-talk. |

Les fichiers `.puml` peuvent être ouverts avec un visualiseur PlantUML ou une
extension PlantUML pour VS Code. Les tests qui couvrent ces composants et les
commandes de validation sont répertoriés dans [docs/testing.md](../docs/testing.md).
En particulier, les tests unitaires et synthétiques ne sont pas une validation
en conditions réelles ; l'état connu est suivi dans
[docs/roadmap.md](../docs/roadmap.md).

## Repères dans le code

- Démarrage et validation des commandes : `cmd/netvue-bridge/main.go`
- API HTTP, cycle de vie et superviseur multi-caméra : `internal/manager/`
- Découverte, protocole, session, vidéo, audio et supervision : `internal/netvue/`
- Configuration, client HTTP, coordinator et WebRTC Home Assistant : `custom_components/netvue_local/`
- Tests Go : `internal/`, `cmd/` et `tests/e2e/`
- Tests Home Assistant : `tests/ha/` et `tests/test_home_assistant_*.py`
