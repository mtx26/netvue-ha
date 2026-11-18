# Phase 26 — gestion multi-caméra dynamique Go

## OBJECTIF

Remplacer le modèle d'une instance Go par caméra par une passerelle Go unique,
capable de gérer un nombre non fixé de sessions indépendantes. Home Assistant
reste responsable de la configuration persistante ; MediaMTX est partagé.

## MODIFICATIONS

- `netvue-go/internal/manager` fournit un registre runtime et une session
  `RunContinuous` par caméra. Les opérations de démarrage, arrêt et suppression
  sont sérialisées par caméra ; le superviseur existant garde la responsabilité
  des reconnexions et de la vidéo.
- `net/http` expose les routes de contrôle pour l'état, l'ajout, le démarrage,
  l'arrêt et la suppression. Les chemins RTSP sont déterministes ; MediaMTX
  reste sur `127.0.0.1:8554` et l'API sur `127.0.0.1:8765`. Aucun token ni
  mécanisme d'authentification supplémentaire n'a été ajouté.
- `netvue-bridge serve` démarre le gestionnaire sans contacter de caméra.
  Chaque ajout HA déclenche ensuite la découverte et la session Netvue.
- L'intégration Home Assistant conserve ses config entries, réconcilie les
  caméras absentes après redémarrage du pont et supprime la session au
  déchargement. Si la suppression échoue, l'entrée reste chargée. La
  découverte Python redondante a été retirée ; le protocole Netvue reste en Go.
- La stack `deployment/go-stack.yaml` contient un service Go et un MediaMTX.
  L'ancienne passerelle Python et son déploiement restent disponibles comme
  référence historique, sans être démarrés en parallèle.

## FONCTIONNEMENT

Home Assistant est la source persistante ; le registre Go est volatil. Après
redémarrage du pont, HA ajoute les entrées absentes. Une session déjà présente,
même arrêtée ou en échec, n'est pas relancée implicitement. Chaque caméra a
son propre contexte et son propre cycle de vie ; une panne ou un arrêt ne
bloque pas les autres.

La revue indépendante a identifié une course entre redémarrage et suppression.
Le verrou par caméra et le test concurrent correspondant ont été ajoutés.
Elle a aussi motivé la correction de la réconciliation HA après un DELETE en
échec et de la propagation des erreurs d'arrêt dans la CLI. Les adresses RTSP
configurables, incompatibles avec l'URL HA fixe, ont été retirées de la
commande `serve`.

## TESTS

- `go test ./...` : réussi.
- `go test -race ./...` : réussi.
- `go vet ./...` : réussi.
- `go build -o netvue-bridge-phase26.exe ./cmd/netvue-bridge` : réussi.
- Avec MediaMTX 1.21.4 et FFmpeg 8.1 locaux : publication RTSP suivie d'un
  décodage indépendant réussie ; test synthétique multi-flux réussi, avec
  vérification de l'isolation, de l'arrêt/reprise et du changement de
  résolution. Le premier rejeu du décodage n'avait que neuf images disponibles ;
  la durée du média synthétique a été allongée, puis les deux tests ont réussi.
- PyYAML a analysé `go-stack.yaml` et trouvé les deux services attendus.
- `python -m py_compile` a validé les modules HA modifiés et le test associé.
- Dans `ghcr.io/home-assistant/home-assistant:2026.11.2`, les 20 tests
  `test_home_assistant_integration.py` réussissent. Le premier rejeu a révélé
  que le test de concurrence exigeait à tort l'absence de POST avant DELETE ;
  il vérifie désormais la suppression finale et l'absence de résurrection.
- Le scénario HA complet réussit aussi dans un réseau Docker `--internal` :
  config flow, options, entités, disponibilité RTSP, capture JPEG Home
  Assistant, arrêt du flux et déchargement. `--network none` ne convenait pas
  à ce scénario, car Zeroconf HA exige une interface IPv4 ; le réseau interne
  n'offre aucun accès externe ni accès aux caméras.
- `docker stack config` valide la stack rendue avec variables fictives.
- `docker build -f netvue-go/Dockerfile -t netvue-bridge-go:phase26 netvue-go`
  réussit. Le lancement conteneurisé de `serve --help` confirme l'image
  Linux/amd64, l'entrée `/netvue-bridge` et l'UID `10001:10001`.

Aucun test n'a contacté de caméra réelle. Aucune stack ni service de production
n'a été modifié.

## ÉTAT FINAL

Le gestionnaire Go multi-caméra, l'intégration HA et la stack Portainer sont
implémentés et vérifiés hors ligne avec Go, MediaMTX, FFmpeg et Home Assistant
conteneurisé ; l'image Go est également construite et vérifiée localement.
Le déploiement Portainer, la compatibilité des caméras et la stabilité
prolongée restent non vérifiés.

Prochaine étape nécessaire : préparer le déploiement Portainer après
autorisation. Tout contact avec les caméras ou changement de production
nécessite toujours une autorisation distincte.
