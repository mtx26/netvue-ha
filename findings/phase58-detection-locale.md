# Phase 58 — Détection locale

## Objectif

Ajouter un service local isolé de détection d'objets réutilisant les flux RTSP
existants, puis exposer son état et ses événements dans Home Assistant.

## Décisions

- Détecteur initial : YOLOv8n ONNX Runtime CPU.
- Classes initiales : personnes, chats, chiens, oiseaux, bicyclettes,
  voitures, motos, bus et camions.
- Suivi : IoU simple avec confirmation temporelle et refroidissement.
- Zones : rectangles normalisés configurés par nom de flux.
- Détection de colis : non activée ; aucune classe COCO fiable n'est assimilée
  automatiquement à un colis.
- Accélération AMD : non déclarée. Le conteneur reste sur CPU jusqu'à mesure
  démontrée sur le serveur.
- Interface locale : API loopback sur le port 8766, sans publication Docker.

## Modifications

- Nouveau service `ai-service/` avec Dockerfile, modèle, API et tests unitaires.
- Nouveau service Swarm `netvue-ai` avec limites CPU/mémoire et sécurité
  minimale.
- Intégration HA : capteur de détection, switch d'activation et événements
  `netvue_local_event`.
- Configuration IA : activation, seuil de confiance, cadence, confirmation
  et zones JSON disponibles directement lors de l'ajout manuel ou découverte
  de la caméra, puis modifiables dans les options de l'intégration.
  La roue dentée de chaque carte caméra ouvre le dessin des zones ; l'interface
  reste à valider dans Home Assistant réel.
- Roadmap et architecture mises à jour.
- Correction du lecteur IA : remplacement de `cv2.VideoCapture` par PyAV.
  Un H.264 synthétique 360p → 1080p → 360p reproduit avec OpenCV 4.12
  les erreurs `Slice parameters 0, 1080 are invalid` et des dimensions
  incorrectes. PyAV restitue les neuf images avec les dimensions attendues.
  Cela explique une cause reproductible du journal fourni, sans confirmer
  le changement de résolution sur la caméra réelle.

## Tests

- Tests unitaires du suivi IoU et des zones.
- Régression synthétique multi-résolution, pixels, fermeture du lecteur et
  remise à zéro de la confirmation du suivi lors d'un changement de taille.
- Test de configuration Portainer du conteneur IA.
- Tests Go/Python à relancer après les changements.
- Suite HA exécutée dans un conteneur isolé : 106 tests et 63 sous-tests
  réussis ; 64 tests frontend réussis.

Le changement d'adresse de la caméra reste possible lorsque le service IA est
indisponible et conserve les options IA de l'entrée.

## État

Implémentation hors ligne et service déployé sur le serveur réel. Deux flux
Netvue actifs ont été découverts; une caméra atteignait l'état `detecting`.
Mesure stabilisée sur une charge réelle : environ 59 % CPU et 198 Mio RAM pour
le conteneur IA, sans détection confirmée pendant la fenêtre observée.

L'intégration HA 0.2.22 a été sauvegardée puis installée dans
`custom_components/netvue_local` sans redémarrage du conteneur HA. Le
redémarrage ou rechargement ne sera pas lancé automatiquement afin de préserver
le lecteur WebRTC existant.

Validation restante : catégories réellement visibles, précision/retard des
événements, endurance 24 h, switch depuis HA après chargement, intégration du
colis, accélération AMD/VAAPI et mesures multi-caméras plus longues.
