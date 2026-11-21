# Phase 60 - Reprise iOS, plein ecran et overlay IA

## Objectif

Verifier et corriger le lecteur historique iOS, le plein ecran live/replay
et les boites de detection IA live/historiques sans perturber WebRTC.

## Decisions

- Conserver les MP4 MediaMTX natifs: un echantillon reel contient H.264 Main
  1920x1080 et AAC-LC mono 8 kHz, avec `moov` avant `mdat`.
- Conserver HTTP `video/mp4`, les reponses Range et les URL Home Assistant
  authentifiees signees; ne pas transcoder H.264 inutilement.
- Utiliser l API Fullscreen standard, les API WebKit lorsqu'elles sont presentes,
  puis un mode plein ecran CSS avec en-tetes de securite et taille dynamique.
- Representer les boites en SVG avec les dimensions originales du flux et
  `preserveAspectRatio="xMidYMid meet"`.
- Utiliser une duree de vie courte pour les boites live et une selection
  historique a +/-2 secondes autour du timestamp affiche.
- Garder les anciens enregistrements sans metadata jouables normalement.

## Modifications

- Le lecteur distingue les contours live et replay dans la carte Lovelace.
- Ajout d un toggle unique qui masque/affiche les boites live ou historiques.
- Les boites live utilisent les coordonnees, dimensions, confiance, categorie
  et track_id emis par le detecteur.
- Le service IA envoie les boites au pont pour un index JSONL borne.
- Les routes Go/HA exposent les metadonnees par plage ou par segment.
- Les routes authentifiees restent distinctes des flux media publics.

## Tests

- 73 tests frontend executes: 73 reussis.
- Suite Go complete executee: tous les tests reussis.
- 13 tests IA executes: tous reussis.
- Sondage reel: MP4 10 minutes, H.264/AAC, taille environ 130 Mo,
  indexation `moov` avant `mdat`.
- `git diff --check`: aucun probleme de formatage.

## Etat final

- Plein ecran live/replay et repli iOS/WebKit: implemente hors ligne.
- Overlay live avec boites reelles: implemente hors ligne.
- Metadonnees historiques: persistance/test Go implementees.
- Lecture replay iPhone/Safari: cause exacte non confirmee, et aucun test
  physique iOS n a ete execute.
- Lecture Home Assistant iOS WebView: validation physique necessaire.

## Limites restantes

- Si le bouton overlay manque après la mise à jour, installer Netvue Local
  `0.2.30` et redémarrer Home Assistant. Le module de la carte change d'URL
  pour éviter de réutiliser celui de `0.2.29`.

- Verifier sur iPhone Safari et Home Assistant iOS que la lecture demarre apres
  geste utilisateur, que la reprise/seek conserve la position et que le Range
  est accepte.
- Verifier un ancien appareil dont le niveau H.264 ou l audio AAC 8 kHz pourrait
  etre limite.
- Valider l alignement live/replay sur camera reelle avec plusieurs objets.

## Suivi 0.2.31 — détections brèves sur Garage

- En production sur `0.2.30`, Home Assistant avait enregistré les six événements
  `zone_entered` signalés. Le capteur Garage avait également reçu une boîte de
  voiture à 17:49:16, puis repassé à `off` au relevé suivant. Les événements ne
  représentent pas la présence courante.
- Une capture de 75 secondes depuis le conteneur Home Assistant a observé
  50 instantanés avec des boîtes `car` (1920 × 1080) et 96 sans objet. Des
  relevés HA espacés de 30 secondes sont tombés sur des instantanés vides
  malgré des boîtes avant et après. La cause des intermittences de l'inférence
  elle-même n'est pas établie.
- Les deux entités d'objets lisent désormais le statut IA toutes les deux
  secondes, sans accélérer la supervision vidéo ni le bus d'événements. Les
  boîtes disparaissent au premier relevé vide ou après six secondes sans image
  fraîche. La lecture par sondage peut encore manquer une détection très brève.
- Le pont de production renvoyait des boîtes historiques en HTTP 200 sur une
  plage de dix minutes. L'alignement à l'écran, en direct et en replay, reste
  à vérifier dans le tableau de bord après déploiement de `0.2.31`.

## Suivi 0.2.33 — alignement des cadres

Le calque SVG est affiché par-dessus la vidéo du lecteur actif et dimensionné
sur l'image, sans les bandes noires ni la frise. Il suit
les changements de dimensions du lecteur et de résolution de la vidéo. Ce
correctif concerne l'affichage, pas les détections ni l'export MP4.
Alignement et superposition vérifiés dans le navigateur avec une vidéo
synthétique en direct, en replay et en plein écran CSS. Caméra réelle et
iPhone non testés pour ce correctif.

## Suivi 0.2.32 — cadres synchronisés et réglages

- Le cadre direct reste affiché jusqu'au prochain état IA reçu par la carte.
  Un état vide l'efface ; un état indisponible le masque. Aucun minuteur
  d'affichage ne peut l'effacer entre deux relevés.
- L'IA enregistre la transition vers un état vide après un état positif. En
  replay, seul le dernier échantillon atteint peut s'afficher : une détection
  à venir n'anticipe plus le cadre. Une coupure de trois secondes protège
  contre les cadres périmés lorsque les métadonnées sont manquantes,
  notamment pour les anciens enregistrements.
- La roue dentée est accessible en direct et en replay. Les choix sont
  indépendants, enregistrés pour cette caméra dans le navigateur. Par défaut,
  les cadres sont visibles à l'écran et absents du MP4 téléchargé.
- Activation en production non effectuée. Pour utiliser les transitions
  historiques et l'export annoté, mettre à jour l'intégration, le pont et le
  service IA.

### Deux voitures dans le Garage

- Sur la capture fournie, le modèle installé renvoyait deux boîtes de voiture
  à 0,474 et 0,388. Au seuil de 0,45, la seconde était écartée.
- Aucun seuil ni aucune durée spéciale pour les voitures : toutes les pistes
  confirmées restent visibles pendant la durée de vie déjà utilisée par le
  tracker (cinq inférences manquées). Seules les observations réelles peuvent
  générer un événement. Le capteur indique aussi le nombre par classe.
- Deux pistes simultanées et leur disparition indépendante sont testées sur
  une séquence synthétique. Les essais de contraste et de recadrage n'ont pas
  confirmé les deux voitures entières au seuil inchangé sur cette capture.
  La comparaison de modèles sur des images nocturnes reste à faire.
- Le seuil de confiance par défaut est passé de 0,45 à 0,38 pour toutes les
  classes. Une valeur déjà enregistrée dans les options d'une caméra est
  conservée ; régler manuellement la caméra existante pour essayer le nouveau
  seuil. Cette baisse peut augmenter les faux positifs.

### Corrections après revue

- Une erreur d'inférence efface immédiatement l'état IA et enregistre un
  marqueur de fin pour le replay. La reprise repart sans pistes périmées.
- Le replay et l'export consultent la même fenêtre, y compris les trois
  secondes qui précèdent le segment. La route historique sans dates garde
  son comportement d'origine ; le direct utilise toujours le statut IA.
- Le pont accepte un seul export annoté à la fois et exige un téléchargement
  complet pour ne pas réencoder à chaque requête partielle. Le MP4 original
  conserve la reprise HTTP. La charge sur un long segment reste à mesurer.
- Libass a besoin d'une police même pour les rectangles ASS vectoriels.
  Le pont embarque DejaVu Sans ; le test d'intégration utilise la même
  police dans son conteneur Home Assistant.
