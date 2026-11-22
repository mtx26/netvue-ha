# Phase 68 — Reprise réseau

## Objectif et constats

Améliorer la reprise après coupure sans modifier la stack de production. Les
journaux existants de la caméra autorisée montrent des expirations vidéo à
10 s et des échecs de découverte. Aucun événement 503 associé à cette caméra
n'a été trouvé dans les trois heures consultées : sa cause reste inconnue.
Trois requêtes UDP ciblées ont reçu trois réponses à enveloppe valide ; ce
test bref ne mesure ni stabilité ni taux de perte.

## Modifications

- Après un login réussi, réutilisation en mémoire de l'endpoint et de la clé ;
  redécouverte après erreur réseau ou refus de cette authentification. Une
  découverte fraîche suivie d'un refus de login reste fatale.
- Backoff Go variable, plafonné à 30 s ; poursuite des essais récupérables du
  lecteur avec backoff variable également plafonné à 30 s.
- Délai de silence vidéo porté à 30 s. Paquets identiques avec PTS identique
  non republiés ; PTS figé durant 30 s traité comme une interruption.
- Journaux 503 supplémentaires pour les encodeurs et l'arrêt du gestionnaire,
  avec adresse caméra et catégorie, sans erreur interne sensible.
- Santé vidéo distincte du cycle de vie du superviseur : dernière publication,
  état, échecs de reconnexion, durée de coupure estimée et dernier diagnostic
  assaini. La carte indique la reprise lorsque HA connaît le diagnostic.
- Réapplication de la configuration d'enregistrement après reprise et essais
  espacés si MediaMTX ne répond pas. L'état actif exige aussi une configuration
  MediaMTX activée, sans garantir l'écriture des fichiers.
- L'IA ignore une image décodée identique portant le même PTS que la dernière
  image analysée ; une scène identique avec un nouveau PTS reste analysable.

## Tests et limites

Les tests synthétiques couvrent une perte des réponses UDP après authentification,
une clé périmée, une coupure TCP suivie d'une republication RTSP, les doublons
vidéo, les délais aléatoires, un redémarrage avec changement de port, le
blocage vidéo sur TCP ouvert et la reprise du lecteur et de la configuration
d'enregistrement. `go test -race ./...`, `go vet ./...`, `go build ./...`,
les 89 tests frontend et les 22 tests IA ont réussi. Le parseur de santé HA a
été testé avec des modules HA factices ; les tests HA complets ne peuvent pas
s'exécuter sans Home Assistant. Les tests nécessitant MediaMTX ou FFmpeg ont
été ignorés faute d'outils disponibles localement.

Sur Windows, le microbenchmark de 16 instantanés caméra prend environ 5,6 µs
par appel avec santé contre 2,7 µs sans santé ; ce n'est pas une mesure CPU
du système complet ni une évaluation de stabilité en production.

Une image visuellement figée mais reçue avec des PTS et charges utiles
différents ne peut pas être distinguée de façon fiable d'une scène immobile
avec ces seuls indices. Les cycles d'événements IA et les états de colis après
coupure restent à vérifier : une présence antérieure ne doit pas devenir une
disparition artificielle. La reprise réelle des enregistrements, les trous
sur fichiers, les mesures CPU globales et les essais sur caméra restent à
valider.
Aucun déploiement,
redémarrage, modification de Home Assistant ou de la stack, ni suppression
d'enregistrement n'a été effectué.
