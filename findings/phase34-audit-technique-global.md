# Phase 34 — Audit technique global de Netvue Bridge

**Date :** 17 novembre 2026  
**Nature :** audit technique et améliorations de phase 34. Aucun déploiement,
service de production, serveur Debian ou caméra réelle n'a été contacté.

Cette mise à jour a migré le module Go vers le chemin du dépôt, corrigé la
gestion des partitions H.264, mutualisé l'initialisation des sockets de
découverte, rendu obligatoires dans la CI les tests vidéo et le parcours
HA/Go/MediaMTX sur réseau isolé, corrigé l'adressage du pair synthétique et
renforcé les tests de sécurité Portainer. Cette continuation ignore les
réponses UDP malformées jusqu'au délai borné, protège le déchargement HA contre
l'annulation et conserve le garde-fou de création face aux réponses POST
ambiguës. Les modifications utilisateur préexistantes ont été préservées.

## Synthèse

Le projet a une architecture cohérente et relativement simple : le binaire Go
porte le protocole Netvue, les sessions et la publication ; Home Assistant
conserve la configuration et pilote une API Go locale ; MediaMTX reste un
serveur RTSP séparé. Les contrôles de concurrence du gestionnaire, le parsing
borné, les erreurs assainies, les délais, la fermeture à l'annulation et les
tests synthétiques sont des points forts vérifiés.

Aucun défaut P0/P1 restant n'a été démontré après corrections. Les défauts
confirmés traités comprennent le chemin de module Go périmé, l'acceptation de
partitions H.264 orphelines, l'arrêt de découverte sur une réponse malformée,
les transitions d'état POST ambiguës et le déchargement HA annulé à mi-parcours.
Les tests Go et les cas HA ciblés passent localement. Le run complet de la
nouvelle CI Linux/Docker a réussi sur `de5982d` ; son premier passage avait
détecté une attente de diagnostic E2E incorrecte, corrigée avant le second
passage. Restent la capacité multi-caméra à mesurer, la politique de
redémarrage Swarm à confirmer et la compatibilité matérielle à valider
séparément.

**La stabilité de caméra et le déploiement réel ne sont pas certifiés par cet
audit.** Les rapports antérieurs attestent des validations ponctuelles, mais
elles n'ont pas été rejouées sur le code et l'installation présents. Je n'ai
contacté ni serveur ni caméra.

## Architecture et périmètre examiné

```text
Home Assistant (config entries, entités)
    ├── HTTP loopback :8765 ──> gestionnaire Go ──> sessions Netvue LAN
    │                             └── découverte broadcast optionnelle
    └── HA Stream <── RTSP loopback :8554 <── MediaMTX <── H.264/RTP
```

Le code actif examiné comprend :

- CLI et cycle de vie : `cmd/netvue-bridge/`.
- Découverte, authentification, framing, réception, H.264, RTSP et
  supervision, y compris les changements locaux de découverte LAN :
  `internal/netvue/`.
- Gestion multi-caméra, API HTTP et tests : `internal/manager/`.
- Les modules Python, métadonnées et traductions de
  `custom_components/netvue_local/`, dont le nouveau parcours de découverte.
- `Dockerfile`, `deployment/go-stack.yaml`, `deployment/mediamtx.yml`,
  workflow de publication, configuration HACS, tests Python/Go et
  documentation d'architecture/développement/déploiement/protocole/roadmap.

`archive/python-bridge/` est bien identifié comme ancien et hors runtime
courant. Les APK, outils et historiques de `input/`, `tools/`, `scripts/` et
`findings/` ne constituent pas des modules de production Go/HA ; les constats
de cet audit reposent d'abord sur le code courant. Le rapport global plus
ancien `findings/audit-global-netvue.md` décrit un état antérieur et contient
des conclusions désormais obsolètes (notamment sur les entités HA et la
reprise manuelle) ; le présent rapport le remplace pour l'état courant sans
effacer son historique.

## Constats classés

### P0/P1 — aucun défaut confirmé

