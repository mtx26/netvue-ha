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
