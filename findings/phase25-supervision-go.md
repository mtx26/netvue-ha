# Phase 25 — Supervision continue Go

## OBJECTIF

Faire évoluer la session Go bornée vers un service `run` qui maintient une
session vidéo et la relance après les pannes récupérables, tout en conservant
une commande `test` à durée limitée. Le même moteur `RunSession` est utilisé
par les deux commandes. Aucune caméra réelle ni installation de production
n'est contactée.

## MODIFICATIONS

- `RunSession` accepte une durée nulle pour rester actif jusqu'à l'annulation
  du contexte. Une durée positive conserve la session bornée à 24 heures
  maximum. Les validations pures de la source, de la caméra et de l'URL RTSP
  précèdent maintenant le préflight réseau afin qu'une mauvaise configuration
  ne soit pas réessayée comme une panne transitoire.
- `RunContinuous` ajoute une seule boucle séquentielle. Elle attend la fin et
  la fermeture d'une session avant toute tentative suivante ; aucun état
  persistant, budget, goroutine de superviseur ou système de quota n'est
  ajouté.
- Politique de reprise : découverte expirée, connexion fermée, interruption
  vidéo, flux H.264 non prêt, erreurs RTSP de démarrage et erreurs réseau
  (`net.Error`) sont récupérables. Le délai démarre à 2 s, double jusqu'à 30 s
  et repart à 2 s après une session d'au moins cinq minutes. Authentification
  refusée, configuration/protocole invalides, média invalide, erreur vidéo
  critique et erreur inconnue arrêtent la supervision. Toutes les réponses
  RTSP 5xx sont transitoires ; les réponses 4xx sont bloquantes.
- `ErrVideoNotReady` sépare l'absence temporaire d'image/IDR des erreurs
  critiques du publisher ; `ErrVideoFatal` identifie les erreurs de génération
  RTP et de création d'encodeur. Les erreurs critiques conservent leur cause.
- Le CLI offre `test` (durée obligatoire) et `run` (jusqu'à l'arrêt).
  Ctrl+C et SIGTERM annulent le contexte ; le nettoyage différé de la session
  se termine avant le code de sortie. L'annulation volontaire n'est pas
  journalisée comme un échec.
- La classification et les tests utilisent des erreurs et des sessions
  synthétiques. Aucun mécanisme de migration, état de production, budget
  journalier ou dépendance supplémentaire n'a été ajouté.

## FONCTIONNEMENT

`RunContinuous` appelle `RunSession` avec une durée nulle. À chaque retour, la
session a fermé sa connexion caméra et son publisher RTSP avant que le
superviseur classe l'erreur. Une fin par annulation parent retourne proprement ;
une erreur non récupérable est remontée à la CLI ; une erreur récupérable
attend le délai de backoff dans un timer annulable avant une nouvelle session.

Le délai de stabilité est mesuré depuis le début de l'appel à `RunSession`.
La session conserve son échéance absolue de démarrage vidéo de cinq secondes ;
une session qui ne produit pas de vidéo ne peut donc pas consommer le délai de
stabilité complet.

## TESTS

Depuis `netvue-go/` :

- `go test ./...` — réussi.
- `go test -race ./...` — réussi.
- `go vet ./...` — réussi.
- `go build ./cmd/netvue-bridge` — réussi.
- `go run ./cmd/netvue-bridge test --help` et `go run ./cmd/netvue-bridge run --help` — réussis.
- Compilation croisée Linux/amd64 avec `CGO_ENABLED=0` — réussie.
- Le test de reconnexion utilise plusieurs erreurs transitoires suivies d'une
  session qui s'arrête ; il vérifie aussi le reset après stabilité, le plafond
  du backoff, le refus d'authentification, les configurations invalides,
  l'arrêt pendant le backoff, le routage des commandes et la classification
  RTSP aux bornes 4xx/5xx (dont 501, 505 et 599).
- Les tests existants couvrent également l'annulation pendant des lectures
  bloquées et la fermeture du publisher. Toute la suite passe sous `-race`.

La compilation Linux avec cgo activé n'a pas été possible sur cet hôte Windows :
les en-têtes Linux requis par le compilateur cgo (`sys/mman.h`, `grp.h`) sont
absents. Le binaire Go pur pour Linux/amd64 compile correctement avec
`CGO_ENABLED=0`.

MediaMTX et FFmpeg, ainsi que leurs variables d'intégration, sont absents.
La publication/décodage H.264 indépendant, le changement de résolution et les
timestamps observés par un décodeur n'ont donc pas été rejoués dans cette
phase. Aucun appareil réel ni service de production n'a été contacté.

## ÉTAT FINAL

Les commandes `test` et `run`, la supervision à un seul endroit, le backoff
plafonné, la classification récupérable/bloquante et l'arrêt par annulation
sont testés hors ligne ; SIGTERM est enregistré par le point d'entrée, mais
le signal n'a pas été envoyé à un processus dans cette phase.
Une caméra fictive peut
déclencher des erreurs transitoires et une reconnexion sans concurrence entre
sessions.

Limites restantes : une caméra par processus, aucune stabilité prolongée ou
compatibilité caméra vérifiée en conditions réelles, et aucun décodage
indépendant MediaMTX/FFmpeg exécuté dans cette phase. Prochaine étape :
rejouer ces validations vidéo avec les outils disponibles localement, puis
concevoir l'extension multi-caméra sans introduire un second superviseur.