Aucun problème de cette gravité n'a été reproduit ou établi à partir du code
et des tests disponibles. Cela ne signifie pas que le protocole ou la chaîne
matérielle sont universellement fiables ; les limites de validation sont
listées ci-dessous.

### P2 — améliorations utiles

#### 1. Chemin de module Go périmé — corrigé

- **État : confirmé, corrigé. Confiance : élevée.**
- **Preuve :** avant correction, le chemin du module déclaré et les imports
  internes n'étaient pas cohérents avec le dépôt et le label OCI.
- **Effet corrigé :** `go.mod` et les imports internes de
  `cmd/netvue-bridge/` et `internal/manager/` utilisent désormais
  `github.com/mtx26/netvue-ha`, cohérent avec le dépôt et le label OCI.
- **Validation :** tests, race detector, vet et build réussis après migration.
- **Effort / bénéfice :** faible / cohérence durable des artefacts Go.

#### 2. La capacité multi-caméra n'a pas de limite d'admission ni de mesure

- **État :** absence de plafond confirmée ; risque de capacité non mesuré.
- **Preuve :** `internal/manager/manager.go:96-131` ajoute une entrée et
  démarre un runner sans limite de sessions ; la stack définit une limite de
  ressources globale, non une capacité par caméra.
- **Effet :** davantage de sessions peut épuiser CPU/mémoire ou perturber les
  flux sous les limites du conteneur. L'API est sans authentification
  distincte, mais écoute en loopback et ce modèle de confiance est documenté.
- **Recommandation :** mesurer 1, 2 puis plusieurs flux synthétiques dans les
  limites de déploiement visées, documenter la capacité soutenable et ne fixer
  un plafond qu'à partir des mesures et du modèle d'accès. Ne pas introduire
  de quota arbitraire.
- **Effort / bénéfice :** moyen / éviter une dégradation non caractérisée.

#### 3. Chaîne vidéo et HA/Go auparavant non couverts par la CI — validée

- **État :** manque confirmé, intégration ajoutée et exécutée avec succès.
  Confiance élevée sur les scénarios synthétiques ; aucune conclusion matériel.
- **Preuve :** les tests MediaMTX/FFmpeg dans
  `internal/netvue/rtsp_integration_test.go` et
  `internal/netvue/multi_camera_integration_test.go` étaient ignorés sans
  variables MediaMTX/FFmpeg. Le workflow épingle les images, compile les
  binaires Linux, exécute ces deux tests sur `--network none` et vérifie que
  chacun est `PASS` (un `SKIP` fait échouer le job). Il exécute aussi
  `tests/ha_go_e2e_synthetic.py` dans un réseau Docker `--internal`, avec le
  pair caméra dans un second conteneur. La CI exige le marqueur de succès
  terminal du parcours E2E. Le déclencheur `pull_request` a été ajouté ; la
  publication reste conditionnée à `main`.
