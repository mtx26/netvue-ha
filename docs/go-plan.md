# Reprise de Netvue Bridge en Go

## Cahier des charges

Construire une passerelle locale maintenable qui reçoit un flux Netvue autorisé
sur le LAN et le publie en H.264/RTSP pour Home Assistant. Le nouveau programme
est isolé sous `netvue-go/`; l'implémentation Python demeure une référence et
n'est ni remplacée ni supprimée pendant cette reprise.

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

- Configuration documentée, plusieurs caméras indépendantes et une session
  active par appareil.
- Extension du superviseur unique au multi-caméra, avec une session active par
  appareil ; pas de budgets persistants hérités sans besoin démontré ni
  migration sûre.
- Gestion de la reconnexion, des changements SPS/PPS/résolution et des
  interruptions, caractérisée par des tests de bout en bout synthétiques.
- API de contrôle loopback ou mécanisme Home Assistant retenu après
  comparaison ; MediaMTX reste un service distinct.
- Stack Docker/Portainer reproductible, privilèges minimaux, ressources
  bornées, aucun port publié et documentation de retour arrière.
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
épinglées dans `netvue-go/go.mod`. Le module gortsplib v5 exige Go 1.26.0 ;
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
CLI test/run / configuration explicite
          |
          v
superviseur unique (run) ou session bornée (test)
   | découverte UDP -> endpoint LAN -> login explicite -> demande LIVE
   | réception TCP -> trames de commande / média
   v
analyse Annex B, paramètres H.264 et horloge
          |
          v
client publieur gortsplib -- RTSP/TCP --> MediaMTX local
                                               |
                                               +--> Home Assistant
```

Le paquet `internal/netvue` porte les détails spécifiques aux trames Netvue et
au cycle de session. Le traitement Annex B, l'horloge et la publication RTSP
restent séparés des messages de commande. `test` exécute une session bornée ;
`run` délègue sa reconnexion à un unique superviseur séquentiel. Aucun
gestionnaire HTTP ni état persistant n'est ajouté.

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
  Les réponses RTSP 5xx sont transitoires, les 4xx bloquantes ; SIGINT/SIGTERM
  et annulation ferment le cycle courant avant la sortie. `test` exige une
  durée positive sans plafond ; `run` n'a pas d'échéance de session.
- **Vérifié hors ligne :** génération synthétique H.264, publication vers un
  MediaMTX temporaire lié à loopback et décodage de dix images par FFmpeg ;
  tests unitaires, tests avec le détecteur de courses et `go vet` réussis.
  Cette vérification date de la phase 23 et n'a pas été rejouée en phase 24,
  MediaMTX et FFmpeg étant absents de l'environnement courant.
  Le listener RTSP loopback est prévalidé avant la découverte ; cette
  correction passe les tests unitaires et avec détecteur de courses. Un serveur
  gortsplib local vérifie aussi la séquence ANNOUNCE/SETUP/RECORD sur trois
  générations successives après changement SPS/PPS, ainsi que les délais de
  démarrage, l'annulation RTSP et le préflight IPv6.
- **En cours :** images B, fragments et validation indépendante du décodage
  après les changements du moteur restent à caractériser.
- **Ensuite :** caractériser les interactions avec MediaMTX/FFmpeg après
  stabilisation du moteur, puis étendre l'architecture au multi-caméra.
- **Ouvert :** disponibilité du flux sans cloud, protocole réellement utilisé
  par la caméra 1, gestion générique des images B et fragments, stabilité
  continue, version Home Assistant et configuration de déploiement Go.
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
