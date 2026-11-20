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
| 🧪 | Protocole Netvue | Connexion et vidéo fiables ; [guide de reconstruction LAN](../findings/phase44-specification-reconstruction-lan.md) |
| 🧪 | Supervision | Reconnexion et fonctionnement continu |
| 🧪 | Multi-caméra | Sessions indépendantes |
| 🧪 | Home Assistant | Intégration complète |
| 🧪 | Découverte LAN | Découverte UDP réelle confirmée en [phase 35](../findings/phase35-decouverte-lan-ha.md) ; création depuis HA et autres modèles à valider |
| ✅ | CI multi-composants | Go/race/vet, tests HA, MediaMTX/FFmpeg, E2E HA ↔ Go ↔ MediaMTX et rendu Swarm réussis dans le [run CI](https://github.com/mtx26/netvue-ha/actions/runs/44139631957) ; la publication A/V synthétique est désormais incluse |
| 🧪 | Refonte tests et CI (phases 47–48) | PR : tests Go/HA rapides systématiques, jobs d'intégration ignorés uniquement pour les changements documentaires ; validation complète conservée sur `main`. Aucun SKIP toléré, fuzzing et tests E2E synthétiques ; **run GitHub complet vert après la phase 48 à confirmer** ([rapport phase 47](../findings/phase47-refonte-tests-ci.md), [phase 48](../findings/phase48-fiabilite-ci-tests.md)) |
| ✅ | Validation réelle courte (phase 36) | 30 s de H.264 décodées depuis la session active de la caméra ; le digest déployé n'est pas relié au commit local ([rapport](../findings/phase36-validation-reelle.md)) |
| ✅ | Endurance synthétique | Multi-caméra, caméra défaillante, churn, annulation et libération de goroutines testés dans la [phase 35](../findings/phase35-fiabilite-prolongee-et-performances.md) |
| 🧪 | CI d'endurance | `endurance.yml` : race répété, fuzz long, endurance multi-caméra, manuel ou nocturne ; premier run GitHub à confirmer |
| 🧪 | Stabilité réelle 24 h/24 | Protocole préparé dans la [phase 35](../findings/phase35-fiabilite-prolongee-et-performances.md) ; aucune validation réelle effectuée |
| 🧪 | Performances de référence | Benchmarks et profils CPU/heap du gestionnaire ; aucune optimisation justifiée |

## 2. Audio

| État | Fonctionnalité | Objectif |
|---|---|---|
| 🧪 | Analyse APK audio | Analyse statique des trames et directions ; réception AAC confirmée sur la caméra 2, unité de `samplesize` inconnue |
| 🧪 | Réception audio | AAC/ADTS 8 kHz mono décodé sur la caméra 2 ; PCM 16 bits non validé sur caméra |
| 🧪 | RTSP audio/vidéo | AAC-LC mono 8 kHz capturé sur la caméra ; pendant le talk, la publication a redémarré au changement de format audio et a attendu un IDR après changement H.264. Continuité A/V et avertissements DTS restent à confirmer ([phase 45](../findings/phase45-validation-reelle-talkback.md), [phase 39](../findings/phase39-talkback.md)) |
| 🧪 | Audio bidirectionnel Go | Envoi Go direct d'une sinusoïde AAC-LC/ADTS 700 Hz, 0,5 s, -33,5 dBFS RMS ; pic spectral de retour à -53,7 dBFS, contre -62,3/-64,4 dBFS avant/après. Signal compatible avec un retour acoustique, mais haut-parleur et son audible non confirmés ([phase 45](../findings/phase45-validation-reelle-talkback.md)) |
| 🧪 | Microphone Home Assistant | Provider WebRTC opt-in et transport synthétique validés ; microphone navigateur/téléphone→caméra non testé en réel. Contrôle expérimental désactivé par défaut ; `two_way_audio` et bouton restent masqués par défaut ([phase 43](../findings/phase43-microphone-ha.md), [phase 45](../findings/phase45-validation-reelle-talkback.md)) |

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
| 🧪 | WebRTC | Provider HA opt-in, conversion audio et adaptation H.264 pour les offres incompatibles sans modifier le RTSP ; essai navigateur réel à faire. ICE limité à l'IPv4 source en UDP : un tunnel HTTPS seul ne suffit pas à distance |
| 🧪 | Micro à distance sans VPN | Cloudflare TURN facultatif par installation, configuration ICE limitée aux caméras Netvue en mode WebRTC natif ; négociation via relais local testée, Cloudflare et restitution du micro hors LAN à valider |
| 🧪 | Miniature caméra HA | Image complète proportionnelle dans le badge 80 × 80 ; proxy et formats synthétiques testés, affichage réel à confirmer ([phase 46](../findings/phase46-miniature-ha.md)) |
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
