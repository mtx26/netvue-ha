# Installer Netvue Bridge

## Prérequis

- Un nœud Swarm Linux/amd64 sur le LAN, avec Home Assistant dans le même réseau
  host.
- Les ports locaux `8554` et `8765` disponibles.
- Dépôt GitHub public pour installer l'intégration avec HACS.
- Image `ghcr.io/mtx26/netvue-bridge-go` publique pour que Portainer la
  télécharge sans authentification.

La stack utilise une passerelle Go et MediaMTX. L'API et RTSP écoutent sur
`127.0.0.1`, sans authentification distincte ; les processus partageant le
réseau host peuvent y accéder.

## 1. Ajouter la stack

Dans Portainer, ouvrir **Swarm → Stacks → Add stack → Web editor** et coller
le contenu de [go-stack.yaml](./go-stack.yaml).

| Variable | Valeur |
|---|---|
| `NETVUE_NODE` | Nom du nœud Swarm qui héberge HA |
| `NETVUE_SOURCE` | IPv4 LAN locale de ce nœud |
| `NETVUE_BRIDGE_IMAGE` | Facultatif : tag ou digest Go à la place de `latest` |

Le nom du nœud figure dans Portainer ; l'IPv4 est visible avec `ip -4 address`
sur le serveur. Cliquer sur **Deploy the stack**. Aucun fichier MediaMTX ni
port publié supplémentaire n'est nécessaire.

## 2. Installer Netvue Local avec HACS

Dans Home Assistant, ouvrir **HACS → Intégrations → ⋮ → Dépôts personnalisés**.
Ajouter `https://github.com/mtx26/netvue-ha` avec la catégorie **Intégration**,
puis installer **Netvue Local** et redémarrer Home Assistant.

## 3. Ajouter une caméra

Dans Home Assistant, choisir **Paramètres → Appareils et services → Ajouter
une intégration → Netvue Local**, puis saisir le nom et l'IPv4 de la caméra.
La session démarre immédiatement. Vérifier l'image, le capteur RTSP et le
capteur de session. Le bouton de reprise lance une nouvelle tentative.

Le [guide Netvue Local](../custom_components/netvue_local/README.md) décrit
les entités et le dépannage.

## Versions et vérification

Le [workflow GitHub](../.github/workflows/publish-go-image.yml) teste Go et HA,
puis publie l'image `latest` et le tag `sha-<commit>` sur `main`. HACS suit la
branche par défaut, ou une release GitHub si elle existe. L'accès GHCR depuis
Portainer et l'installation réelle restent à vérifier.

`latest` évolue à chaque publication mais ne met pas à jour une stack déjà
déployée. Pour figer la version, utiliser le digest indiqué dans le résumé du
lancement comme `NETVUE_BRIDGE_IMAGE`.

Sans déploiement, vérifier le rendu du YAML depuis la racine du dépôt :

```sh
NETVUE_NODE=validation-node \
NETVUE_SOURCE=192.0.2.10 \
docker stack config -c deployment/go-stack.yaml
```

Cette commande vérifie l'interpolation, pas l'accès au registre ni le
démarrage des services.
