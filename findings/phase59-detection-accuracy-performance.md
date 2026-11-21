# Phase 59 — Précision, cadence et diagnostics IA

## Objectif

Corriger les défauts confirmés du décodage YOLO, réduire les alertes répétées
et mesurer le service sur le serveur et les flux Netvue actifs, sans toucher
à Home Assistant, MediaMTX ni aux enregistrements.

## État initial mesuré

Serveur Debian 13, Ryzen 3 5400U, huit processeurs logiques, limite du service
IA de 1,5 CPU et 768 Mio. Deux flux 1920 × 1080 étaient actifs.
Dans `docker stats`, 100 % représente environ un processeur logique consommé.

Avant le changement, quatre échantillons `docker stats` sur environ 40 s
donnaient **109,5 % CPU en moyenne** (95,69–117,86 %) et **220,6–237,2 Mio**
de mémoire. Le service ne publiait ni latence ni compteur d’inférence ; le
débit et la précision ne peuvent donc pas être reconstruits rétrospectivement.

L’API et les journaux contenaient **195 événements en 15 min**, tous
`zone_entered / car / entrance`. Cela démontre une répétition excessive,
mais pas que les classifications étaient fausses : aucune image étiquetée
ou observation visuelle ne permettait de confirmer les voitures.

## Modifications

- Prétraitement YOLO avec conservation du rapport d’aspect, letterboxing 640 ×
  640 et retrait du padding au retour aux coordonnées de l’image. Le calcul
  précédent étirait l’image et projetait incorrectement les boîtes lorsque
  le flux n’était pas carré.
- Filtrage vectorisé des classes et suppression non maximale par classe
  (IoU 0,45). Le modèle ONNX exporté sans NMS pouvait produire plusieurs
  alertes pour des boîtes superposées du même objet.
- Refroidissement d’événement de 30 s par caméra, type, catégorie et zone,
  plutôt que par identifiant de piste seulement.
- Échantillonnage adaptatif : **1 inférence/s au repos**, retour à la cadence
  configurée dès qu’une détection est observée, conservée pendant 2 s après
  la dernière détection. L’inférence périodique demeure donc active même sur
  une image immobile. Aucun filtre de mouvement OpenCV n’est ajouté.
- Métriques par caméra dans l’API existante : latence de la dernière
  inférence, FPS d’inférence observé, ratio d’images avec détection confirmée,
  images décodées/ignorées, compteurs d’inférence et d’erreurs, dernière
  erreur. Ce ratio mesure la fréquence des images positives ; **ce n’est pas
  une mesure de précision**.
- Home Assistant lit les métriques sur le binaire IA existant, signale
  l’indisponibilité du service IA dans le diagnostic et borne la mémoire des
  identifiants d’événements. Les champs `entry_id`, `camera` et `host` de
  l’intégration ne peuvent plus être écrasés par le contenu d’un événement.
- Aucun nouvel entity, dépendance, backend Prometheus ou modèle n’est ajouté.
  Les événements gardent leur timestamp Unix en secondes, catégorie,
  confiance, piste et zone.

## Modèles de colis évalués