- **Validation :** le run
  [44139631957](https://github.com/mtx26/netvue-ha/actions/runs/44139631957)
  a réussi les tests Go/race/vet, 39 tests Home Assistant, le rendu Swarm,
  les deux tests MediaMTX/FFmpeg en `PASS` et l'E2E complet. Le premier run
  [44138422839](https://github.com/mtx26/netvue-ha/actions/runs/44138422839)
  a révélé que le diagnostic contenait le préfixe `[authentication]` absent de
  l'assertion E2E ; celle-ci a été corrigée avant le run réussi. Le job de
  publication a été ignoré hors `main`.
- **Effet :** les régressions de publication/décodage, d'isolation
  multi-caméra, de découverte, d'intégration HA et de nettoyage font échouer
  les contrôles avant fusion.
- **Effort / bénéfice :** moyen / détecter les régressions transversales avant
  fusion ou publication.

#### 4. La politique de redémarrage Swarm mérite un essai d'exploitation

- **État : probable, non vérifié sur Swarm.**
- **Preuve :** les deux services de `deployment/go-stack.yaml` fixent
  `max_attempts: 3` et `window: 120s`. Le processus `serve` quitte si le bind
  de l'API échoue ; aucune sonde de santé n'est configurée.
- **Effet possible :** une panne de démarrage répétée ou une collision
  persistante peut laisser le service sans tâche active jusqu'à intervention,
  selon le comportement du Swarm déployé.
- **Recommandation :** vérifier ce comportement dans un Swarm jetable avec un
  échec de démarrage contrôlé ; décider ensuite si les tentatives doivent être
  illimitées ou si une intervention explicite est préférable. Ne pas modifier
  la politique sans essai ni exigence d'exploitation.
- **Effort / bénéfice :** faible pour l'essai / éviter une panne silencieuse
  prolongée.

#### 5. La découverte LAN n'était pas testée par broadcast — E2E validé

- **État :** manque confirmé ; test E2E ajouté et broadcast synthétique Linux
  confirmé dans la CI.
- **Preuve :** `internal/netvue/discovery_test.go:14-52` injecte des réponses
  sur une socket loopback ciblée ; il ne passe pas par `DiscoverLAN`
  (`discovery.go:24-53`) pour vérifier l'adresse d'interface, le calcul du
  broadcast et la réception. Le parcours HA/Go lance maintenant le faux pair
  dans un second conteneur à une IP distincte : l'ancienne disposition plaçait
  pont et caméra à la même adresse, filtrée à juste titre comme réponse locale.
- **Validation :** le run CI 44139631957 a parcouru avec succès la découverte
  broadcast synthétique, la sélection HA, le gestionnaire Go et le flux
  MediaMTX, avec deux conteneurs sur réseau Docker interne.
- **Limite :** cela ne valide pas la découverte sur le réseau ni sur une
  caméra réelle.
- **Effort / bénéfice :** moyen / prouver le parcours sans caméra réelle.

#### 6. Les partitions H.264 orphelines pouvaient être publiées — corrigé

- **État :** chemin de code confirmé ; usage par les flux Netvue non vérifié.
  Confiance élevée sur le code, inconnue sur le matériel concerné.
- **Preuve :** `internal/netvue/video.go` ne marquait comme VCL que les types
  1 et 5, alors que `mediacommon/v2/pkg/codecs/h264` définit aussi les types
  2, 3 et 4 (`NALUTypeDataPartitionA/B/C`). B/C seuls, ou sans A dans la même
  unité reçue, ne constituent pas une unité de partition décodable.
- **Correction :** A reste du VCL ; B/C ne sont transmis qu'en présence de A
  dans la même unité. Une unité contenant B/C orphelines est écartée avant la
  mise à jour de l'horloge ou la publication. Les tests couvrent A seul,
  A+B+C, B/C orphelines et le mélange avec une tranche non partitionnée.
- **Conséquence corrigée :** des NAL B/C non décodables ne peuvent plus faire
  avancer l'horloge ni atteindre le publisher comme une unité complète.
- **Limite :** l'intégralité des unités et la fragmentation entre paquets du
  protocole caméra n'ont pas été vérifiées sur matériel réel. Si A/B/C sont
  livrées sur des appels `Push` distincts, le réassemblage n'est pas assuré.
- **Effort / bénéfice / confiance :** faible / éviter une publication vidéo
  invalide / élevée pour le chemin de code, non vérifiée sur le matériel.

#### 7. Une réponse UDP malformée interrompait la découverte — corrigé

- **État :** confirmé par le chemin d'erreur ; corrigé et testé. Confiance élevée.
- **Preuve :** `discover()` retournait immédiatement toute erreur de parsing
  provenant de l'IP cible. Un datagramme malformé empêchait donc la réception
  d'une réponse valide envoyée avant l'échéance.
- **Conséquence :** une seule réponse malformée pouvait interrompre une
  découverte alors qu'une réponse valide aurait suivi dans le délai.
- **Correction :** les erreurs de parsing sont mémorisées et ignorées jusqu'à
  la date limite du socket, qui n'est pas réinitialisée. Une absence de réponse
  valide reste un timeout récupérable ; un code de résultat entier non nul
  reste un rejet explicite immédiat. Un code non entier est traité comme une
  réponse malformée, pas comme un rejet explicite ; l'absence de C conserve le
  comportement antérieur du parseur.
- **Validation :** tests UDP loopback malformé-puis-valide, malformé-seul,
  code non entier puis valide et rejet explicite avant une réponse valide.
- **Limite :** aucun équipement ni réseau caméra réel n'a été contacté.
- **Effort / bénéfice / confiance :** faible / poursuivre la découverte sans
  masquer les rejets explicites / élevée.

#### 8. Confiance locale et protocole non chiffré : limites connues

- **État : confirmé et documenté, pas une vulnérabilité distante démontrée.**
- **Preuve :** `validateLoopbackAddress` restreint l'écoute de l'API à
  loopback, mais il n'y a pas d'authentification ; MediaMTX écoute aussi sur
  loopback. `docs/netvue-protocol.md` indique que la clé locale traverse le
  réseau Netvue sans chiffrement applicatif apparent.
- **Effet :** les processus de l'hôte qui peuvent atteindre loopback peuvent
  piloter les sessions ; un observateur du LAN de la caméra peut potentiellement
  observer le login. L'exposition Internet n'est pas prise en charge.
- **Recommandation :** conserver le bind loopback, le réseau de confiance et
  l'interdiction de publier ces ports. Si des processus locaux non fiables ou
  des accès navigateur deviennent une exigence, faire une analyse de menace
  ciblée avant d'ajouter une authentification ou une validation d'origine.

#### 9. État de création HA effacé avant validation du POST — corrigé

- **État :** confirmé et corrigé. Priorité P2 ; confiance élevée.
- **Fichiers :** `custom_components/netvue_local/bridge_api.py` et
  `tests/test_home_assistant_integration.py`.
- **Preuve :** l'état `add_uncertain` pouvait être levé à la réception des
  en-têtes, avant validation du statut et du corps. Une erreur serveur ou une
  réponse incohérente pouvait donc faire croire qu'une création était résolue.
- **Conséquence :** une nouvelle création pouvait être envoyée alors que le
  POST précédent avait peut-être déjà créé la session Go.
- **Correction :** seuls les rejets contractuels avant insertion (400/409/503)
  et les réponses 200/202 dont le corps est validé lèvent le garde-fou. Les
  autres statuts, corps invalides, délais, erreurs transport et annulations le
  conservent. `delete_uncertain` n'est pas affaibli.
- **Validation :** tests de POST ambigu, réponse 5xx, corps invalide,
  annulation après en-têtes et récupération après rejet certain ; 39 tests HA
  réussis dans le run CI 44139631957.
- **Effort / bénéfice :** faible à moyen / éviter les créations dupliquées et
  préserver la cohérence entre HA et Go.

#### 10. Annulation pendant le déchargement HA — corrigée

- **État :** confirmé et corrigé. Priorité P2 ; confiance élevée.
- **Fichiers :** `custom_components/netvue_local/__init__.py` et
  `tests/test_home_assistant_integration.py`.
- **Preuve :** une annulation entre les déchargements des plateformes pouvait
  laisser l'entrée partiellement déchargée et son coordinateur actif.
- **Conséquence :** l'état HA pouvait diverger de la session gérée par Go,
  surtout si l'annulation arrivait pendant DELETE.
- **Correction :** chaque déchargement attend son résultat ; en cas d'échec ou
  d'annulation, seules les plateformes effectivement déchargées sont restaurées.
  Pendant DELETE, un retrait confirmé achève l'unload ; une erreur restaure les
  plateformes et garde `delete_uncertain`.
- **Validation :** tests d'annulation et d'échec à mi-parcours ainsi que les
  cas DELETE confirmé/incertain ; 39 tests HA réussis dans le run CI 44139631957.
- **Effort / bénéfice :** moyen / éviter les entrées HA partiellement actives
  ou une recréation de session après suppression.

### P3 — corrections de cohérence et points à surveiller

- **Cohérence documentaire et garde-fous CI — corrigés :** le prérequis Python
  3.13.2+ est indiqué dans `docs/development.md`, la roadmap distingue guide
  Portainer disponible et déploiement non validé, et le workflow exécute sur
  les pull requests. `tests/test_portainer_config.py` vérifie maintenant
  l'exécution non-root, le système de fichiers en lecture seule, `cap_drop`,
  et l'absence de ports publiés et volumes sur les services.
- **Réconciliation prudente résiduelle :** les POST/DELETE dont le résultat
  reste réellement inconnu conservent leur garde-fou et peuvent nécessiter un
  rechargement HA. Ne pas effacer ces états sans ajouter une réconciliation
  serveur idempotente.
- **Erreurs de protocole fatales :** `retryableSessionError` arrête la session
  sur `ErrProtocol`, `ErrMediaFrame` ou `ErrVideoFatal`. Cela évite de boucler
  sur des données incompatibles et rend la panne visible ; un paquet ponctuel
  invalide peut toutefois conduire à une reprise manuelle. Ne pas élargir les
  retries sans vecteur de caméra démontrant le caractère transitoire et tests
  bornés.
- **Entrypoint et image :** le build Docker cible explicitement Linux/amd64,
  cohérent avec le guide Portainer. La stack utilise `latest` par défaut, mais
  autorise un digest/tag explicite et le guide recommande de pinner le digest.
  Utiliser ce pin en exploitation ; étendre l'architecture uniquement si un
  nœud ARM devient une cible.

## Revue des mécanismes et éléments bien conçus

- **Cycle de vie Go :** verrou par caméra pour les transitions, verrou de
  registre pour l'état partagé, garde de génération dans `run`, arrêt global
  idempotent et attente bornée par contexte. Les tests couvrent les ajouts
  concurrents, suppression/reprise et isolation entre caméras.
- **API :** routes explicites, boucle locale obligatoire, taille JSON limitée,
  champs inconnus rejetés, un seul objet accepté, timeouts HTTP configurés et
  noms de stream whitelistés.
- **Protocole :** `io.ReadFull`, longueur maximale, JSON typé, résultat
  d'authentification/LIVE explicite, endpoint de découverte vérifié contre
  l'IP demandée, échéances et annulation qui ferment les sockets.
- **Vidéo :** limites de taille et dimensions, validation SPS, démarrage SPS /
  PPS / IDR, prise en compte des partitions NAL, redémarrage du publisher aux
  changements de paramètres,
  horodatage RTP monotone et tests de collisions/wrap/changements.
  `gortsplib`/`mediacommon` sont employés pour les tâches standards ; aucun
  serveur RTSP maison n'a été développé.
- **HA :** état de session séparé de disponibilité RTSP, sonde bornée,
  réponses API vérifiées, reprise manuelle explicite et compensation des
  déchargements partiels, y compris lors d'une annulation.
- **Déploiement :** conteneur Go scratch, non-root, lecture seule et sans
  capabilities ; MediaMTX restreint à RTSP TCP loopback, autres protocoles et
  fonctionnalités désactivés.

Les corrections des transitions POST ambiguës et de l'annulation HA sont
détaillées aux constats P2 9 et 10 ; elles conservent les garde-fous de
réconciliation nécessaires aux requêtes réellement incertaines.

L'initialisation des deux chemins de découverte UDP est maintenant
mutualisée dans `openDiscoverySocket`, tandis que les boucles de collecte
restent distinctes pour préserver leurs politiques de résultat. La découverte
LAN reste bornée : une seule recherche simultanée
(`http.go:32-57`), sous-réseau de l'adresse `--source`, filtrage, délai de
deux secondes, dédoublonnage et plafond de 256 réponses (`discovery.go:24-110`).
L'API retourne uniquement les adresses et l'appel HA reste explicite, sans
démarrer de session. Le nouveau E2E utilise deux conteneurs sur un réseau
interne pour respecter le filtrage des réponses émises par l'adresse source.
Le broadcast, la sélection HA et l'obtention d'une image JPEG au travers de
Go/MediaMTX ont été confirmés par le run CI 44139631957.

## Validation effectuée dans cet audit

| Vérification | Résultat |
|---|---|
| `go test -count=1 ./...` | Réussi, trois paquets. |
| `go test -race -count=1 ./...` | Réussi, trois paquets. |
| `go vet ./...` | Réussi. |
| `go build -o NUL ./cmd/netvue-bridge` | Réussi. |
| Tests ciblés découverte/H.264 | Réussis, incluant réponses malformées, rejet valide et partitions orphelines. |
| Tests HA ciblés avec stubs d'import | Réussis, 21 tests avec HTTP loopback et plateformes simulées. |
| `python -B -m unittest discover -s tests -p test_portainer_config.py -v` | Réussi, 2 tests. |
| Syntaxe Python des fichiers HA modifiés et E2E | Aucune erreur Pylance. |
| Tests Go/race/vet sur GitHub Actions | Réussis sur Linux dans le run 44139631957. |
| Suite Home Assistant sur GitHub Actions | Réussie, 39 tests. |
| MediaMTX, FFmpeg et isolation multi-caméra | Les deux tests ont affiché `PASS` dans le run 44139631957 ; aucun `SKIP`. |
| E2E HA ↔ Go ↔ broadcast synthétique ↔ MediaMTX | Réussi ; marqueur final JPEG présent dans le run 44139631957. |
| Rendu de la stack Swarm sans déploiement | Réussi dans le run 44139631957. |
| Publication d'image | Ignorée comme prévu car le run est sur `refractor`, pas `main`. |
| Structure des clés de traduction `strings.json` / `en` / `fr` | Identique. |
| Suite HA complète localement | Impossible : Home Assistant n'est pas installé. La suite complète a toutefois réussi dans l'image HA épinglée de la CI. |
| Docker, MediaMTX/FFmpeg local | Indisponibles ; ces tests ont été exécutés dans GitHub Actions. |
| Mesures CPU/RAM/capacité | Non réalisées : aucun benchmark représentatif multi-flux n'existe dans le dépôt. |
| Serveur Debian et caméra 192.168.129.2 | Non contactés. Aucune session réelle démarrée. |

Les rapports historiques documentent notamment une validation ponctuelle de la
caméra 2 et une chaîne E2E synthétique dans un environnement Docker. Ces
résultats ne sont pas revendiqués comme exécutés à nouveau pendant cet audit.

Les tests Go couvrent désormais les partitions NAL et les réponses de
découverte malformées. Le parcours HA ciblé exécuté localement utilise des
stubs d'import Home Assistant et un serveur HTTP loopback ; il ne valide pas
l'API réelle de HA. Cette limite est compensée pour la suite courante par les
39 tests et l'E2E exécutés dans les images Home Assistant et Docker de la CI.

## Feuille de route priorisée

1. **Validation continue :** conserver les contrôles CI multi-composants
   obligatoires ; le [run réussi](https://github.com/mtx26/netvue-ha/actions/runs/44139631957)
   a validé l'exécution complète après correction de l'assertion E2E.
2. **Capacité :** bench synthétique multi-flux sous les limites de la stack,
   suivre CPU/mémoire et choisir une capacité documentée ; ajouter ensuite une
   limite d'admission seulement si nécessaire.
3. **Résilience d'exploitation :** tester Swarm avec panne de démarrage
   contrôlée et confirmer le comportement après trois tentatives ; ajuster la
   politique selon l'objectif de disponibilité.
4. **Validation réelle séparée :** avant test caméra, vérifier d'abord l'état
   de la production et l'absence de session concurrente, puis définir le point
   précis à vérifier et respecter les limites de durée/essais. Ce n'est pas un
   préalable aux correctifs logiciels ci-dessus.

## État Git

Les fichiers modifiés dans cette continuation comprennent le workflow CI,
le scénario E2E, le traitement H.264 et sa régression test, le traitement et
les tests de découverte, le cycle de vie HA et son test, le test Portainer,
`docs/development.md`, `docs/roadmap.md` et ce rapport, en plus de la migration
de module déjà présente dans l'arbre. Les changements applicatifs et CI sont
sur `refractor` (`3caedda`, puis `de5982d`). Les fichiers d'agents utilisateur
préexistants n'ont pas été inclus dans ces commits. Aucun code ni service de
production n'a été déployé.
