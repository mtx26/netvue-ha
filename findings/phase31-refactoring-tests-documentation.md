# Phase 31 — Tests Go et documentation

## Objectif

Simplifier les tests et les documents actifs sans changer le code de production
ni supprimer les scénarios utiles.

## Modifications

- Les tests HTTP du gestionnaire partagent leurs requêtes et attentes.
- Les tests de session partagent la création et le nettoyage des connexions ;
  les tests vidéo réutilisent l'extraction H.264 MPEG-TS.
- Les cas de validation sont nommés lorsqu'ils gagnent en lisibilité. Les
  douze fichiers de tests Go ont été examinés ; les plus simples sont conservés.
- Les outils standard Go suffisent : aucune bibliothèque n'a été ajoutée.
- La documentation active passe de 8 225 à 3 935 mots. Le guide
  Portainer privilégie l'installation directe ; les rapports historiques
  restent inchangés.
- Le guide suppose que le propriétaire rend l'image GHCR publique ; la
  configuration d'un jeton Portainer a été retirée.

## Fonctionnement

Les helpers restent dans les tests. Les vérifications de protocole, délais,
erreurs, isolation, vidéo et reprise restent présentes. Le guide Portainer
utilise l'image Go et l'intégration Home Assistant actuelle.

## Tests

- `go test ./...`, `go test -race ./...` et `go vet ./...` : réussis.
- Comparaison automatique : 72 tests principaux avant et après ; 120 puis
  126 tests et sous-tests nommés, aucun nom initial disparu.
- Couverture en un passage : 79,0 %, 80,4 % et 77,8 % pour le CLI, le
  gestionnaire et Netvue, avant comme après. Les blocs couverts varient :
  sur trois passages, Netvue mesure 78,5 % avant et 78,4 % après, avec une
  différence sur une branche d'échéance. Le test d'échéance répété dix fois
  couvre les mêmes blocs avant et après ; la cause de la variation n'est pas
  établie.
- Les deux tests MediaMTX/FFmpeg, normalement optionnels, réussissent dans
  Docker sans réseau : décodage vidéo et isolation multi-caméra fictive.
- Relecture indépendante des tests Netvue : aucun défaut concret relevé.
  `gofmt`, `git diff --check` et les liens Markdown relatifs sont vérifiés.

## État final

Refactoring terminé et testé hors ligne. Aucune caméra ni aucun service de
production n'a été contacté ou modifié. La stabilité réelle reste à vérifier.
