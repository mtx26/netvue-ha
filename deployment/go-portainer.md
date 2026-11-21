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
Seul RTSP/TCP est activé dans MediaMTX ; les transports UDP, MoQ et les
autres services sont désactivés. Pour un test manuel, utiliser
[mediamtx.yml](./mediamtx.yml), pas la configuration par défaut de MediaMTX.

## 1. Ajouter la stack

Dans Portainer, ouvrir **Swarm → Stacks → Add stack → Web editor** et coller
le contenu de [go-stack.yaml](./go-stack.yaml).

| Variable | Valeur |
|---|---|
| `NETVUE_NODE` | Nom du nœud Swarm qui héberge HA |
| `NETVUE_SOURCE` | IPv4 LAN locale de ce nœud |
| `NETVUE_BRIDGE_IMAGE` | Facultatif : tag ou digest Go à la place de `latest` |
| `NETVUE_RECORDING_RETENTION` | Facultatif : durée de conservation, `168h` par défaut |
| `NETVUE_RECORDING_LIMIT_BYTES` | Facultatif : limite globale en octets, `0` pour désactiver |

Un volume Docker local nommé par la stack est créé automatiquement sur le nœud
choisi. Le service `recordings-init` prépare ses droits pour les deux services
non-root. Le volume persiste après suppression de la stack ; sa suppression
efface les enregistrements. Il reste sur ce nœud et ne suit pas un changement
de `NETVUE_NODE`.

Si une ancienne installation utilisait un répertoire hôte, ses enregistrements
ne sont pas transférés automatiquement dans ce nouveau volume. Conserver ce
répertoire et prévoir une migration avant de le supprimer.

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

## 4. Facultatif : utiliser le micro à distance avec Cloudflare TURN

Garde ton URL HTTPS et ton tunnel Cloudflare actuels. Le tunnel transporte
Home Assistant et la signalisation WebRTC. **Cloudflare Realtime TURN**,
un service distinct, relaie l'image et le son WebRTC si le navigateur ne
peut pas joindre directement le pont. Rien à changer dans le DNS, rien
à installer sur le téléphone, aucun port à ouvrir sur la box. Le RTSP et
l'API du pont restent sur `127.0.0.1`.

1. Dans le [tableau de bord Cloudflare Realtime](https://dash.cloudflare.com/?to=/:account/calls),
   crée une [clé TURN](https://developers.cloudflare.com/realtime/turn/generate-credentials/).
   Si le lien ouvre **Serverless SFU**, sélectionne **TURN** dans la
   navigation Realtime : **ne crée pas d'application SFU** pour Netvue.
   Récupère l'**ID de la clé TURN** et le **jeton API généré pour cette
   même clé**. L'App ID et l'App Secret d'une application SFU ne
   fonctionnent pas, même s'ils ressemblent aux valeurs TURN. N'utilise
   pas non plus l'ID ou le jeton de Cloudflare Tunnel, ni un jeton API
   Cloudflare générique.
2. Mets à jour la stack Portainer avec le [`go-stack.yaml`](./go-stack.yaml)
   et une image Go contenant le support TURN. Dans **Environment
   variables**, ajoute ces deux valeurs, puis redéploie :

   | Variable | Valeur |
   |---|---|
   | `NETVUE_CLOUDFLARE_TURN_KEY_ID` | ID de la clé TURN Realtime |
   | `NETVUE_CLOUDFLARE_TURN_API_TOKEN` | Jeton API de cette clé |

   Une seule clé suffit pour toutes les caméras. Ne colle pas le jeton
   dans le YAML, Git, des logs ou une capture d'écran. Les administrateurs
   Portainer/Docker peuvent lire les variables de la stack. Le pont garde
   le jeton côté serveur et ne transmet que des identifiants temporaires
   à Home Assistant et au navigateur.
3. Mets aussi à jour **Netvue Local** dans HACS. Redémarre Home Assistant
   après la mise à jour ; si l'intégration était déjà à jour, recharge son
   entrée. Le lecteur WebRTC est toujours actif, sans option : ajoute la carte
   Lovelace `netvue-camera-card` (`type: custom:netvue-camera-card`,
   `entity: camera.…`) au tableau de bord.
4. Sur ton téléphone, coupe le Wi-Fi et ouvre **la même URL HTTPS Home
   Assistant** en 4G/5G. Ouvre la caméra, maintiens le bouton micro
   de la carte enfoncé et autorise le micro. Vérifie que le son est audible sur le haut-parleur de la
   caméra. L'activité TURN du tableau de bord Cloudflare ou le type
   `relay` du couple ICE sélectionné dans les diagnostics WebRTC du
   navigateur confirme l'usage du relais.

**Dépannage.** Le pont doit joindre `rtc.live.cloudflare.com` en HTTPS
(TCP 443). Le pont et le navigateur doivent pouvoir joindre
`turn.cloudflare.com` en UDP 3478 ou en TURN/TLS sur TCP 443. Ces
connexions sont sortantes. Une erreur `Cloudflare API HTTP 404` peut
indiquer que Cloudflare ne trouve pas la clé TURN. Vérifie que l'ID
correspond à une clé encore présente dans **Realtime → TURN** et que
le jeton vient de cette même clé. Si tu n'as plus le bon jeton, crée une
nouvelle clé TURN, remplace **les deux variables** dans Portainer et
redéploie. Pour `401` ou `403`, vérifie aussi le couple ID/jeton. Un
échec TURN n'arrête pas la publication RTSP : WebRTC direct reste
possible sur le LAN.

Le pont accepte jusqu'à 4 spectateurs WebRTC par caméra ; au-delà, la carte
affiche « trop de visionneuses ». Un seul spectateur à la fois peut parler
(les autres reçoivent « occupé »). Appuyer sur le micro ne recrée jamais la
session.

**Repli et coût.** Avec TURN configuré, les caméras Netvue utilisent
la carte `netvue-camera-card`, **sans repli HLS**. Les autres caméras et les flux RTSP ne changent pas. Pour
revenir au comportement local, enlève **les deux variables**, redéploie
la stack et recharge les entrées Netvue. Les laisser toutes deux vides
désactive TURN ; en renseigner une seule empêche le démarrage du pont.
La [tarification Realtime](https://developers.cloudflare.com/realtime/sfu/platform/pricing/)
annonce 1 000 Go sortants par mois offerts, partagés entre TURN et SFU,
puis 0,05 $ US/Go. Une lecture WebRTC directe sur le LAN ne consomme
pas ce quota.

## Versions et vérification

Le [workflow GitHub](../.github/workflows/ci.yml) teste Go et HA,
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
