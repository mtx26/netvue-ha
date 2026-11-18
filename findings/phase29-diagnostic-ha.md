# Phase 29 — Diagnostic et reprise dans Home Assistant

## OBJECTIF

Rendre visibles l'état des sessions et les erreurs définitives utiles, puis
permettre la reprise manuelle d'une caméra arrêtée ou en échec sans perturber
les autres. Aucun contact avec les caméras réelles ni changement de
production n'est autorisé.

## MODIFICATIONS

- `internal/manager/manager.go` classe les erreurs définitives en messages
  fixes non sensibles, exposés par l'API et les journaux du gestionnaire ; les
  détails bruts ne sont plus affichés.
- L'intégration Home Assistant ajoute un capteur de session, un bouton de
  reprise et leurs traductions. Le client réutilise la route Go existante
  `/api/v1/cameras/{host}/start` et le verrou de chaque caméra.
- Le coordinator conserve distinctement l'état Go et la disponibilité RTSP ;
  la caméra et le capteur binaire continuent de dépendre de la sonde RTSP.
- Les tests Go et HA couvrent les nouvelles opérations. Les guides HA et
  d'architecture et le plan Go décrivent les comportements et leurs limites.
- Aucun superviseur, API supplémentaire ou traitement vidéo n'a été ajouté.

## FONCTIONNEMENT

Le capteur `sensor.py` affiche `supervising`, `stopping`, `stopped` ou
`failed` ; son attribut `error` contient, si applicable, la catégorie d'erreur
renvoyée par Go, en anglais. Le bouton `button.py` n'est disponible que pour
une session arrêtée ou en échec. Sa commande est sérialisée avec la
réconciliation et la suppression de cette caméra ; une erreur HTTP est
signalée dans HA et l'état est actualisé après acceptation de la commande.

Une session `supervising` peut être en attente d'une reconnexion : elle ne
prouve pas que le flux est disponible. Les erreurs transitoires réessayées ne
sont pas encore exposées par le superviseur. Si le gestionnaire est
indisponible, le diagnostic et le bouton deviennent indisponibles, tandis que
la sonde RTSP reste indépendante. Une reprise acceptée ne prouve ni la
connexion à la caméra ni la disponibilité vidéo.

## TESTS

- `go test ./...`, `go test -race ./...`, `go vet ./...` et `go build ./...` :
  réussis. Le test ciblé du gestionnaire couvre notamment une erreur expurgée,
  la reprise via HTTP, l'effacement de l'erreur et l'isolation d'une deuxième
  session fictive.
- 23 tests HA réussis dans l'image officielle Home Assistant 2026.11.2, sans
  réseau. Ils couvrent l'état, les erreurs, le bouton, la suppression
  concurrente, les réponses invalides et la sonde RTSP indépendante.
- Scénario HA complet réussi dans un réseau Docker temporaire `--internal`,
  sans port publié : deux entrées fictives, échec et diagnostic, échec HTTP de
  reprise signalé, reprise ciblée, seconde session inchangée, panne et retour
  du faux gestionnaire, RTSP MediaMTX et aperçu JPEG. Il utilise un faux
  gestionnaire HTTP, et non le gestionnaire Go réel.
- Le premier lancement du scénario a échoué car le binaire FFmpeg indiqué
  n'était pas exécutable dans l'image HA ; les rejeux avec le FFmpeg présent
  dans l'image ont réussi. L'arrêt volontaire du flux produit un message
  Stream de fin de média attendu.
- La relecture indépendante relève un cas préexistant non corrigé : après
  suppression réussie de la session, un échec du déchargement des plateformes
  HA laisse l'ancien coordinator invalidé. Sa correction doit préserver à la
  fois la sécurité de la suppression concurrente et l'absence de redémarrage
  implicite d'une session en échec.

## ÉTAT FINAL

Diagnostic des échecs définitifs et reprise ciblée sont implémentés et testés
hors ligne. Les causes transitoires, l'enchaînement complet HA vers le
gestionnaire Go réel, les caméras physiques, la stabilité prolongée et le
déploiement restent non vérifiés. Aucun service de production ni caméra n'a
été contacté. Prochaine étape de développement : traiter le cas préexistant
de déchargement HA échoué sans réintroduire de reprise automatique.

**Suite :** ce défaut a été traité en phase 30 ; voir
[phase30-preparation-test-reel.md](phase30-preparation-test-reel.md) pour la
correction, les tests et les limites de la restauration des plateformes.
