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
| 🧪 | Métadonnées LAN | Série, firmware, capacités, SSID et niveau de signal exposés et testés hors ligne ; présence réelle des champs et compatibilité d'autres modèles à vérifier |
| 💡 | Informations appareil supplémentaires | Étudier modèle, version matérielle, MAC, IP Wi-Fi/Ethernet, propriétaire et état cloud ; sources et faisabilité locale à vérifier, sans dépendance cloud obligatoire |
## 2. Audio
## 3. Enregistrement

| État | Fonctionnalité | Objectif |
|---|---|---|
| 🔄 | Conservation sélective | Export multi-segments et détection des trous testés synthétiquement ; limites d'espace, reprise après panne et validation réelle restent à consolider ([stockage protégé](protected-recordings.md)) |
| 🔄 | Enregistrement IA automatique | Règles persistantes, cycle confirmé/actif/terminé, pré-événement 120 s et post-événement 300 s ; protection réelle et validation caméra nécessaires |
| 💡 | Détection de colis | Architecture temporelle et état persisté ; aucun modèle ONNX de colis fiable/licencié n'est encore validé |
| 🧪 | Notifications intelligentes | Notifications HA locales configurables, délai anti-doublon et liens Lovelace ; snapshot/iPhone/validation production nécessaires |
| 🧪 | Exportation | Téléchargement MP4 authentifié ; export annoté complet, un à la fois, avec les mêmes boîtes que le replay. Le téléchargement original reste inchangé. Validation réelle nécessaire |
| ⬜ | Captures | Enregistrer des images |

## 4. Détection et statistiques

| État | Fonctionnalité | Objectif |
|---|---|---|
| 💡 | Détection de mouvement | Comparer Netvue, Go et Frigate |
| 💡 | Comparaison Frigate | Tester gratuitement Frigate en local sur les flux RTSP existants, sans remplacer Netvue AI ; comparer précision, alertes et charge CPU sur le serveur actuel, sans envoi d'images ni matériel supplémentaire. Intégration HA via MQTT à étudier ; essais réels soumis à autorisation |
| ⬜ | Historique des événements caméra | Mouvement et alertes consultables depuis Home Assistant ; nécessite une source d'événements |
| 🧪 | Zones de détection | Une zone rectangulaire via la roue dentée de chaque carte ; toute l'image par défaut et bouton pour la resélectionner ; tests hors ligne, validation réelle nécessaire |
| 🧪 | Notifications HA | Six événements voiture confirmés dans l'historique HA du Garage ; une automatisation de notification réelle reste à valider |
| 🧪 | Statistiques | Latence, FPS, ratio d'images positives, images ignorées et erreurs par caméra dans l'API IA ; exposition HA implémentée, recharge réelle à valider ([phase 59](../findings/phase59-detection-accuracy-performance.md)) |
| 🧪 | Détection d'objets | Suivi indépendant des objets, compteur par catégorie et réglages séparés pour le direct, le replay et l'export. Alignement des cadres vérifié hors ligne ; flux réel à valider et modèles nocturnes à comparer. Classes COCO seulement ([phase 60](../findings/phase60-ios-fullscreen-ai-overlay.md)) |

## 5. Expérience utilisateur

| État | Fonctionnalité | Objectif |
|---|---|---|
| ⬜ | Dashboard | Centraliser les informations |
| 💡 | Refaire le readme | Mettre à jour le fichier README avec les informations actuelles sur le projet et les fonctionnalités implémentées |
| 💡 | Mettre à jour la documentation | Refaire tous les fichiers de documentation pour refléter les informations actuelles sur le projet et les fonctionnalités implémentées |
| 💡 | Vérification du changement d'heure | S'assurer que le système gère correctement les changements d'heure et que les événements et statistiques sont correctement horodatés |

## Règles de suivi

- Mettre à jour les états après chaque développement.
- Distinguer implémentation et validation réelle.
- Ajouter une fonctionnalité lorsqu'un nouveau besoin apparaît.
- Éviter les fonctionnalités redondantes.
- Conserver les décisions techniques détaillées dans les documents adaptés.
- Utiliser cette roadmap comme référence pour les prochaines phases.
