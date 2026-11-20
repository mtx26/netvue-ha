# Audit technique approfondi — Netvue Bridge

**Date :** 2026-11-19  
**Périmètre :** code et configuration présents dans l’arbre de travail, Go, Home Assistant, MediaMTX, WebRTC, FFmpeg, tests et déploiement.  
**Méthode :** inspection statique, tests hors ligne et quatre audits spécialisés en lecture seule. Aucune caméra, aucun compte Netvue ni aucun service de production n’a été contacté.

## Résumé

L’architecture est cohérente : Go isole le protocole Netvue et supervise une session par caméra; l’intégration Python HA pilote l’API locale; `gortsplib` publie vers MediaMTX pour RTSP; Pion fournit le WebRTC natif. FFmpeg ne remplace pas le transport : il intervient dans les adaptations/transcodages nécessaires à certains chemins audio et vidéo. Aucun doublon architectural majeur ni dépendance manifestement inutilisée n’a été établi.

Le gestionnaire a une bonne couverture synthétique du cycle de vie, de l’isolation entre caméras et de l’annulation. Les tests Go et le détecteur de courses réussissent dans cet environnement. Cela ne valide cependant ni la compatibilité générale avec les modèles de caméras, ni la fluidité/synchronisation en conditions réelles. L’intégration HA, la lecture MediaMTX/FFmpeg et le navigateur n’ont pas pu être validés ici.

Les priorités sont :

1. Actualiser le toolchain Go épinglé, dont le scan `govulncheck` d’un sous-agent a rapporté 25 avis de la bibliothèque standard. Le scan n’a pas pu être répété dans l’environnement principal, où `govulncheck` n’est pas installé.
2. Traiter le risque protocolaire confirmé : l’authentification LAN transporte la clé caméra sans chiffrement applicatif.
3. Rendre explicites les échecs de récupération TURN et améliorer la reprise vidéo après pertes.
4. Documenter les limites actuelles d’Annex B, les ports UDP WebRTC et la capacité multi-caméra non mesurée.

## Architecture et flux

```text
                         configuration, état, découverte, signalisation
Home Assistant (Python) ─────────────── HTTP loopback :8765 ──────────────┐
       │                                                                   │
       │ RTSP / intégration Stream                                         v
       └──────────────────────────── MediaMTX :8554 <── RTSP ── Go Manager
                                                       │       │
                                              gortsplib │       ├─ sessions/superviseurs
                                                       │       └─ Pion WebRTC
                                                       │                 ↕ signalisation
                                                       │             HA / navigateur
                                                       │
                                              TCP Netvue local
                                                       │
                                                   Caméra
```

- **Go — contrôle et sessions :** `cmd/netvue-bridge/` fournit les commandes; [Manager](../internal/manager/manager.go) garde un registre volatil, une session supervisée et un contrôleur talkback par hôte. L’API HTTP loopback permet découverte, ajout, état, arrêt/reprise, suppression et signalisation WebRTC. HA conserve la configuration des caméras.
- **Protocole :** découverte LAN UDP; connexion/authentification et médias sur les échanges TCP Netvue. Le chemin LAN classique décrit dans [la référence protocolaire](../docs/netvue-protocol.md) n’est pas chiffré. Les détails et différences de modèles sont documentés dans [la spécification LAN](../docs/netvue-lan-client-spec.md).
- **Vidéo RTSP :** Go retire l’enveloppe Netvue et analyse les NAL H.264; il ne décode pas les images comme un décodeur vidéo généraliste. Les unités sont publiées en RTP/RTSP via `gortsplib`, puis MediaMTX sert le flux à HA. `mediacommon` fournit les structures média.
- **WebRTC :** la signalisation passe par l’intégration HA et l’API Go. Pion termine les pairs WebRTC; ce chemin est distinct de la distribution RTSP par MediaMTX. Lorsque le format H.264 n’est pas directement compatible, le code peut utiliser une adaptation FFmpeg.
- **Audio caméra → navigateur :** les formats AAC reçus sont convertis/encapsulés en Opus pour WebRTC. Le chemin synthétique ne prouve ni la qualité audio sur une caméra réelle, ni la synchronisation A/V de bout en bout.
- **Microphone → caméra :** Opus WebRTC est décodé en PCM, réordonné avec un tampon borné, puis encodé en AAC-LC/ADTS via FFmpeg et transmis au talkback Netvue. L’acceptation ou l’envoi de paquets ne prouve pas que le haut-parleur de la caméra les restitue.

