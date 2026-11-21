# Phases 61-64 - Enregistrement protege, IA, colis et notifications

## Objectif

Completer la conservation permanente, les regles IA automatiques, les evenements colis et les notifications Home Assistant sans envoyer de donnees a un service tiers.

## Implementation

### Phase 61

- Stockage local protege distinct du volume temporaire.
- Index JSON atomique et suppression explicite.
- Bibliotheque Lovelace avec filtres, lecture Range, telechargement, confirmation de suppression et sante du stockage.
- Calcul des trous de couverture et export MP4 sans transcoder les fragments sources inutilement.

### Phase 62

- Règles persistantes par caméra avec catégorie, zone, jours, horaires minuit, confiance et notification.
- Cycle `event_confirmed`, `event_active` et `event_ended`.
- Fenêtres automatiques 120 secondes avant l'événement et 300 secondes après la fin.
- Chevauchements terminés par piste et période de grâce avant fin d'événement.
- Reprise des événements et règles depuis les fichiers locaux après redémarrage.

### Phase 63

- Architecture `package/candidate/present/deposited/disappeared` conservée.
- Aucun poids colis n'est installé automatiquement.
- Le service reste sur YOLO COCO et annonce explicitement `package_detection=unsupported`.
- Le candidat local retenu après recherche est YOLOX-Tiny, uniquement après affinage, validation des licences, précision et charges CPU.

### Phase 64

- Événements confirmés envoyés vers les services `notify.*` configurés.
- Titre, caméra, timestamp, catégorie, zone et action Lovelace locale.
- Délai anti-doublon configurable.
- Aucun lien vidéo public ni image sensible externe.
- La vidéo protégée doit être liée depuis l’événement après vérification du fichier sauvegardé.

## Tests

- Go: suites complètes, tests de race manager/netvue et vérification go vet.
- Python IA: 17 tests locaux réussis.
- Frontend: 36 tests caméra/bibliothèque réussis.
- Home Assistant: les tests nécessitent Python 3.14 et les dépendances 2026.11.

## Limites

- L'export réel MediaMTX n'est pas validé sur la caméra sans une exécution production contrôlée.
- Aucun test iPhone physique n'a été effectué.
- Aucun modèle de colis validé n'a été ajouté.
- Le backend et la carte doivent être construits puis déployés ensemble.

## Navigation libre de la frise

- La sélection de date fonctionne sans enregistrement et garde la fenêtre locale exacte.
- Les périodes vides peuvent être parcourues avec les boutons, la date et le curseur.
- Les plages temporaires sont vertes, les plages protégées violettes.
- Les plages protégées restent sélectionnables après expiration des fichiers temporaires.
- Une plage protégée a priorité sur une zone temporaire chevauchante pour la lecture.

