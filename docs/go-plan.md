# Reprise de Netvue Bridge en Go

## Cahier des charges

Construire une passerelle locale maintenable qui reçoit un flux Netvue autorisé
sur le LAN et le publie en H.264/RTSP pour Home Assistant. Le module Go réside
à la racine du dépôt. Le pont Python historique est isolé sous
`archive/python-bridge/` et ne fait pas partie du code actif ; l'intégration
Home Assistant reste en Python.

### Exigences communes

- Traiter uniquement les appareils et endpoints explicitement configurés ou
  annoncés par une réponse de découverte provenant de l'adresse ciblée.
- Borner tailles, délais, connexions et buffers ; arrêter explicitement sur
  données ou états ambigus.
- Ne jamais journaliser clé locale, jeton, credential, paquet brut ou image.
- Le transport Netvue observé n'est pas chiffré : l'usage est limité à un LAN
  de confiance et aucune protection cryptographique de la clé en transit n'est
  revendiquée.
- Lier les lecteurs RTSP à une adresse locale contrôlée ; ne pas exposer les
  flux sur Internet.
- Fermer proprement les connexions et le publieur à l'arrêt, puis signaler les
  erreurs critiques sans les convertir en succès.
- Vérifier les comportements hors ligne avec des pairs et médias synthétiques.
  Les validations réelles caméra et production restent des étapes séparées et
  autorisées.

## Fonctionnalités prévues

### MVP — une caméra, exécution locale bornée

- Découverte UDP unicast avec validation de l'émetteur et de l'endpoint.
- Une seule connexion LAN et un seul échange de login ; un résultat explicite
  est requis avant de demander le flux.
- Une demande LIVE, réception et validation de trames H.264 bornées.
- Publication RTSP/TCP vers un serveur local tel que MediaMTX.
- CLI explicite : `test` exécute une session bornée et `run` supervise une
  session continue jusqu'à l'arrêt ; aucune commande implicite ne contacte un
  appareil.
- Tests synthétiques du cadrage, des résultats d'authentification, des délais,
  des erreurs H.264 et de la publication.

**Critères de sortie :** `go test ./...` et `go vet ./...` réussissent ; tests
isolés couvrent trames tronquées, champs de résultat absents, unités H.264
malformées, limites, annulation et fermeture ; un média synthétique est publié
sur RTSP/TCP et décodé par un outil indépendant ; aucune caméra n'est contactée.

### Version 1 — exploitation locale et Home Assistant

- Configuration documentée ; utiliser une seule instance du programme Go
  pour toutes les caméras, avec une session et le superviseur existant par
  caméra, et un MediaMTX partagé. Le nombre de caméras n'est pas fixé dans le
  code.
- Pas de budgets persistants hérités sans besoin démontré ni migration.
- Gestion de la reconnexion, des changements SPS/PPS/résolution et des
  interruptions, caractérisée par des tests de bout en bout synthétiques.
- L'intégration `netvue_local` reste en Python pour les config entries, les
  entités et la lecture RTSP ; elle communique avec l'API locale du gestionnaire
  Go au lieu de démarrer le gestionnaire Python.
- Stack Docker/Portainer reproductible sur le réseau host du nœud Home
  Assistant, privilèges minimaux, ressources bornées et aucun port publié.
- Compatibilité caméra 1, stabilité prolongée et déploiement restent des
  validations réelles autorisées, pas des critères démontrables par les seuls
  tests hors ligne.

### Évolutions futures

- Enregistrement, rétention et consultation d'historique.
- Gestion et quotas de stockage explicites.
- Détection de mouvement et statistiques.
- Intégration Home Assistant plus riche et gestion pratique de plusieurs
  appareils.

Ces évolutions ne doivent pas introduire prématurément de traitement vidéo,
base de données ou API supplémentaire au MVP.

## Comparaison vidéo et décision

