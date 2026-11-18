# Phase 30 — Préparation du premier essai Go et Home Assistant

## OBJECTIF

Corriger le déchargement HA de la phase 29, vérifier la chaîne complète avec
des éléments fictifs et préparer les opérations Portainer/HA pour une caméra
réelle, sans déployer ni contacter l'appareil.

## MODIFICATIONS

- `custom_components/netvue_local/__init__.py` décharge les plateformes avant
  de supprimer la session. Si une plateforme échoue, les plateformes déjà
  déchargées sont rétablies et la session est conservée. En cas d'échec de
  suppression de session, les plateformes déchargées sont également
  rétablies ; HA signale l'échec.
- `bridge_api.py` empêche la recréation automatique d'une session manquante
  après une suppression de résultat incertain. Si une création n'a pas reçu
  de réponse, il refuse les créations, reprises et suppressions suivantes : une
  ancienne requête pourrait encore aboutir. Les états incertains restent
  référencés pendant le processus HA, mais ne sont pas un budget persistant.
- `tests/ha_go_e2e_synthetic.py` fait communiquer la vraie intégration HA et le
  véritable gestionnaire Go avec une caméra Netvue fictive et MediaMTX.
  `tests/ha_integration_synthetic.py` couvre aussi les échecs de déchargement
  dans un vrai processus HA avec un gestionnaire HTTP fictif.
- Le guide `deployment/go-portainer.md`, le guide de développement et le plan
  Go présentent la procédure préparée, les tests et les préconditions.
- Le workflow de publication est préparé pour un push sur `main` : tests Go
  et HA, image Go `latest` et tag de commit, puis artefact HA correspondant.
  Le déclenchement manuel reste disponible ; seule `main` publie. Le label OCI
  pointe vers le dépôt source effectif. La version HA est passée à `0.2.1`.
- La stack utilise l'image Go par défaut et configure MediaMTX par variables,
  sans fichier à copier. Seuls `NETVUE_NODE` et `NETVUE_SOURCE` sont requis ;
  `NETVUE_BRIDGE_IMAGE` reste une surcharge facultative. Le package existant
  est privé ; les droits GHCR restent inchangés.

## FONCTIONNEMENT

Le scénario complet utilise une adresse privée appartenant au conteneur de
test, un réseau Docker temporaire `--internal` et aucun port publié. La caméra
fictive rejette d'abord le login ; HA affiche l'échec provenant du gestionnaire
Go. Le bouton de reprise entraîne un second login fictif accepté, puis des
images H.264 fabriquées sont transmises à Go, publiées vers MediaMTX et
décodées en JPEG par Home Assistant. Le déchargement normal supprime la session.

En cas d'échec partiel du déchargement ou de suppression, HA 2026.11.2 place
l'entrée en `FAILED_UNLOAD`, même si les plateformes ont été rétablies ; cette
entrée peut nécessiter un redémarrage HA. La compensation n'est pas une
transaction garantie si la restauration elle-même échoue. Une réponse DELETE
perdue est traitée comme incertaine ; une session manquante n'est pas recréée
automatiquement tant que le processus HA garde cet état en mémoire. Un POST
sans réponse définitive interdit une suppression qui pourrait précéder une
création retardée ; une intervention manuelle est alors nécessaire.

Les variables MediaMTX reproduisent la configuration restrictive de
`deployment/mediamtx.yml` dans le fichier embarqué par l'image officielle
épinglée. Le publieur et les lecteurs RTSP restent sur le loopback partagé ;
aucune authentification RTSP supplémentaire n'est introduite. La version HA
est distribuée en artefact du même lancement que l'image, pas via HACS.

## TESTS

- `go test ./...`, `go test -race ./...` et `go vet ./...` : réussis.
- 26 tests HA dans l'image officielle Home Assistant 2026.11.2 sans réseau :
  réussis. Le test ajouté reproduit d'abord le défaut initial (session perdue,
  coordinator invalidé), puis vérifie la conservation d'une session en échec
  et l'absence de reprise automatique ; un autre couvre la suppression
  incertaine, le POST expiré et la conservation de l'état après collecte.
- Le scénario HA à gestionnaire fictif vérifie, avec HA réel, la restauration
  des quatre entités après échec d'une plateforme et après échec du DELETE,
  ainsi que l'état `FAILED_UNLOAD`. Les erreurs HA correspondantes sont
  attendues car ces pannes sont injectées.
- Le scénario complet avec le binaire du gestionnaire extrait de l'image Go
  construite, caméra fictive, MediaMTX et HA a réussi : diagnostic, reprise
  explicite, flux, JPEG et déchargement.
