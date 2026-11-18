# Phase 27 — Installation Go dans Portainer

## OBJECTIF

Rendre l'image de la passerelle Go et la stack Portainer simples à préparer,
avec un parcours documentaire unique pour les nouvelles installations. Le
résultat attendu est une image Linux/amd64 reproductible et une procédure qui
explique comment la rendre disponible au nœud Swarm, sans déployer en
production.

## MODIFICATIONS

- Le Dockerfile Go conserve le builder Go 1.26.1 épinglé par digest, la
  compilation statique Linux/amd64 et l'image d'exécution `scratch`. Un label
  OCI rattache l'image au dépôt source.
- Ajout de `.github/workflows/publish-go-image.yml`, déclenché manuellement et
  distinct du workflow Python existant. Il lance les tests Go, la détection
  des courses et `go vet`, puis publie une image `ghcr.io/mtx26/netvue-bridge-go`
  avec un tag de commit et affiche son digest. Le workflow n'a pas été lancé ;
  aucune image n'a été publiée.
- Le guide Portainer détaille la construction locale mono-nœud et l'usage
  optionnel d'une image GHCR épinglée par digest, l'accès au registre si le
  package est privé, l'architecture Linux/amd64, les variables nécessaires et
  les contraintes du réseau host.
- Le README Home Assistant renvoie vers le guide Go et précise que le guide
  Portainer général décrit l'ancien déploiement Python. Le README Go mentionne
  les deux parcours de construction/distribution.
- La stack `deployment/go-stack.yaml` n'a pas nécessité de modification :
  elle utilise déjà une instance Go, MediaMTX partagé, le réseau host,
  l'adresse de contrôle loopback et des ressources limitées.

## FONCTIONNEMENT

Pour une installation mono-nœud, l'image peut être construite sur le nœud
Linux/amd64 affecté aux services et référencée avec son tag local. Pour un
Swarm multi-nœud, l'image doit être accessible aux nœuds depuis un registre ;
la procédure recommande alors le digest produit par l'étape manuelle GHCR.
Cette publication n'est pas automatique et reste une action distincte.

Go continue de gérer les sessions Netvue et son API locale. L'intégration Home
Assistant reste en Python et conserve ses entrées de configuration ; aucune
modification du contrat API ni nouvelle fonctionnalité HA n'a été ajoutée.
MediaMTX et l'API continuent d'écouter sur loopback dans l'espace réseau host
partagé, sans port publié par la stack.

## TESTS

- `go test ./...` : réussi.
- `go test -race ./...` : réussi.
- `go vet ./...` : réussi.
- Construction locale Docker Linux/amd64 : réussie, sans avertissement après
  retrait du `--platform` constant dans le Dockerfile.
- Inspection de l'image : Linux/amd64, utilisateur `10001:10001`, point
  d'entrée `/netvue-bridge` ; démarrage de `serve --help` réussi.
- `docker stack config` avec des variables synthétiques : réussi ; la sortie
  contient les deux services, le réseau host et les adresses loopback attendues.
- Analyse YAML du workflow et vérification des liens de documentation :
  réussies.
- Aucun déploiement, publication externe ou contact de caméra n'a été réalisé.

## ÉTAT FINAL

La documentation et la configuration pour construire localement l'image Go
ou préparer sa publication manuelle sont disponibles. Le workflow GHCR est
préparé mais non déclenché. La compatibilité de l'intégration HA avec le
contrat existant est conservée ; seul son parcours documentaire a été corrigé.

Restent à vérifier séparément : disponibilité/visibilité du package GHCR,
fonctionnement du réseau host et des bind mounts sur le nœud Debian cible,
ports occupés, accès HA au loopback et installation effective dans Portainer.
Ces vérifications ne sont pas un déploiement et nécessitent une autorisation
distincte pour la production. Aucune compatibilité caméra réelle n'est établie.