| Technologie | Rôle évalué | Limites pour ce projet |
|---|---|---|
| [gortsplib](https://github.com/bluenviron/gortsplib) | Client RTSP Go qui peut publier un média H.264 et paquetiser les unités d'accès en RTP. | N'interprète pas les messages Netvue, ne répare pas les horodatages et ne décode pas les images. |
| [mediacommon](https://github.com/bluenviron/mediacommon) | Utilitaires H.264 Annex B, SPS et paquetisation utilisés par l'écosystème Go/MediaMTX. | Ne remplace ni un client RTSP complet ni un décodeur vidéo. |
| [FFmpeg](https://ffmpeg.org/) | Décodeur/outil indépendant pour valider les médias synthétiques et diagnostic de référence. | Un processus externe dans le chemin de production ajouterait gestion de cycle de vie, copie de données et contraintes de licence de compilation. |
| [MediaMTX](https://github.com/bluenviron/mediamtx) | Serveur RTSP séparé, destinataire de publication et point de lecture local. | N'est pas un client du protocole Netvue ni une bibliothèque de traitement du pont. |

**Décision MVP :** publier directement en Go avec `gortsplib/v5` et utiliser
`mediacommon/v2` pour analyser les unités Annex B. Les versions initialement
évaluées sont `gortsplib v5.7.0` et `mediacommon v2.10.0` ; elles sont
épinglées dans `go.mod`. Le module gortsplib v5 exige Go 1.26.0 ;
l'environnement de développement observé est Go 1.26.1. FFmpeg ne sera pas
une dépendance obligatoire du binaire, mais restera un lecteur/décodeur
indépendant des tests vidéo. MediaMTX reste un processus distinct.

Cette décision évite un serveur RTSP maison et un processus FFmpeg permanent.
Elle ne prouve pas l'équivalence avec le décodeur PyAV historique. Une
publication H.264 ne sera déclarée validée qu'après lecture et décodage
indépendants ; le support de B-frames, de fragments et de corrections de PTS
doit être explicitement caractérisé.

## Architecture cible

```text
CLI test/run                         CLI serve
     |                                   |
     v                                   v
session / superviseur unique       API HTTP locale Go
                                        |
                               gestionnaire runtime léger
                               une session RunContinuous par caméra
                                        |
    découverte UDP -> login -> LIVE -> H.264/RTP
                                        |
                         gortsplib -- RTSP/TCP --> MediaMTX
                                                       |
                                                       +--> Home Assistant
```

Le paquet `internal/netvue` porte les détails spécifiques aux trames Netvue et
au cycle de session. Le traitement Annex B, l'horloge et la publication RTSP
restent séparés des messages de commande. `test` exécute une session bornée ;
`run` supervise une caméra. La commande `serve` ajoute le gestionnaire HTTP
runtime : il lance une session indépendante par caméra sans persister la liste.
Les config entries Home Assistant constituent la source de configuration
durable.

### Décision phase 26 — multi-caméra dynamique

Une seule application Go et un gestionnaire runtime lancent `RunContinuous`
indépendamment pour chaque caméra ; aucune limite fixe au nombre de caméras
n'est codée. Un seul MediaMTX reçoit les chemins uniques, stables et dérivés
des adresses caméra. L'API `net/http` locale gère ajout idempotent, état, URL
RTSP, arrêt, redémarrage explicite et suppression. Elle n'ajoute pas
d'authentification distincte et reste liée à loopback comme l'ancien manager.

Les config entries Home Assistant constituent la source persistante de
configuration ; Go ne conserve que l'état runtime. Le coordinator HA réconcilie
une entrée absente après redémarrage du pont sans relancer une entrée existante
en état arrêté ou en échec. La suppression HA arrête et retire la session.
Python reste l'interface Home Assistant (config entries, entités, probe RTSP),
Go porte les sessions, l'authentification Netvue, H.264, RTSP et la supervision.
L'ajout HA garde la saisie manuelle de l'adresse ; chaque session Go effectue
sa découverte unicast. La découverte de candidats par diffusion LAN n'est pas
implémentée. Le pont Python historique est archivé hors de l'arbre actif.
La stack dynamique contient un seul
service Go et un MediaMTX. Les détails sont dans `deployment/go-portainer.md`.

## Avancement et décisions ouvertes

- **Terminé :** cahier des charges, comparaison bibliographique indépendante,
  synthèse du protocole, session unique (découverte, login, LIVE), validation
  Annex B/SPS et publication directe RTSP/TCP. Le moteur vidéo caractérise
  aussi les collisions/réinitialisations de timestamps, les limites par axe,
  les remplacements successifs de SPS/PPS sans mutation partielle sur entrée
  rejetée et la fermeture du publisher. Un pair loopback vérifie que le refus
  d'authentification arrête le flux avant LIVE.
- **Phase 25 :** le CLI offre `test` (session bornée) et `run` (supervision
  continue), avec un seul cycle retryable, backoff plafonné et arrêt bloquant
  sur refus d'authentification, configuration invalide ou erreur critique.
  Seul le 503 RTSP est classé temporaire ; les autres codes de réponse 4xx/5xx
  arrêtent la tentative, pour ne pas assimiler toute réponse serveur à une panne
  transitoire. SIGINT/SIGTERM
  et annulation ferment le cycle courant avant la sortie. `test` exige une
  durée positive sans plafond ; `run` n'a pas d'échéance de session.
- **Vérifié hors ligne :** génération synthétique H.264, publication vers un
  MediaMTX temporaire lié à loopback et décodage de dix images par FFmpeg ;
  tests unitaires, tests avec le détecteur de courses et `go vet` réussis.
  Cette vérification initiale date de la phase 23 ; après une période sans
  binaires disponibles, elle a été rejouée en phase 26 à partir des outils
  locaux fournis.
  Le listener RTSP loopback est prévalidé avant la découverte ; cette
  correction passe les tests unitaires et avec détecteur de courses. Un serveur
  gortsplib local vérifie aussi la séquence ANNOUNCE/SETUP/RECORD sur trois
  générations successives après changement SPS/PPS, ainsi que les délais de
  démarrage, l'annulation RTSP et le préflight IPv6.
- **Phase 26 — implémentation et vérification hors ligne terminées :**
  l'application Go unique, le gestionnaire runtime multi-caméra, l'API locale,
  la réconciliation HA et la stack Portainer sont implémentés. `go test ./...`,
  `go test -race ./...`, `go vet ./...` et `go build` réussissent. Avec
  MediaMTX 1.21.4 et FFmpeg 8.1 locaux, la publication RTSP décodée et le test
  multi-flux passent ; ce dernier vérifie l'isolation, l'arrêt/reprise d'une
  publication et le changement de résolution. PyYAML analyse la stack en deux
  services et `py_compile` valide l'intégration et ses tests. Dans l'image
  officielle Home Assistant 2026.11.2, 20 tests unitaires et le scénario
  synthétique complet (config/options, entités, RTSP, JPEG, arrêt et décharge)
  réussissent dans des conteneurs isolés sans accès réseau externe. L'image Go
  se construit localement, démarre en UID 10001 sur Linux/amd64 et affiche
  l'aide de `serve` ; `docker stack config` valide la stack rendue. Aucune
  caméra réelle ni production n'a été contactée.
- **Phase 27 — préparation de l'installation Go terminée hors production :**
  le Dockerfile conserve une compilation reproductible Linux/amd64, ajoute le
  label OCI du dépôt et livre uniquement le binaire dans `scratch`. Le nouveau
  workflow manuel distinct de l'ancien workflow Python exécute
  `go test ./...`, `go test -race ./...` et `go vet ./...`, puis peut publier
  une image GHCR Go par tag de commit et afficher son digest ; il n'a pas été
  déclenché. Le guide Portainer décrit la construction locale mono-nœud,
  l'option registre par digest, l'accès GHCR privé/public, l'architecture
  amd64 et les limites du réseau host. Le README HA pointe désormais vers le
  guide Go et identifie le guide Python comme historique. Lors de cette phase,
  les tests Go, la construction et le démarrage d'aide de l'image, ainsi que
  `docker stack config` ont réussi ; aucune publication ni aucun déploiement
  n'a été effectué.
- **Phase 28 — réorganisation Go :** le module et l'image Go sont à la racine ;
  les sources, tests et déploiement Python historiques sont archivés dans
  `archive/python-bridge/` hors des workflows actifs. L'intégration HA et ses
  tests restent actifs. Les chemins de build et de publication, les guides et
  le contexte Docker ont été adaptés. Après stabilisation de l'arborescence,
  tests Go, détecteur de courses, `go vet`, compilation, build Docker et rendu
  de la stack ont réussi ; les 20 tests HA et le scénario vidéo HA synthétique
  ont réussi dans Docker. Les deux tests vidéo Go MediaMTX/FFmpeg ont réussi.
  Les commandes Go du workflow ont réussi dans son image Linux, sur une copie
  locale minimale sans réseau ; le workflow de publication n'a pas été lancé.
  Un premier `go vet` lancé pendant le déplacement a échoué sur un répertoire
  disparu ; le rejeu après déplacement a réussi.
- **Prochaine étape :** vérifier la disponibilité et la visibilité souhaitée
  du package GHCR, puis déclencher manuellement sa publication uniquement
  après autorisation. La stack sur le nœud cible reste à valider avant tout
  déploiement ; aucun essai caméra ou production n'est autorisé par cette
  phase.
- **Ouvert :** disponibilité du flux sans cloud, protocole réellement utilisé
  par la caméra 1, gestion générique des images B et fragments, stabilité
  continue et stabilité sous charge.
- **Politique timestamp Go :** un wrap naturel 32 bits est accepté. Un doublon,
  un retour arrière ou un saut supérieur à 10 s avance de 1 ms (90 ticks RTP),
  avec ancre réinitialisée sur la nouvelle valeur source. Cette correction
  minimale préserve l'ordre sans inférer une cadence ; la cause et les valeurs
  brutes de l'anomalie historique restent inconnues.
- **Limite Go connue :** les B-frames, fragments répartis entre plusieurs
  paquets, décodage après changements répétés et continuité vidéo réelle ne
  sont pas encore validés par MediaMTX/FFmpeg dans cette phase.

## Résultats de vérification présents

- Environnement observé : `go1.26.1 windows/amd64`.
- Tests du module `internal/netvue` : réussis pour le cadrage initial, lecture
  TCP fragmentée, rejet des trames invalides et validation des entiers JSON.
- Tests en boucle locale : authentification fictive rejetée avant LIVE et
  publication H.264 décodée par FFmpeg depuis un lecteur RTSP TCP synthétique.
- Aucune communication caméra, opération de production ou publication
  externe n'a été effectuée.
- **Phase 24 :** `go test ./...`, `go test -race ./...`, `go vet ./...` et
  `go build ./cmd/netvue-bridge` réussissent. Les tests ajoutés couvrent le
  wrap RTP 32 bits, collisions/réinitialisations/sauts PTS, limites SPS par
  axe, absence de mutation après rejet de paramètres contradictoires,
  remplacements successifs et trois générations RTSP loopback. La
  lecture/décodage par MediaMTX/FFmpeg n'a pas été exécutée dans cette phase :
  les deux binaires et variables d'intégration sont absents.
- **Phase 25 :** `go test ./...`, `go test -race ./...`, `go vet ./...` et
  `go build ./cmd/netvue-bridge` réussissent. Les tests caractérisent la
  classification des erreurs, les reconnexions séquentielles, le backoff et
  son reset, les erreurs bloquantes, l'arrêt pendant le délai, la durée nulle
  de session, les durées `test` positives supérieures à 24 heures et les
  commandes `test`/`run`. La compilation Linux/amd64 avec
  `CGO_ENABLED=0` réussit ; la cross-compilation cgo n'est pas disponible faute
  d'en-têtes Linux sur l'hôte Windows. MediaMTX et FFmpeg restent absents.