- L'image Go a été construite localement, puis son aide exécutée dans un
  conteneur sans réseau ; architecture `amd64` et UID `10001:10001` vérifiés.
  La première construction forcée sans réseau a échoué faute de cache de
  dépendances ; la construction normale suivante a réussi.
- Une vérification SSH passive du serveur Debian montre Swarm actif, un
  Docker Linux/x86_64, un conteneur Home Assistant en réseau host, et deux services Netvue `1/1` :
  l'ancien pont Python et MediaMTX. Les ports 8554 et 8765 sont occupés. Ces
  observations ponctuelles ne prouvent pas quel processus occupe chaque port
  ni si le pont communique actuellement avec une caméra. Une première commande
  SSH de diagnostic avait échoué à cause de sa syntaxe ; les lectures simples
  suivantes ont réussi. Le fichier du budget historique existe encore mais
  n'est pas lisible par le compte SSH ; son contenu n'a pas été lu. HA monte
  `/home/mtx26/homeassistant` dans `/config` et l'intégration personnalisée
  existe déjà sur ce chemin. Aucun service n'a été arrêté ou modifié.
- Un lancement simultané de trois validations Docker a rencontré des délais
  de démarrage MediaMTX et des expirations de tests HA. Les tests HA relancés
  seuls et les deux scénarios relancés isolément ont réussi ; la cause précise
  de ces délais n'est pas établie.
- Pour la stack simplifiée, `docker stack config` a rendu l'image par défaut et
  les deux variables ; 27 tests HA et de parité de configuration ont réussi
  dans Docker. La configuration des variables a été validée par l'image
  officielle MediaMTX 1.21.4. Un test isolé a vérifié l'unique écoute TCP
  RTSP sur loopback, l'absence d'écoute UDP, le refus d'un second publieur et
  le décodage FFmpeg. Une première lecture a expiré car le flux synthétique
  utilisait un intervalle d'images-clés trop long ; le test a réussi avec un
  intervalle d'une seconde. Le premier téléchargement de l'image MediaMTX
  a échoué car l'utilitaire d'authentification Docker n'était pas dans le
  PATH ; il a réussi après correction du PATH pour la commande.
- Le scénario complet HA → Go → caméra fictive → MediaMTX → JPEG a été rejoué
  avec la configuration intégrée et les variables de la stack : réussi.
  Les tests Go, race, vet et build ont réussi. L'image Go a été reconstruite ;
  son label et son aide ont été vérifiés. La première construction sans
  réseau n'avait pas le cache des modules ; une commande de contrôle du
  label a ensuite échoué à cause du quoting PowerShell et a été corrigée.
- La structure YAML, les déclencheurs, la condition de branche et les étapes
  du workflow ont été contrôlés statiquement. GitHub Actions, la publication
  GHCR, l'accès privé depuis Portainer et le téléchargement de l'artefact
  n'ont pas été exécutés.

## ÉTAT FINAL

**Observé historiquement :** un état v3 `blocked/BLOCKED` est rapporté sur le
serveur. Son état actuel et son association exacte à la caméra visée ne sont
pas vérifiés. Aucune donnée historique n'a été lue ou modifiée pendant cette
phase.

**Limite bloquante :** Go ne consulte pas ce budget. L'ajout HA démarre une
supervision avec reconnexions sans limite globale de durée ou de tentatives.
Lancer Go pour éviter l'ancien blocage ou présenter ce parcours comme un essai
strictement borné serait incorrect. Avant tout essai réel : identifier l'état
canonique et les producteurs, préparer et tester hors ligne une réconciliation
qui préserve l'historique, puis définir un contrôle borné vérifié et obtenir
les autorisations distinctes. Les services existants et les ports occupés
doivent également être pris en compte sans lancer un deuxième pont. La
commande `test` Go ne suffit pas à lever ce blocage.

Installation et essai réel restent donc **non exécutés et non autorisés**.
La publication automatique préparée ne prendra effet qu'après transmission
autorisée sur `main` et réussite du workflow ; le registre privé exige une
authentification Portainer. `latest` ne met pas à jour une stack existante.
Les instructions préparées sont dans `deployment/go-portainer.md`. Aucune
caméra ni aucun compte Netvue n'a été contacté ; seul un diagnostic SSH
passif du serveur a été effectué, sans modifier la production. Prochaine
étape : établir les faits sur l'état historique et préparer sa réconciliation
sûre avant de fixer les limites de l'essai.
