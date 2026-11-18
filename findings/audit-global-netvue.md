# Audit global Netvue — état du 17 novembre 2026

## Périmètre et méthode

Quatre lectures indépendantes ont couvert le cœur Go, l'intégration Home
Assistant, l'infrastructure Docker/MediaMTX et la qualité transversale. Les
conclusions ont été confrontées aux tests hors ligne relancés pendant l'audit.
Aucun fichier de production n'a été modifié. Aucune caméra, compte ou
installation de production n'a été contacté.

**Interprétation des états :** « testé » désigne un test synthétique hors ligne,
pas une validation sur caméra réelle. Une fonctionnalité présente dans le code
mais sans test d'intégration correspondant est marquée partielle ou non
vérifiée. Les chemins cités sont ceux du code courant ; les rapports antérieurs
ne sont pas considérés comme preuve suffisante à eux seuls.

## État des fonctionnalités

| Fonctionnalité | État | Preuve dans le code | Travail restant | Priorité |
|---|---|---|---|---|
| Passerelle Go unique | **Implémenté et testé hors ligne.** `serve` lance un gestionnaire en mémoire ; un `RunContinuous` indépendant est créé par caméra. | `netvue-go/cmd/netvue-bridge/main.go`; `netvue-go/internal/manager/manager.go:93-130`; `manager_test.go:18-308` | La capacité réelle n'est pas mesurée et le gestionnaire n'impose pas de plafond d'admission au nombre de sessions. | P1 avant d'augmenter le parc |
| Ajout/suppression depuis HA | **Implémenté et testé** par tests de contrat HA et tests API Go. Les noms et URL sont déterministes ; le manager évite les doublons. | `custom_components/netvue_local/config_flow.py:36-98`; `bridge_api.py:59-102`; `netvue-go/internal/manager/http.go:20-27,35-112` | Pas de scénario E2E reliant le vrai client HA au vrai manager Go. | P1 |
| Persistance/réconciliation | **Partiel.** HA garde les config entries ; le manager Go ne persiste pas la liste. Une entrée absente est ajoutée au prochain chargement/refresh. | `netvue-go/internal/manager/manager.go:48-58`; `custom_components/netvue_local/bridge_api.py:59-86`; `manager_test.go:450-458` | Réconciliation additive uniquement : une session ajoutée par un autre client local ou une entrée supprimée hors déchargement normal peut ne pas être réconciliée globalement. Définir la propriété des sessions avant toute synchronisation destructive. | P2 |
| Découverte LAN à la demande | **Non implémentée côté interface.** HA demande une IPv4 manuelle ; Go effectue ensuite une découverte unicast ciblée vers cette adresse. Ce n'est pas une recherche de caméras candidates. | `custom_components/netvue_local/config_flow.py:36-61`; `netvue-go/internal/netvue/discovery.go:26-45` | La découverte déclenchée depuis HA répond à l'objectif V1 demandé, mais n'est pas un prérequis technique au premier déploiement contrôlé ; conserver la saisie manuelle en recours et ne pas lancer de balayage automatique. | P1 pour la V1 demandée ; non bloquante pour le premier déploiement |
| Protocole et authentification Netvue | **Implémentés ; parsing et échanges simulés testés.** Trames bornées, réponse de découverte validée, login explicite, délais et annulation. | `netvue-go/internal/netvue/protocol.go:12-124`; `discovery.go:26-167`; `authentication.go:22-134`; tests `protocol_test.go`, `discovery_test.go`, `authentication_test.go` | Aucune validation caméra n'a été faite dans cet audit. Compatibilité firmware et caméra 1 restent inconnues. Le transport LAN n'apporte pas de chiffrement applicatif ; la clé de session peut transiter en clair. | P1 avant usage réel |
| Réception et traitement H.264 | **Implémentés et testés avec médias synthétiques.** Annex B, SPS/PPS, IDR, limites de taille/dimensions, changements de paramètres et horloge RTP sont traités. | `netvue-go/internal/netvue/media.go:11-40`; `video.go:92-239`; `video_test.go:14-217` | La sémantique du timestamp Netvue en millisecondes reste à confirmer sur une source autorisée. B-frames, fragments étalés entre paquets et cohérence des identifiants SPS/PPS ne sont pas démontrés. | P1 pour compatibilité, P2 pour bords non observés |
| Publication RTSP / MediaMTX | **Implémentée et testée.** gortsplib publie H.264 par TCP ; MediaMTX/FFmpeg décodent un flux synthétique. Le test à deux chemins vérifie isolation, arrêt/reprise et changement de résolution. | `netvue-go/internal/netvue/video.go:241-377,427-449`; `rtsp_integration_test.go`; `multi_camera_integration_test.go:238-…` | Les tests de publication sont séparés des sessions manager/Netvue. Ils ne démontrent pas à eux seuls la chaîne entière avec plusieurs sessions gérées. | P1 |
| Supervision et reconnexions | **Implémentées et testées par tests Go.** Backoff plafonné, annulation et erreurs fatales explicites ; le 503 RTSP est temporaire, les autres statuts d'erreur ne sont pas automatiquement réessayés. | `netvue-go/internal/netvue/supervisor.go:15-119`; `supervisor_test.go:21-227`; classification dans `video.go:379-402` | La politique est cohérente et testée hors ligne, mais n'a pas été qualifiée sur différents MediaMTX ou durées prolongées. | P2 |
| Cycle de vie multi-caméra | **Implémenté et testé hors ligne.** Tests couvrent cinq sessions simulées, ajouts concurrents, arrêt, redémarrage, suppression et fermeture globale. Une panne simulée n'interrompt pas les autres. | `netvue-go/internal/manager/manager.go:135-334`; `manager_test.go:18-308` | Les cinq caméras sont des runners simulés, sans mesure de ressources ni flux vidéo pour chaque session. | P1 avant capacité annoncée |
| API locale Go | **Implémentée et testée.** Routes status/listing, ajout, consultation, start, stop et delete ; parsing JSON borné, erreurs HTTP et écoute loopback. | `netvue-go/internal/manager/http.go:20-112,144-216`; `manager_test.go:309-499` | API sans authentification distincte par décision du projet. Un processus local pouvant joindre loopback peut gérer les caméras ; aucune limite d'admission au nombre de sessions n'est appliquée. L'option `--listen` doit rester au port attendu par HA (`127.0.0.1:8765`). | P1 — garder le contrôle local documenté |
| Reprise d'une session `failed` dans HA | **Partielle.** Go conserve `failed` et son erreur, et l'API expose `/start`. HA valide l'état mais ne l'affiche pas et n'appelle pas `/start`; elle ne redémarre donc pas une session échouée existante. | `manager.go:135-170,310-325`; `http.go:24-26,77-90`; `bridge_api.py:35-45,59-86`; `coordinator.py:26-31` | Exposer l'état utile et une reprise explicite à l'utilisateur. Ne pas relancer automatiquement les refus d'authentification ou autres erreurs fatales. | P1 |
| Entités HA et aperçu | **Implémentés et testés synthétiquement dans Home Assistant 2026.11.2.** Une caméra, un capteur de disponibilité, le probe RTSP et une image JPEG via HA Stream ont été vérifiés. | `custom_components/netvue_local/camera.py:15-46`; `binary_sensor.py:14-33`; `coordinator.py:26-31`; `tests/ha_integration_synthetic.py` | Le capteur représente la disponibilité SDP RTSP/H.264, pas l'état direct de la caméra Netvue ni la cause d'échec. Le scénario utilise un faux manager HTTP. | P1 pour diagnostiquer clairement |
| Stack Portainer / Docker | **Configuration présente ; build Go et rendu de stack validés lors de la phase 26.** Un seul service Go et un MediaMTX partagé, limites de ressources et arrêt SIGTERM déclarés. | `netvue-go/Dockerfile:1-15`; `deployment/go-stack.yaml:1-72`; `deployment/mediamtx.yml:1-21`; `deployment/go-portainer.md` | Le mode host requiert HA sur le même nœud/namespace réseau. Ports libres, bind MediaMTX, architecture du nœud et quotas effectifs restent à vérifier avant déploiement. | P0 avant installation |
| Livraison d'image / CI Go | **Partielle.** Le guide sait construire l'image localement, mais Portainer exige une image déjà disponible. Le workflow existant publie l'ancienne image Python ; aucun workflow de tests ou publication Go n'a été trouvé par l'audit transversal. | `deployment/go-portainer.md:39-58`; `.github/workflows/publish-bridge-image.yml:4,24-33` | Définir une livraison reproductible de l'image Go (transfert local ou registre contrôlé) et distinguer nettement les artefacts Python historiques. | P1 avant déploiement |
| Fonctions futures (enregistrement, rétention, historique, mouvement, statistiques) | **Non implémentées**, à dessein. MediaMTX et la stack désactivent l'enregistrement. | `deployment/mediamtx.yml:4-21`; plan dans `docs/go-plan.md` | À développer après stabilisation et mesure de capacité, avec exigences de stockage/rétention explicites. | P3 / futur |

