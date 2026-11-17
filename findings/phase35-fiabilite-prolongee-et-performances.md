# Phase 35 — Fiabilité prolongée et performances

## Objectif et décisions

Tester hors ligne la durée de vie des sessions, l'isolation multi-caméra, le
churn du gestionnaire et ses coûts de base. Aucune caméra, compte Netvue,
stack ou service de production n'a été contacté ni modifié.

L'audit indépendant n'a pas trouvé de défaut concret de fermeture, de
concurrence ou d'isolation dans les chemins examinés. Les mesures ne montrent
pas de hotspot applicatif qui justifierait une optimisation ; le code de
production reste inchangé.

## Tests ajoutés

[`internal/manager/endurance_test.go`](../internal/manager/endurance_test.go)
ajoute :

- Huit sessions synthétiques saines simultanées et une caméra en échec ; arrêt
  d'une session et fermeture sans effet sur les autres.
- 32 cycles ajout/arrêt/reprise/suppression, puis quatre sessions actives
  pendant une fenêtre d'une seconde avec heartbeats.
- Six caméras effectuant chacune 20 cycles concurrents d'arrêt/reprise,
  vérification des démarrages, de l'annulation et de l'absence de session
  active après fermeture.
- Vérification bornée du retour des goroutines à la référence avec une
  tolérance de trois goroutines ambiantes.
- Benchmarks d'un cycle ajout/arrêt/reprise/suppression et d'instantanés de
  16 caméras.

## Mesures de référence

Commande : `go test ./internal/manager -run '^$' -bench '^BenchmarkManagerEndurance' -benchtime=3s -benchmem -count=1 -cpuprofile <temp>/cpu.pprof -memprofile <temp>/mem.pprof`

| Mesure | Avant | Après, code de production inchangé |
|---|---:|---:|
| Cycle complet du gestionnaire | 5,121 µs/op | 4,631 µs/op |
| Allocations du cycle | 867 B/op ; 22 allocs/op | 867 B/op ; 22 allocs/op |
| Instantané de 16 caméras | 1,703 µs/op | 2,412 µs/op |
| Allocations de l'instantané | 1 560 B/op ; 4 allocs/op | 1 560 B/op ; 4 allocs/op |
| Profil CPU du test | 10,95 s CPU / 9,03 s profil (121,3 %) | 11,49 s CPU / 9,52 s profil (120,7 %) |
| Heap retenu en fin de profil | 2,56 MiB | 2,00 MiB |
| Goroutines après churn et fermeture | — | 2, identique à la référence du test |

Les latences profilées sont des échantillons uniques, sensibles à
l'ordonnancement. Cinq répétitions sans profil ont donné 4,336–6,412 µs/op
pour le cycle et 2,072–2,252 µs/op pour l'instantané. Les allocations sont
restées identiques ; aucun gain ou régression attribuable à la phase n'est
établi. Le profil comprend le runtime des tests et le heap retenu n'est pas le
RSS d'une instance déployée. Ces nombres servent de référence locale, pas de
seuil de capacité. Le profil CPU met principalement en évidence le runtime Go
et le tri des instantanés ; aucun travail applicatif dominant n'a été démontré.
Les fichiers de profil temporaires ont été supprimés.

## Validation hors ligne

- `go test -count=1 -timeout=120s ./...` : réussi.
- `go test -count=1 -race -timeout=180s ./...` : réussi.
- `go vet ./...` et `go build ./...` : réussis.
- Tests endurance répétés : `go test -race ./internal/manager -run '^TestManagerEndurance' -count=5 -timeout=90s` : réussi.
- Tests protocole, sessions et vidéo avec pairs locaux : réussis dans la suite Go.
- Les tests MediaMTX/FFmpeg nécessitent des exécutables externes absents de cet
  environnement ; aucune intégration vidéo multi-publisher locale n'est
  revendiquée. Le run CI antérieur mentionné dans la roadmap n'est pas un
  résultat de cette phase.

Les tests Go complets ont été lancés séquentiellement. Deux commandes de test
simultanées ont d'abord tenté d'utiliser le même port UDP fixe sous Windows ;
les validations séquentielles complètes ont ensuite réussi.

## Protocole de validation réelle sur 24 h

À exécuter uniquement après autorisation explicite désignant la caméra, la
fenêtre, les opérations et le plafond de tentatives. La présente phase ne
l'exécute pas.

1. Utiliser une image Go identifiée par digest, un MediaMTX/RTSP de validation
   isolé et un lecteur unique ; ne pas redéployer ni modifier la stack de
   production. Éviter les sessions concurrentes sur la caméra.
2. Avant le départ, enregistrer le commit/digest, l'état des services, la
   configuration et les compteurs. Fixer à l'avance le nombre maximal de
   reconnexions autorisées. Ne pas modifier la caméra, son firmware ou ses
   paramètres.
3. Maintenir le flux pendant 24 h. Chaque minute, relever l'état via
   `GET http://127.0.0.1:8765/api/v1/status`, les journaux du pont et
   `docker stats --no-stream` (CPU, mémoire, PIDs). Garder un lecteur RTSP
   décodant en continu et relever les interruptions, la progression des
   images et les horodatages.
4. Interrompre le test en cas d'état `failed`, d'échecs répétés, de dépassement
   du plafond autorisé, de croissance mémoire soutenue ou de saturation
   durable des ressources. La stack réserve 0,50 CPU et 512 MiB au pont ;
   ces limites sont des garde-fous de déploiement, pas une référence de
   performance observée.
5. À la fin, conserver les mesures et journaux expurgés, confirmer la fermeture
   de la session et comparer CPU, mémoire, images, erreurs et délais de reprise
   au début. Les interruptions, suppressions et reprises actives sur caméra
   restent hors protocole sauf autorisation séparée.

Le service n'expose actuellement ni métriques Go ni endpoint pprof :
goroutines et allocations réelles ne peuvent pas être observées par cette
procédure sans instrumentation dédiée. Elles sont mesurées ici dans les tests
et benchmarks synthétiques ; le RSS réel et la latence vidéo de bout en bout
restent à mesurer pendant une validation autorisée.

## État final et limites

Les tests synthétiques et la référence de performance sont établis ; aucun
correctif de production ni optimisation n'était justifié par les résultats.
La stabilité réelle sur 24 h, les ressources de l'instance déployée, la
latence vidéo réelle et la validation MediaMTX/FFmpeg locale restent à faire.