Le modèle déployé reste YOLOv8n ONNX CPU, exporté depuis `ultralytics==8.2.0`
et l’asset [Ultralytics v8.2.0](https://github.com/ultralytics/assets/releases/tag/v8.2.0).
Son dépôt de code est sous AGPL-3.0
([licence v8.2.0](https://github.com/ultralytics/ultralytics/blob/v8.2.0/LICENSE)).
Les classes exposées par Netvue restent le sous-ensemble COCO déclaré dans le
code ; aucune ne signifie « colis ».

Candidats examinés, sans télécharger ni exécuter leurs poids :

- [Mact0/RF-DETR Package Detection](https://huggingface.co/Mact0/rf-detr-package-detection) :
  licence Apache-2.0 déclarée, modèle RF-DETR Medium d’environ 135 MB
  (129 Mio), deux classes
  non nommées dans la fiche et jeu de données personnalisé peu décrit. La
  fiche ne fournit pas de score vérifiable dans les informations consultées.
  Rejeté : provenance/évaluation insuffisantes et poids PyTorch trop lourds
  pour justifier l’essai sur ce serveur CPU.
- [manako/Detect-delivery-package-detection](https://huggingface.co/manako/Detect-delivery-package-detection) :
  ONNX YOLOv11 nano et classes annoncées « livreur », boîte, enveloppe,
  sac d’expédition et livraison alimentaire ; licence absente et score
  d’évaluation vide. Rejeté faute de provenance, licence et validation
  reproductible.
- [industoai/Package-Detection](https://huggingface.co/industoai/Package-Detection) :
  licence CC BY-NC 4.0, poids PyTorch de segmentation d’environ 144 MB
  (137 Mio).
  Rejeté : non commercial, tâche et coût différents, aucune précision locale
  vérifiée.
- [tarabukinivan/sn44-delivery-package-detection-v1](https://huggingface.co/tarabukinivan/sn44-delivery-package-detection-v1) :
  artefact ONNX d’environ 10,6 Mio, mais pas de fiche, licence, classes,
  données ou évaluation vérifiables. Rejeté.

**Capacité colis/livraison : non prise en charge.** Un événement « colis
déposé », « colis resté », « colis disparu » ou « livraison possible » serait
une interprétation trompeuse avec les classes actuelles ; aucun n’est émis.

## Mesures après changement

L’image a été construite sur le serveur à partir de l’image précédemment
déployée, sans reconstruire ni télécharger le modèle, puis testée hors réseau.
Le service IA seul a été mis à jour ; l’image active est
`netvue-ai:phase59-adaptive-20261121`.

| Mesure | Avant | Après cadence adaptative |
|---|---:|---:|
| CPU conteneur IA | moyenne **109,5 %** sur 4 échantillons | moyenne **70,2 %** sur 4 échantillons/42 s (63,60–80,62 %) |
| Inférences sur 30 s, 2 caméras | environ **150** avec cadence fixe | **100** : 25 sur la caméra sans détection, 75 sur celle avec détections |
| Caméra sans détection confirmée | environ **2,5 inférences/s** | environ **0,83/s**, détection périodique conservée |
| Caméra avec détections confirmées | environ **2,5/s** | environ **2,5/s** |
| Latence observée par inférence après adaptation | non disponible auparavant | environ **75–80 ms** au dernier relevé |
| RAM conteneur IA | 220,6–237,2 Mio | 212,4–244,7 Mio, avec pointe transitoire au démarrage |
| Erreurs IA après adaptation | non exposées | **0** sur l’échantillon |

La baisse de CPU observée est d’environ **36 % par rapport à la moyenne
initiale**. Après le premier échantillon de la version adaptative, les trois
derniers ont une moyenne de **69,3 %**, contre **94,6 %** pour les trois
échantillons stables de la version sans adaptation (baisse d’environ 27 %).
L’échantillon est court et non randomisé ; il ne constitue pas un benchmark
d’endurance. La mémoire reste sous 32 % de sa limite, sans baisse conclusive.
La cadence configurée est conservée pendant les détections ; le gain vient de
la caméra calme.

Après déduplication, **un événement** a été observé pendant la première minute
sur le flux auparavant répétitif, contre 13/min dans la fenêtre initiale.
C’est un indice de réduction du spam, pas une preuve de précision. Les deux
flux restaient en état `detecting`; aucune erreur d’inférence n’a été relevée.

## Home Assistant, vidéo et tests

- Tests IA : **13 réussis** localement et dans un conteneur dérivé isolé.
  Ils couvrent les coordonnées non carrées, la suppression d’une boîte
  superposée, le refroidissement, la cadence de veille et la récupération
  après erreur d’inférence.
- Tests Home Assistant : **110 réussis**, **68 sous-tests réussis** ;
  Ruff (`E4,E7,E9,F`) sans erreur. Avertissements HA/AIOHTTP préexistants.
- Deux chemins MediaMTX sur deux étaient prêts après chaque déploiement.
  Home Assistant, le pont et MediaMTX sont restés actifs sans redémarrage
  observé. Aucun déploiement ou redémarrage de Home Assistant n’a été fait.
- La lecture WebRTC et la lecture d’un enregistrement n’ont pas été
  déclenchées artificiellement : seul l’état des services et des chemins
  MediaMTX a été contrôlé. Aucun fichier d’enregistrement n’a été modifié.

Le code d’intégration HA et ses tests sont dans le dépôt, mais l’instance HA
en production n’a pas été rechargée ; ses nouvelles métriques et son diagnostic
`ai_unavailable` ne seront visibles qu’après une mise à jour/recharge HA sûre.

## Choix non retenus et limites

- Pas de ByteTrack : aucune séquence annotée n’est disponible pour démontrer
  un gain sur le suivi IoU simple, les occlusions ou les croisements.
- Pas de filtre de mouvement OpenCV : l’échantillonnage adaptatif fondé sur
  les détections donne un gain CPU mesuré tout en conservant une inférence
  périodique.
- Pas d’accélération VAAPI : FFmpeg annonce la prise en charge VAAPI, mais
  `/dev/dri` n’est pas exposé au conteneur IA. Aucun GPU AMD n’est utilisé.
- Pas d’évaluation d’exactitude avec une vérité terrain, de rappel sur objets
  furtifs, de taux de faux positifs visuellement confirmé, ni d’endurance
  longue. La caméra qui signale « car » doit encore être validée visuellement.
- Aucun modèle colis ou logique de livraison n’est activé.
- L’historique d’enregistrement ne reçoit pas de marqueurs IA ; l’API
  d’enregistrement actuelle n’a pas été modifiée.

## Retour arrière

Le service IA est un service Swarm indépendant. Pour revenir précisément à
l’image d’origine enregistrée avant la phase 59 :

```sh
docker service update \
  --image ghcr.io/mtx26/netvue-ai:latest@sha256:b066ee964408d063e6ccadf18ac3e1aa18761261af07c6a4a49b343f2aba5689 \
  --no-resolve-image --detach=false netvue_netvue-ai
```

Cette opération ne redémarre ni Home Assistant, ni le pont, ni MediaMTX.
L’image IA actuellement exécutée a été construite localement sur le serveur ;
elle n’a pas été publiée dans GHCR.
