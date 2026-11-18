# Netvue Bridge — Roadmap

## Objectif

Développer une passerelle locale Netvue vers RTSP et Home Assistant,
fiable, performante, modulaire et simple à installer.

## États

- ✅ Terminé et validé
- 🧪 Implémenté, validation réelle nécessaire
- 🔄 En développement
- ⬜ À faire
- 💡 À étudier

## 1. Fiabilité et intégration

| État | Fonctionnalité | Objectif |
|---|---|---|
| 🧪 | Protocole Netvue | Connexion et vidéo fiables |
| 🧪 | Supervision | Reconnexion et fonctionnement continu |
| 🧪 | Multi-caméra | Sessions indépendantes |
| 🧪 | Home Assistant | Intégration complète |
| 🧪 | Découverte LAN | Découverte UDP réelle confirmée en [phase 35](../findings/phase35-decouverte-lan-ha.md) ; création depuis HA et autres modèles à valider |
| ✅ | CI multi-composants | Go/race/vet, tests HA, MediaMTX/FFmpeg, E2E HA ↔ Go ↔ MediaMTX et rendu Swarm réussis dans le [run CI](https://github.com/mtx26/netvue-ha/actions/runs/44139631957) ; la publication A/V synthétique est désormais incluse |
| ✅ | Validation réelle courte (phase 36) | 30 s de H.264 décodées depuis la session active de la caméra ; le digest déployé n'est pas relié au commit local ([rapport](../findings/phase36-validation-reelle.md)) |
| ✅ | Endurance synthétique | Multi-caméra, caméra défaillante, churn, annulation et libération de goroutines testés dans la [phase 35](../findings/phase35-fiabilite-prolongee-et-performances.md) |
| 🧪 | Stabilité réelle 24 h/24 | Protocole préparé dans la [phase 35](../findings/phase35-fiabilite-prolongee-et-performances.md) ; aucune validation réelle effectuée |
| 🧪 | Performances de référence | Benchmarks et profils CPU/heap du gestionnaire ; aucune optimisation justifiée |

## 2. Audio

| État | Fonctionnalité | Objectif |
|---|---|---|
| 🧪 | Analyse APK audio | Analyse statique des trames et directions ; réception AAC confirmée sur la caméra 2, unité de `samplesize` inconnue |
| 🧪 | Réception audio | AAC/ADTS 8 kHz mono décodé sur la caméra 2 ; PCM 16 bits non validé sur caméra |
| 🧪 | RTSP audio/vidéo | H.264 et AAC décodés ensemble ; continuité d'un lecteur lors d'un changement de résolution réel confirmée, avertissement DTS restant à analyser en [phase 39](../findings/phase39-talkback.md) |
| 🧪 | Audio bidirectionnel Go | Opus→AAC-LC/ADTS vers la session Netvue active, retour AAC caméra→Opus et fermeture/reprise après inactivité RTP implémentés ; tests média FFmpeg à exécuter, restitution réelle non prouvée ([phase 43](../findings/phase43-microphone-ha.md)) |
| 🧪 | Microphone Home Assistant | Signalisation et transport WebRTC intégrés en opt-in ; `two_way_audio` reste désactivé jusqu'à confirmation acoustique ([phase 43](../findings/phase43-microphone-ha.md)) |

## 3. Enregistrement

| État | Fonctionnalité | Objectif |
|---|---|---|
| ⬜ | Mesures de capacité | Débit, stockage et ressources |
| ⬜ | Enregistrement continu | Enregistrer les vidéos |
| ⬜ | Rétention | Gérer automatiquement l'espace disque |
| ⬜ | Historique | Consulter les enregistrements |
| ⬜ | Exportation | Télécharger des extraits |
| ⬜ | Captures | Enregistrer des images |

## 4. Détection et statistiques

| État | Fonctionnalité | Objectif |
|---|---|---|
| 💡 | Détection de mouvement | Comparer Netvue, Go et Frigate |
| ⬜ | Zones de détection | Configurer les zones surveillées |
| ⬜ | Notifications HA | Signaler les événements |
| ⬜ | Statistiques | Suivre événements et disponibilité |
| 💡 | Détection d'objets | Personnes, animaux et véhicules |

## 5. Expérience utilisateur

| État | Fonctionnalité | Objectif |
|---|---|---|
| 🧪 | WebRTC | Provider HA opt-in, RTSP/TCP H.264/AAC, ICE UDP limité à l'IPv4 source et conversion audio intégrés ; test synthétique arrêt/reprise ajouté, validation média CI et accès réseau externe à confirmer |
| ⬜ | Configuration HA | Simplifier les réglages |
| 🧪 | Alertes de panne | Entité « Caméra indisponible » et diagnostics actionnables ajoutés ; tests HA/Portainer passés en conteneur isolé, état réel HA non vérifié |
| ⬜ | Dashboard | Centraliser les informations |
| 🧪 | Installation Portainer | Guide et stack disponibles ; déploiement réel non validé |

## Règles de suivi

- Mettre à jour les états après chaque développement.
- Distinguer implémentation et validation réelle.
- Ajouter une fonctionnalité lorsqu'un nouveau besoin apparaît.
- Éviter les fonctionnalités redondantes.
- Conserver les décisions techniques détaillées dans les documents adaptés.
- Utiliser cette roadmap comme référence pour les prochaines phases.