## Avancement estimé

Estimation qualitative fondée sur les critères fonctionnels présents dans le
brief et les preuves relevées ; ce n'est ni une mesure de couverture du code ni
une promesse de fiabilité :

- **MVP logiciel : majoritairement implémenté et testé hors ligne.** La chaîne
  Go, la publication RTSP, les erreurs, l'arrêt et les fonctions de base HA
  disposent de tests synthétiques. La compatibilité matérielle et certaines
  hypothèses H.264/timestamps restent non vérifiées sur une source autorisée.
- **V1 implémentée : partielle.** Gestionnaire multi-caméra, API, entités HA et
  stack sont présents. Il manque la découverte utilisateur demandée, l'état et
  la reprise explicites dans HA, un essai E2E réel entre manager Go et HA, ainsi
  que la qualification de capacité.
- **V1 opérationnellement validée : non démontrée.** Les tests locaux sont
  positifs, mais aucune stack Portainer n'a été déployée sur le serveur cible ;
  topologie, ports, ressources et fonctionnement prolongé restent à vérifier.

## Problèmes techniques et simplifications

### À corriger avant un premier déploiement

1. **Corriger le parcours de documentation HA.** Le README de l'intégration
   renvoie encore vers `deployment/README.md`, explicitement consacré à l'ancienne
   stack Python (`custom_components/netvue_local/README.md:16`,
   `deployment/README.md:1-8`). Le lien doit diriger vers
   `deployment/go-portainer.md`; l'ancienne stack doit être signalée comme
   référence non active.
