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
| 🧪 | Découverte LAN | E2E broadcast synthétique confirmé en CI ; réseau et caméra réels non vérifiés |
| ✅ | CI multi-composants | Go/race/vet, 39 tests HA, MediaMTX/FFmpeg, E2E HA ↔ Go ↔ MediaMTX et rendu Swarm réussis dans le [run CI](https://github.com/mtx26/netvue-ha/actions/runs/44139631957) |
| ✅ | Endurance synthétique | Multi-caméra, caméra défaillante, churn, annulation et libération de goroutines testés dans la [phase 35](../findings/phase35-fiabilite-prolongee-et-performances.md) |
| 🧪 | Stabilité réelle 24 h/24 | Protocole préparé dans la [phase 35](../findings/phase35-fiabilite-prolongee-et-performances.md) ; aucune validation réelle effectuée |
| 🧪 | Performances de référence | Benchmarks et profils CPU/heap du gestionnaire ; aucune optimisation justifiée |

## 2. Audio

| État | Fonctionnalité | Objectif |
|---|---|---|
| 💡 | Analyse APK audio | Comprendre les protocoles audio |
| ⬜ | Réception audio | Récupérer le microphone |
| ⬜ | RTSP audio/vidéo | Publier image et son synchronisés |
| ⬜ | Audio bidirectionnel | Parler vers la caméra |
| ⬜ | Intégration HA | Utiliser le microphone depuis HA |

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
| ⬜ | WebRTC | Réduire la latence |
| ⬜ | Configuration HA | Simplifier les réglages |
| ⬜ | Alertes de panne | Signaler les caméras indisponibles |
| ⬜ | Dashboard | Centraliser les informations |
| 🧪 | Installation Portainer | Guide et stack disponibles ; déploiement réel non validé |

## Règles de suivi

- Mettre à jour les états après chaque développement.
- Distinguer implémentation et validation réelle.
- Ajouter une fonctionnalité lorsqu'un nouveau besoin apparaît.
- Éviter les fonctionnalités redondantes.
- Conserver les décisions techniques détaillées dans les documents adaptés.
- Utiliser cette roadmap comme référence pour les prochaines phases.