## État des fonctionnalités

| Fonctionnalité | État observé |
|---|---|
| Découverte, authentification, sessions et supervision | Implémentées; tests synthétiques Go réussis. Une découverte LAN et des essais caméra sont rapportés historiquement, mais ne sont pas répétés dans cet audit et ne valident pas tous les modèles. |
| Multi-caméra | Sessions et contrôleurs audio isolés par hôte; tests de concurrence et cinq sessions synthétiques indépendantes. Capacité maximale, CPU et RAM par caméra non mesurés. |
| H.264 → RTSP | Parseur et publication implémentés; tests Go synthétiques réussis. Pas de validation locale MediaMTX dans cet environnement; l’historique décrit un essai H.264 court sans relier le binaire testé au commit actuel. |
| Audio caméra → WebRTC | Conversion AAC/Opus présente et couverte par des tests synthétiques. Audio réel, latence, qualité et synchronisation non validés ici. |
| Microphone/talkback | Conversion et transport présents; tests synthétiques Go réussis. Microphone navigateur, compatibilité de la caméra et restitution sonore réelle non validés. |
| WebRTC direct et TURN | Signalisation et relais facultatif présents dans l’arbre courant. Tests Go unitaires présents; aucun navigateur, tunnel Cloudflare ni réseau distant testé. |
| Intégration Home Assistant | Configuration, entités, caméra, diagnostics et provider WebRTC implémentés. Suite Python non exécutée faute de Home Assistant; l’environnement Python 3.13.1 est aussi sous le minimum déclaré par les exigences de test (≥ 3.14.2). |
| Déploiement Portainer/Swarm | Stack et guide présents; sécurité de configuration généralement restrictive. Aucun build Docker, rendu de stack ou runtime Linux validé localement (Docker absent). |

## Constats priorisés

### P1 — Toolchain Go épinglé avec avis de vulnérabilité

**Statut :** version du toolchain confirmée; vulnérabilités signalées par le scan du sous-agent, non reproduit ici; exploitabilité propre à ce service non établie.

Le [Dockerfile](../Dockerfile) épingle Go 1.26.1 et [go.mod](../go.mod) annonce Go 1.26.0. Le scan `govulncheck ./...` rapporté par l’audit de déploiement a trouvé 25 avis dans la bibliothèque standard, dont des correctifs annoncés dans des versions ultérieures de la branche Go 1.26 (ex. avis HTTP et certificats). `govulncheck` n’étant pas installé dans l’environnement principal, ce scan n’a pas été répété.

**Conséquence :** les images compilées peuvent embarquer des défauts corrigés dans le runtime. Le risque effectif dépend de l’API et des conditions d’exploitation; l’écoute loopback réduit notamment l’exposition HTTP directe.

**Recommandation :** vérifier les avis avec `govulncheck`, mettre à jour le toolchain vers le dernier patch maintenu et aligner Docker/CI; ajouter le scan à la CI. Étudier séparément le scan des paquets OS de l’image.

### P1 — La clé caméra transite en clair sur le LAN

**Statut :** confirmé par le code et l’analyse protocolaire; réutilisabilité de la clé par un tiers non établie.

`internal/netvue/authentication.go` place la clé dans le message de login; le chemin LAN classique ne fournit pas de chiffrement applicatif. Une personne capable d’observer le réseau local pourrait donc lire des données d’authentification.

**Conséquence :** exposition de la clé sur un LAN non fiable. Aucun correctif TLS unilatéral n’est recommandé sans preuve que les caméras l’acceptent.

