# Installer Netvue Bridge

## PrÃ©requis

- Un nÅ“ud Swarm Linux/amd64 sur le LAN, avec Home Assistant dans le mÃªme rÃ©seau
  host.
- Les ports locaux `8554` et `8765` disponibles.
- DÃ©pÃ´t GitHub public pour installer l'intÃ©gration avec HACS.
- Image `ghcr.io/mtx26/netvue-bridge-go` publique pour que Portainer la
  tÃ©lÃ©charge sans authentification.

La stack utilise une passerelle Go et MediaMTX. L'API et RTSP Ã©coutent sur
`127.0.0.1`, sans authentification distincte ; les processus partageant le
rÃ©seau host peuvent y accÃ©der.
Seul RTSP/TCP est activÃ© dans MediaMTX ; les transports UDP, MoQ et les
autres services sont dÃ©sactivÃ©s. Pour un test manuel, utiliser
[mediamtx.yml](./mediamtx.yml), pas la configuration par dÃ©faut de MediaMTX.

## 1. Ajouter la stack

Dans Portainer, ouvrir **Swarm â†’ Stacks â†’ Add stack â†’ Web editor** et coller
le contenu de [go-stack.yaml](./go-stack.yaml).

| Variable | Valeur |
|---|---|
| `NETVUE_NODE` | Nom du nÅ“ud Swarm qui hÃ©berge HA |
| `NETVUE_SOURCE` | IPv4 LAN locale de ce nÅ“ud |
| `NETVUE_BRIDGE_IMAGE` | Facultatif : tag ou digest Go Ã  la place de `latest` |

Le nom du nÅ“ud figure dans Portainer ; l'IPv4 est visible avec `ip -4 address`
sur le serveur. Cliquer sur **Deploy the stack**. Aucun fichier MediaMTX ni
port publiÃ© supplÃ©mentaire n'est nÃ©cessaire.

## 2. Installer Netvue Local avec HACS

Dans Home Assistant, ouvrir **HACS â†’ IntÃ©grations â†’ â‹® â†’ DÃ©pÃ´ts personnalisÃ©s**.
Ajouter `https://github.com/mtx26/netvue-ha` avec la catÃ©gorie **IntÃ©gration**,
puis installer **Netvue Local** et redÃ©marrer Home Assistant.

## 3. Ajouter une camÃ©ra

Dans Home Assistant, choisir **ParamÃ¨tres â†’ Appareils et services â†’ Ajouter
une intÃ©gration â†’ Netvue Local**, puis saisir le nom et l'IPv4 de la camÃ©ra.
La session dÃ©marre immÃ©diatement. VÃ©rifier l'image, le capteur RTSP et le
capteur de session. Le bouton de reprise lance une nouvelle tentative.

Le [guide Netvue Local](../custom_components/netvue_local/README.md) dÃ©crit
les entitÃ©s et le dÃ©pannage.

## Versions et vÃ©rification

Le [workflow GitHub](../.github/workflows/ci.yml) teste Go et HA,
puis publie l'image `latest` et le tag `sha-<commit>` sur `main`. HACS suit la
branche par dÃ©faut, ou une release GitHub si elle existe. L'accÃ¨s GHCR depuis
Portainer et l'installation rÃ©elle restent Ã  vÃ©rifier.

`latest` Ã©volue Ã  chaque publication mais ne met pas Ã  jour une stack dÃ©jÃ 
dÃ©ployÃ©e. Pour figer la version, utiliser le digest indiquÃ© dans le rÃ©sumÃ© du
lancement comme `NETVUE_BRIDGE_IMAGE`.

Sans dÃ©ploiement, vÃ©rifier le rendu du YAML depuis la racine du dÃ©pÃ´t :

```sh
NETVUE_NODE=validation-node \
NETVUE_SOURCE=192.0.2.10 \
docker stack config -c deployment/go-stack.yaml
```

Cette commande vÃ©rifie l'interpolation, pas l'accÃ¨s au registre ni le
dÃ©marrage des services.
