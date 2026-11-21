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
- Les travaux terminés sont retirés des tableaux ; les lignes restantes sont
  les développements ou validations encore nécessaires.

## 1. Fiabilité et intégration

| État | Fonctionnalité | Objectif |
|---|---|---|
| 🧪 | Reconnexion réelle | Vérifier la reprise après une panne sur caméra réelle ; la stabilité réelle 24 h est suivie séparément |
| 🧪 | Multi-caméra réelle | Sessions indépendantes testées en synthétique ; valider plusieurs caméras simultanément en conditions réelles |
| 🧪 | Parcours Home Assistant réel | Valider le parcours complet avec le code actuel, de l'entrée HA jusqu'à l'aperçu caméra ; l'interface et l'aperçu ne sont pas vérifiés avec la version actuelle |
| 🧪 | Métadonnées LAN | Série, firmware, capacités, SSID et niveau de signal exposés et testés hors ligne ; présence réelle des champs et compatibilité d'autres modèles à vérifier |
| 💡 | Suivi d'IP par numéro de série | En production, une caméra derrière un répéteur a changé d'IP (DHCP) et le pont réessayait indéfiniment l'ancienne ; retrouver la caméra par découverte LAN et numéro de série ([phase 40](../findings/phase40-transcodage-webrtc-partage.md)) |
| 💡 | Informations appareil supplémentaires | Étudier modèle, version matérielle, MAC, IP Wi-Fi/Ethernet, propriétaire et état cloud ; sources et faisabilité locale à vérifier, sans dépendance cloud obligatoire |
| 🧪 | Stabilité réelle 24 h/24 | Sessions réelles bornées d'environ 10 à 13 min observées sur l'ancien pont ([phases 11](../findings/phase11-recovery.md), [15](../findings/phase15-real-stability.md)) ; aucune endurance de 24 h ni validation équivalente du code Go actuel |
| 🔄 | Refonte architecturale | Migration et limites suivies dans [refactor-progress.md](./refactor-progress.md) ; E2E média et parcours HA conteneurisé synthétiques réussis, refonte incomplète |

## 2. Audio

| État | Fonctionnalité | Objectif |
|---|---|---|
| 💡 | Signification de `samplesize` | L'analyse statique est faite ; l'unité de ce champ reste inconnue ([spécification LAN](./netvue-lan-client-spec.md)) |
| 🧪 | Continuité RTSP audio/vidéo | Les essais réels ont montré des interruptions au changement de format ; confirmer la continuité A/V et résoudre les avertissements DTS ([phases 39](../findings/phase39-talkback.md), [45](../findings/phase45-validation-reelle-talkback.md)) |
| 🧪 | Restitution acoustique du talkback Go | Un retour audio a été mesuré, sans preuve de son audible ; confirmer la restitution du haut-parleur par une écoute ou mesure indépendante ([phase 45](../findings/phase45-validation-reelle-talkback.md)) |
| 🧪 | Microphone Home Assistant | Lecteur unique `netvue-camera-card` (push-to-talk momentané, half-duplex, sans renégociation) et correction du bourrage RTP validés hors ligne ; CI complète après la phase 54 réussie ([run](https://github.com/mtx26/netvue-ha/actions/runs/44823857272)). Restitution réelle à valider ([phase 54](../findings/phase54-lecteur-unifie-push-to-talk.md), [phase 45](../findings/phase45-validation-reelle-talkback.md)) |

## 3. Enregistrement

| État | Fonctionnalité | Objectif |
|---|---|---|
| ⬜ | Mesures de capacité | Débit, stockage et ressources |
| 🧪 | Enregistrement manuel et continu | Contrôle Go/HA, fMP4 MediaMTX et réglages persistants implémentés ; validation réelle nécessaire |
| 🧪 | Rétention | Rétention temporelle, limite globale et volume persistant configurés ; validation synthétique nécessaire |
| 🧪 | Historique | Liste, filtrage, lecture/export et suppression via API locale implémentés ; interface HA et E2E à valider |
| 🧪 | Exportation | Téléchargement MP4 via MediaMTX et proxy HA authentifié implémenté ; validation réelle nécessaire |
| ⬜ | Captures | Enregistrer des images |

## 4. Détection et statistiques

| État | Fonctionnalité | Objectif |
|---|---|---|
| 💡 | Détection de mouvement | Comparer Netvue, Go et Frigate |
| ⬜ | Historique des événements caméra | Mouvement et alertes consultables depuis Home Assistant ; nécessite une source d'événements |
| ⬜ | Zones de détection | Configurer les zones surveillées |
| ⬜ | Notifications HA | Signaler les événements |
| ⬜ | Statistiques | Suivre événements et disponibilité |
| 💡 | Détection d'objets | Personnes, animaux et véhicules |

## 5. Expérience utilisateur

| État | Fonctionnalité | Objectif |
|---|---|---|
| 🧪 | WebRTC | Un essai réel a rencontré une offre navigateur incompatible avec le profil H.264 du flux ; adaptation testée hors ligne, lecture WebRTC réelle non confirmée. Un FFmpeg d'adaptation par caméra, partagé entre spectateurs (phase 40) : 9 transcodeurs simultanés avaient saturé la limite de 0,5 CPU en production. ICE limité à l'IPv4 source en UDP : un tunnel HTTPS seul ne suffit pas à distance |
| 🧪 | Micro à distance sans VPN | Cloudflare TURN facultatif par installation, configuration ICE limitée aux caméras Netvue en mode WebRTC natif ; négociation via relais local testée, Cloudflare et restitution du micro hors LAN à valider |
| 🧪 | Carte caméra Lovelace | Lecteur WebRTC, audio, push-to-talk, plein écran et éditeur visuel ; affichage réel à confirmer |
| 🧪 | Mur et synthèse caméra Lovelace | Grille multi-caméras, regroupement état/diagnostic, découverte automatique des entités et éditeurs visuels ; affichage réel à confirmer |
| 🧪 | Miniature caméra HA | Image complète proportionnelle dans le badge 80 × 80 ; proxy et formats synthétiques testés, affichage réel à confirmer ([phase 46](../findings/phase46-miniature-ha.md)) |
| 🧪 | Configuration HA réelle | Vérifier dans l'interface installée le parcours découverte/saisie IPv4, la création de l'entrée et l'affichage des entités |
| 🧪 | Alertes de panne | Entité « Caméra indisponible » et diagnostics actionnables ajoutés ; tests HA/Portainer passés en conteneur isolé, état réel HA non vérifié |
| ⬜ | Dashboard | Centraliser les informations |

## Règles de suivi

- Mettre à jour les états après chaque développement.
- Distinguer implémentation et validation réelle.
- Ajouter une fonctionnalité lorsqu'un nouveau besoin apparaît.
- Éviter les fonctionnalités redondantes.
- Conserver les décisions techniques détaillées dans les documents adaptés.
- Utiliser cette roadmap comme référence pour les prochaines phases.