**Recommandation :** limiter l’usage à un LAN de confiance, protéger le segment réseau et ne jamais journaliser la clé; documenter explicitement cette limite.

### P2 — Le parseur vidéo suppose un cadrage H.264 étroit

**Statut :** comportement confirmé dans `internal/netvue/video.go`; risque de compatibilité probable, généralisation non vérifiée.

Le traitement attend des unités Annex B dans les charges utiles, garde un SPS et un PPS actifs et n’effectue pas de réassemblage général des NAL fragmentés. Il attend une IDR pour démarrer ou reprendre après changement de paramètres.

**Conséquence :** des variantes de paquetisation ou plusieurs jeux SPS/PPS peuvent interrompre le décodage/publication, en particulier lors d’un changement de résolution ou d’une reconnexion.

**Recommandation :** ne pas ajouter un réassembleur spéculatif. Obtenir des fixtures représentatives autorisées; tester fragments, frontières, SPS/PPS multiples et IDR avant d’étendre la prise en charge.

### P2 — Des pertes WebRTC ne déclenchent pas de récupération d’image-clé

**Statut :** confirmé dans [webrtc.go](../internal/manager/webrtc.go), impact réel non mesuré.

`drainRTPSender` lit les retours RTCP et les abandonne. Les demandes PLI/NACK ne sont pas relayées à la source RTSP ni exploitées pour demander une IDR.

**Conséquence :** après perte de paquets, le navigateur peut attendre la prochaine image-clé spontanée; son intervalle n’est pas maîtrisé par le pont.

**Recommandation :** vérifier si la source permet de relayer une demande d’image-clé; sinon documenter la limite et mesurer le délai de reprise avant de modifier le chemin.

### P2 — Les échecs TURN peuvent se transformer en repli silencieux

**Statut :** chemins de code confirmés dans les modifications locales courantes; impact réseau dépendant du client.

Dans `webRTCPeer.start`, l’erreur de récupération des identifiants TURN est ignorée; le pair peut être créé sans relais et échouer ensuite pour un client sans chemin direct. Côté HA, une exception lors du chargement initial peut sélectionner la caméra WebRTC standard sans tâche de renouvellement TURN.

**Conséquence :** négociation parfois présentée comme réussie alors que le chemin relayé est indisponible; une erreur temporaire de démarrage peut persister jusqu’au rechargement.

**Recommandation :** distinguer explicitement TURN désactivé, échec temporaire et repli volontaire; faire une nouvelle tentative bornée ou exposer l’état au client/diagnostic HA.

### P2 — Le délai d’inactivité vidéo peut provoquer des reconnexions précoces

**Statut :** seuil Go confirmé; compatibilité caméra non vérifiée.

Le lecteur Go utilise un timeout de silence vidéo de 3 s et un timeout de première image de 5 s; une interruption déclenche la supervision et le backoff. L’APK étudié avait un timeout de lecture de 30 s, mais cela ne prouve pas qu’une vidéo active doit rester silencieuse aussi longtemps.

**Conséquence :** une pause de plus de 3 s peut fermer une session encore récupérable et créer des cycles de reconnexion.

**Recommandation :** traiter ces valeurs comme politique du pont, tester des délais/pertes synthétiques, puis les réévaluer sur des traces représentatives; ne pas les présenter comme constantes du protocole.

### P2 — Le socket ICE UDP utilise un port dynamique sur l’interface LAN

**Statut :** confirmé dans [webrtc.go](../internal/manager/webrtc.go) et [go-stack.yaml](../deployment/go-stack.yaml).

Le pont ouvre un socket UDP4 sur l’adresse source et un port éphémère. L’absence de ports Docker publiés ne signifie donc pas absence de socket réseau pendant WebRTC direct.

**Conséquence :** les règles de pare-feu peuvent bloquer le trajet direct; l’exposition est celle du LAN configuré, pas nécessairement un port Internet à transférer pour TURN.

**Recommandation :** expliquer ce comportement dans le guide de déploiement et limiter le pare-feu au réseau de confiance. Ne fixer une plage que si un besoin opérationnel le justifie.