2. **Définir l'observabilité/reprise.** HA ne montre pas l'erreur `failed` du
   manager et n'utilise pas sa route `/start`. Ajouter un état/diagnostic
   intelligible et une action manuelle, sans boucle automatique sur les erreurs
   fatales.
3. **Ajouter un test de frontière synthétique complet.** Les tests HA utilisent
   un faux gestionnaire (`tests/ha_integration_synthetic.py`), les tests
   manager un runner fictif (`manager_test.go`) et le test vidéo multi-caméra
   des publishers directs (`multi_camera_integration_test.go`). Ils vérifient
   bien chaque responsabilité, mais pas encore HA → API Go réelle → manager →
   sessions → MediaMTX de bout en bout.
4. **Rendre l'image installable de façon non ambiguë.** Le Dockerfile Go est
   utilisable, mais le workflow publié actuellement est Python. Avant de
   demander à l'utilisateur de déployer, préciser comment l'image Go
   construite sera rendue disponible au nœud Portainer et comment son digest
   sera choisi.
5. **Vérifier le contrat réseau sur le nœud cible.** Confirmer Debian/Linux
   amd64 (ou adapter l'image), mode réseau host de HA, disponibilité de
   `127.0.0.1:8554` et `:8765`, bind MediaMTX lisible, `NETVUE_SOURCE` correct,
   et absence de collision. `docker stack config` ne vérifie pas ces conditions
   d'exécution.

### À planifier, sans simplification risquée

- **Découverte :** garder l'ajout manuel possible ; ajouter séparément une
  découverte volontaire via Go puisque la demande mentionne ce parcours.
- **Capacité :** ne pas inventer un plafond caméra arbitraire. Mesurer CPU,
  mémoire, stabilité et récupération avec 1, 2 puis 5 sessions synthétiques
  sous les limites réelles de la stack, puis publier une capacité soutenable.
- **Réconciliation :** préserver HA comme source persistante. Définir comment
  traiter une session Go créée par un autre client ou un arrêt anormal de HA
  avant d'ajouter un inventaire global qui pourrait supprimer une session
  légitime.
- **H.264 :** obtenir des vecteurs autorisés pour timestamps, IDs SPS/PPS,
  B-frames/fragments et changements de paramètres. N'ajouter de traitement
  vidéo qu'en réponse à un cas observé.
- **Sécurité proportionnée :** conserver l'absence de secret dans l'image et le
  bind loopback demandé ; ne pas exposer l'API ou RTSP hors de l'hôte. Le
  loopback n'authentifie pas les processus locaux, et le protocole Netvue LAN
  ne chiffre pas le login. C'est une frontière de confiance à documenter, pas
  une justification pour ajouter une seconde API ou un token sans besoin
  validé. De plus, l'API accepte des sessions sans plafond configuré ; les
  limites mémoire de la stack ne bornent pas leur nombre. Avant d'ouvrir l'accès
  au-delà des processus locaux de confiance, décider si une limite d'admission
  est nécessaire ou mesurer et documenter une capacité soutenable.
- **Legacy :** garder `netvue_bridge/` comme référence, mais ne pas exécuter
  l'ancienne stack parallèlement à Go. La présence d'un manager et d'un
  workflow Python est une duplication dans le dépôt, pas une duplication
  nécessaire au runtime actuel.

## Tests et validations

### Exécutés pendant l'audit

- `GOPROXY=off go test -count=1 ./...` : réussi.
- `GOPROXY=off go test -count=1 -race ./...` : réussi.
- `GOPROXY=off go vet ./...` : réussi.
- Tests RTSP avec MediaMTX 1.21.4 et FFmpeg 8.1 locaux :
  `TestMediaMTXPublicationDecodesWithFFmpeg` et
  `TestMediaMTXMultiCameraIsolation` : réussis. Ils utilisent uniquement
  `127.0.0.1` et des vidéos synthétiques.
- Dans l'image officielle Home Assistant 2026.11.2, 20 tests HA réussissent
  (`unittest discover`). Le scénario Docker synthétique vérifie config/options,
  entités, disponibilité RTSP, capture JPEG, arrêt et déchargement. Le conteneur
  utilise un réseau `--internal` sans sortie Internet ni accès aux caméras.

### Présents mais non validés en conditions réelles

- Caméras physiques, firmware et réception continue.
- Caméra 1 ; généralisation à d'autres exemplaires/modèles.
- Stabilité prolongée (heures/jours), changements réseau et panne durable de
  MediaMTX.
- Capacité/ressources avec plusieurs sessions vidéo réelles.
- Déploiement Portainer sur le serveur cible, mode host effectif et permissions
  des chemins bind.
- Parcours complet manager Go ↔ HA décrit ci-dessus.
- Workflow CI et publication de l'image Go.

Le reviewer qualité a aussi exécuté la suite Python historique sous Windows :
il rapporte 83 tests, 37 ignorés et deux erreurs d'import (module Linux `fcntl`
et Home Assistant absent de cet environnement). Cette suite n'est pas la
validation de l'intégration HA actuelle ; les 20 tests HA ont été exécutés
séparément dans l'image officielle.

