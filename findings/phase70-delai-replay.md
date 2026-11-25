# Phase 70 — Délai de lecture des enregistrements

## Objectif et constat

Corriger les erreurs HTTP 502 lors du replay et de la consultation des
métadonnées. Sur le serveur, la liste MediaMTX d'un flux comportant environ
4 365 segments prenait environ 3,15 s seule et 6,1 s avec deux consultations
simultanées, au-delà du délai initial de 5 s. Le pont a renvoyé une erreur
d'expiration en attendant les en-têtes MediaMTX.

## Décisions et modifications

Le pont partage les consultations simultanées de la liste d'un même flux,
sans cache persistant, et applique un délai de 30 s à cette opération. Chaque
appelant conserve ses propres filtres. Les délais des vues d'enregistrements
Home Assistant sont adaptés pour attendre le pont ; les miniatures disposent
de temps supplémentaire pour leur décodage.

## Tests et état

- Tests du gestionnaire Go, compilation et `go vet` du gestionnaire réussis.
- Tests ciblés Go avec détecteur de courses : consultation lente concurrente,
  isolation entre flux, filtres, reprise après erreur et lecture Range.
- Tests du navigateur multimédia Home Assistant réussis dans un conteneur
  temporaire isolé, notamment avec des réponses retardées de 6,1 s.

La correction est locale et n'est pas déployée. La lecture dans la carte sur
l'installation réelle reste à vérifier après déploiement autorisé. Une liste
qui dépasse encore les nouveaux délais peut toujours échouer.