### P2 — Échecs initiaux de TURN peu visibles dans HA

**Statut :** confirmé par lecture statique; conséquence navigateur non vérifiée.

Les erreurs de renouvellement TURN sont consignées en journal, tandis que les entités de disponibilité dépendent surtout de l’état caméra/RTSP. Une caméra peut apparaître disponible même si un navigateur distant ne peut plus utiliser le relais.

**Recommandation :** documenter la portée limitée du diagnostic ou ajouter un indicateur TURN seulement si cela aide réellement l’exploitation.

### P2 — Politique Swarm peut arrêter un service après trois échecs

**Statut :** configuration confirmée; conséquence opérationnelle conditionnelle.

La stack utilise `on-failure` avec trois tentatives. Cela limite les boucles de crash, mais après épuisement la reprise peut nécessiter intervention/alerte.

**Recommandation :** documenter le seuil et définir une alerte/procédure; ne changer la politique qu’après test de panne synthétique.

### P2 — Jeton TURN exposé aux administrateurs Docker via l’environnement

**Statut :** confirmé dans la stack; menace limitée aux rôles ayant accès à Docker/Portainer.

Le jeton n’est pas écrit en dur dans le dépôt, mais une variable d’environnement de service est visible dans les outils d’inspection Docker.

**Recommandation :** conserver ce modèle si les opérateurs Docker sont de confiance; sinon prendre en charge un secret monté en fichier/secret Swarm.

### P3 — Entrée HA restaurée insuffisamment recoupée

**Statut :** confirmé; scénario dépendant d’une entrée incohérente/altérée.

La restauration vérifie les champs et l’URL, mais ne valide pas entièrement que le nom de flux corresponde à l’hôte selon la règle du config flow.

**Recommandation :** réutiliser cette validation à la restauration et ajouter un test d’entrée incohérente.

### P3 — ICE direct doit être déclaré direct dans go.mod

**Statut :** confirmé par `go mod tidy -diff`.

`github.com/pion/ice/v4` est importé directement, mais reste marqué `// indirect`. La commande propose de le déplacer dans le bloc direct; les checksums sont inchangés.

**Recommandation :** appliquer `go mod tidy` dans un changement dédié et examiner le diff.

### P3 — Smoke test d’image moins restrictif que la stack

**Statut :** écart de test confirmé.

La stack exécute les conteneurs en lecture seule et supprime toutes les capacités; le smoke test CI ne reprend pas toutes ces options.

**Recommandation :** faire tourner le smoke test avec les mêmes restrictions afin de détecter les besoins d’écriture/capacité non déclarés.

## Fiabilité, multi-caméras et performances

Le gestionnaire sérialise les opérations par caméra, protège le registre, annule les superviseurs à l’arrêt et attend leur terminaison. Les tests couvrent notamment cinq caméras, ajouts concurrents, arrêt d’une caméra sans arrêter l’autre, reprise, suppression contre redémarrage, découverte concurrente et fermeture. Le détecteur de courses a réussi.

L’isolation est donc **validée synthétiquement**, pas en charge réseau réelle. Il n’existe pas ici de preuve de capacité maximale, de débit caméra, de latence, de coût CPU/RAM par caméra, de pertes acceptables ou de synchronisation A/V. Les limites de stack rapportées (0,5 CPU/512 MiB pour le pont, 0,5 CPU/128 MiB pour MediaMTX) ne sont pas corrélées à un nombre de caméras testé. La prochaine mesure utile est un test de charge synthétique sous ces limites, avec un objectif de caméras fixé.

Les reprises après coupure sont prévues par le superviseur, mais les pauses longues, redémarrages de caméra et stabilité 24/7 restent à valider hors matériel réel. La panne audio peut être désactivée sans interrompre automatiquement la session vidéo dans les chemins inspectés; cela ne prouve pas que toutes les combinaisons réelles de flux se comportent ainsi.

## Sécurité et simplicité des composants