## Éléments indispensables avant le premier déploiement

1. Corriger le lien HA vers le guide Go et identifier clairement les fichiers
   Python/stack/workflow historiques.
2. Décider comment l'image Go sera construite puis transférée au nœud
   Portainer ; vérifier son architecture et son digest.
3. Valider hors production le YAML rendu et la disponibilité des ports,
   chemins de configuration, réseau host et interface source sur le nœud ciblé.
4. Ajouter un scénario synthétique de bout en bout HA → vrai manager Go →
   MediaMTX, comprenant redémarrage du manager, suppression et reprise.
5. Exposer l'état `failed` et une reprise manuelle documentée avant de
   considérer l'exploitation quotidienne.
6. Obtenir une autorisation séparée avant tout essai caméra. Déployer en
   production ou contacter une caméra ne fait pas partie de cet audit.

## Fonctionnalités pouvant attendre

Enregistrement, historique consultable, gestion de rétention/stockage,
détection de mouvement, statistiques et intégration HA enrichie peuvent attendre
la validation de la chaîne de base. Le stockage, la confidentialité et les
politiques de rétention devront être définis avant de commencer l'enregistrement.

## Plan de développement recommandé

| Phase | Objectif | Critère de sortie |
|---|---|---|
| 27 — Préparation au déploiement | Corriger le parcours documentaire et clarifier la construction/transmission de l'image Go. | Un opérateur suit le seul guide Go sans être dirigé vers l'ancienne stack. |
| 28 — Diagnostic et reprise HA | Montrer l'état/erreur utile ; ajouter une reprise manuelle contrôlée, sans relancer les erreurs fatales automatiquement. | Une session synthétique `failed` est visible et reprenable explicitement. |
| 29 — Découverte volontaire (V1) | Ajouter à Go une découverte LAN déclenchée par l'utilisateur dans HA, si ce parcours reste une exigence de la V1 ; conserver la saisie manuelle comme recours. Ce n'est pas un prérequis au premier déploiement contrôlé. | Une découverte simulée trouve/filtre un appareil sans contacter le LAN réel. |
| 30 — Contrat E2E | Relier HA, le vrai manager Go, MediaMTX et une caméra synthétique ; couvrir ajout, redémarrage, suppression et erreurs. | Test isolé stable, sans faux manager sur la frontière testée. |
| 31 — Capacité et résilience | Simuler 1/2/5 flux, mesurer ressources et tester déconnexions/arrêts/reprises sous limites de stack ; décider si une limite d'admission est nécessaire à partir des mesures et du modèle de confiance local. | Capacité soutenable et limites documentées, sans plafond arbitraire dans le code. |
| 32 — Validation de déploiement isolée | Construire/importer l'image et valider la stack sur un environnement Linux/Swarm isolé, sans caméra ni production. | Réseau host, loopback, bind, signal et limites confirmés sur la cible technique. |
| 33 — Validation matérielle autorisée | Tester progressivement la caméra explicitement autorisée, puis stabilité prolongée et autres exemplaires seulement avec autorisation. | Résultats rapportés séparément pour chaque caméra et durée réellement testée. |
| Futur — Stockage et analyse | Concevoir enregistrement, rétention, consultation, mouvement et statistiques après mesures de capacité. | Critères de confidentialité, espace et rétention définis avant implémentation. |
