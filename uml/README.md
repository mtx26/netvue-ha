# Schémas UML du pont Netvue

Ces schémas décrivent les chemins réellement présents dans le dépôt : le
parcours Home Assistant, la session Netvue vers RTSP, le contrôle des sessions
par l'API et la signalisation WebRTC. Ils documentent le code actif au moment
de leur rédaction ; ils ne signifient pas que chaque fonction a été validée
sur une caméra réelle.

## Diagrammes

| Fichier | Ce qu'il montre |
|---|---|
| [01-architecture.puml](./01-architecture.puml) | Les composants et les flux entre Home Assistant, Go, la caméra et MediaMTX. |
| [02-session-camera.puml](./02-session-camera.puml) | Les étapes et contrôles d'une session caméra, de la validation des paramètres à la publication RTSP et au superviseur. |
| [03-cycle-home-assistant.puml](./03-cycle-home-assistant.puml) | Découverte, configuration, réconciliation, surveillance et retrait d'une caméra depuis Home Assistant. |
| [04-webrtc.puml](./04-webrtc.puml) | Signalisation WebRTC, lecture RTSP et conversion média optionnelle. |

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