- L’API Go (`127.0.0.1:8765`) et RTSP MediaMTX (`127.0.0.1:8554`) n’ont pas d’authentification séparée; leur portée est locale/host selon la configuration. Les processus partageant l’espace réseau hôte peuvent y accéder.
- La stack limite les services MediaMTX, n’expose pas de ports Docker déclarés, lance les services non-root, en lecture seule et avec capacités supprimées. Ces protections sont cohérentes avec le besoin.
- Les sockets UDP ICE du pont sont distincts des ports publiés par Docker. Aucun flux ne doit être exposé à Internet sans décision explicite.
- Pas de doublon majeur identifié : `gortsplib` publie RTSP, MediaMTX distribue RTSP, Pion termine WebRTC, `mediacommon` aide au traitement des formats et FFmpeg fournit les conversions/compatibilités manquantes. La complexité la plus spécifique est concentrée dans l’adaptation H.264/Opus et le protocole propriétaire; la remplacer sans fixtures de caméra serait risqué.

## Validations effectuées

### Dans l’environnement principal

- `go test ./...` : réussi.
- `go test -race ./...` : réussi.
- `go vet ./...` : réussi.
- `pytest -q tests` : **échec à la collecte** des tests HA, `ModuleNotFoundError: homeassistant`; Python est 3.13.1 alors que les exigences HA de test demandent ≥ 3.14.2.
- `pytest -q -rs tests/test_portainer_config.py` : **3 réussis, 1 ignoré**; le test ignoré exige Linux et `NETVUE_TEST_MEDIAMTX`.
- Docker et FFmpeg ne sont pas installés ici; aucun build d’image, MediaMTX réel, adaptation FFmpeg réelle ou test navigateur n’a été exécuté.
- `go mod tidy -diff` : signale `pion/ice/v4` comme dépendance directe à déplacer; aucune écriture effectuée.
- `govulncheck` : non installé dans l’environnement principal.
- `git diff --check` : aucune erreur d’espacement; Git affiche seulement ses avertissements LF→CRLF sur certains fichiers.

### Résultats rapportés par l’audit de déploiement

Le sous-agent rapporte aussi `go build ./...`, `go mod verify`, fuzz court et tests Go/E2E locaux réussis; les scénarios nécessitant MediaMTX ont été ignorés. Son `govulncheck ./...` a signalé les 25 avis décrits plus haut. Ces validations ne sont pas toutes répétées dans l’environnement principal.

### Non effectué

Aucune caméra n’a été contactée; aucun service ou stack de production n’a été modifié; aucune validation réelle Home Assistant, navigateur, micro, haut-parleur ou réseau TURN n’a été réalisée.

## État Git et limites de l’audit

Le dépôt était déjà modifié au début de l’audit; ces changements, notamment les ajouts Cloudflare TURN, ont été traités comme état courant. Pendant l’audit, ces changements ont été intégrés à `HEAD` (`4deb5c1`, `feat: add optional Cloudflare TURN support for WebRTC`); ce commit n’a pas été créé par cet audit. Aucun code n’a été modifié par cet audit. Ce rapport est son seul fichier ajouté.

## Feuille de route recommandée

1. Vérifier/résoudre les avis Go et aligner le toolchain Docker/CI; intégrer `govulncheck` et considérer le scan de l’image OS.
2. Documenter le risque de clé en clair et maintenir le protocole sur un LAN de confiance.
3. Définir les comportements TURN en cas d’échec et rendre la disponibilité du relais observable.
4. Documenter le port UDP ICE dynamique; tester le filtrage direct/relayé sur un réseau de test.
5. Tester une perte vidéo et le délai de reprise; décider du traitement PLI/IDR selon le support de la source.
6. Obtenir des fixtures H.264 représentatives avant d’élargir le parseur.
7. Ajouter/restaurer l’environnement HA compatible et une validation Linux MediaMTX/FFmpeg/navigateur en CI.
8. Fixer un objectif de caméras, puis mesurer débit, CPU, mémoire, latence et stabilité sous les limites de déploiement.
9. Corriger la déclaration directe de `pion/ice/v4`, renforcer le smoke test et documenter le seuil de reprise Swarm.
