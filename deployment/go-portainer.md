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

## 4. Facultatif : utiliser le micro à distance avec Cloudflare TURN

Garde ton URL HTTPS et ton tunnel Cloudflare actuels. Le tunnel transporte
Home Assistant et la signalisation WebRTC. **Cloudflare Realtime TURN**,
un service distinct, relaie l'image et le son WebRTC si le navigateur ne
peut pas joindre directement le pont. Rien à changer dans le DNS, rien
à installer sur le téléphone, aucun port à ouvrir sur la box. Le RTSP et
l'API du pont restent sur `127.0.0.1`.

1. Dans le [tableau de bord Cloudflare Realtime](https://dash.cloudflare.com/?to=/:account/calls),
   crée une [clé TURN](https://developers.cloudflare.com/realtime/turn/generate-credentials/).
   Récupère l'**ID de la clé** et son **jeton API**. Ce ne sont ni l'ID
   du tunnel Cloudflare Tunnel ni le jeton `cloudflared`.
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
   entrée. Dans les **Options de la caméra**, coche **Signalisation WebRTC
   expérimentale** et **Contrôle microphone expérimental**. Si ta version
   de Home Assistant le demande, active aussi l'audio bidirectionnel dans
   **Paramètres → Système → Labs**.
4. Sur ton téléphone, coupe le Wi-Fi et ouvre **la même URL HTTPS Home
   Assistant** en 4G/5G. Ouvre la caméra, clique sur **Parler** et autorise
   le micro. Vérifie que le son est audible sur le haut-parleur de la
   caméra. L'activité TURN du tableau de bord Cloudflare ou le type
   `relay` du couple ICE sélectionné dans les diagnostics WebRTC du
   navigateur confirme l'usage du relais.

**Dépannage.** Le pont doit joindre `rtc.live.cloudflare.com` en HTTPS
(TCP 443). Le pont et le navigateur doivent pouvoir joindre
`turn.cloudflare.com` en UDP 3478 ou en TURN/TLS sur TCP 443. Ces
connexions sont sortantes. Une erreur `Cloudflare API HTTP 401` ou `403`
dans les journaux du pont indique de vérifier le couple ID/jeton. Un
échec TURN n'arrête pas la publication RTSP : WebRTC direct reste
possible sur le LAN.

**Repli et coût.** Avec TURN configuré, les caméras Netvue ayant activé
WebRTC utilisent le lecteur WebRTC natif de HA, **sans repli HLS** dans
ce lecteur. Les autres caméras et les flux RTSP ne changent pas. Pour
revenir au comportement local, enlève **les deux variables**, redéploie
la stack et recharge les entrées Netvue. Les laisser toutes deux vides
désactive TURN ; en renseigner une seule empêche le démarrage du pont.
La [tarification Realtime](https://developers.cloudflare.com/realtime/sfu/platform/pricing/)
annonce 1 000 Go sortants par mois offerts, partagés entre TURN et SFU,
puis 0,05 $ US/Go. Une lecture WebRTC directe sur le LAN ne consomme
pas ce quota.

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
