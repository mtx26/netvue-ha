# Netvue Local pour Home Assistant

L'intégration conserve les entrées HA et pilote le pont Go ; seul le pont
effectue découverte LAN, login et réception vidéo.

## Installation

- Pont Go et MediaMTX sur le même nœud et réseau host que HA : MediaMTX
  `127.0.0.1:8554`, API `127.0.0.1:8765`.
- L'IPv4 de `NETVUE_SOURCE` doit couvrir le réseau des caméras.
- Ne publiez pas ces ports : API sans authentification distincte.

Dans HACS, ajouter `https://github.com/mtx26/netvue-ha` comme dépôt
personnalisé de catégorie **Intégration**, installer **Netvue Local**, puis
redémarrer Home Assistant. Le dépôt doit être public.

Suivre le [guide Portainer](../../deployment/go-portainer.md) pour installer le
pont Go et MediaMTX.

## Ajouter et gérer une caméra

Dans **Paramètres → Appareils et services → Ajouter une intégration**, choisir
**Netvue Local**. Choisir **Rechercher des caméras** pour demander au pont une
découverte LAN (à utiliser uniquement avec autorisation), puis sélectionner
une adresse et saisir un nom. Une liste vide ou une erreur permet de réessayer
ou de passer à la saisie manuelle ; celle-ci est également proposée dès le
début et après une recherche. Les adresses déjà configurées sont refusées.
La découverte ne démarre pas de session ; la création de l'entrée le fait.

La découverte exige un pont prenant en charge `POST /api/v1/discovery` sans
corps, avec réponse JSON `{"cameras":[{"host":"192.168.x.y"}]}` ; une liste
vide est valide et une erreur HTTP est signalée. Si cette API est absente,
utiliser la saisie manuelle. L'intégration génère le nom et l'URL RTSP ; elle
ne demande ni URL, port ni identifiants :

```text
192.168.10.2 → netvue_192_168_10_2
             → rtsp://127.0.0.1:8554/netvue_192_168_10_2
```

Cette opération démarre une session caméra. Une entrée HA restaurée peut
également être réconciliée dès qu'un gestionnaire répond : voir les
[précautions de déploiement](../../deployment/go-portainer.md) avant tout
essai ou redémarrage. HA ne parle pas le protocole Netvue.

Une session et un superviseur Go sont gérés par entrée ; le nombre maximal
n'est pas fixé et la capacité dépend des ressources/caméras/MediaMTX. Le
registre Go est volatil, HA restaure ses entrées. L'entité caméra lit via HA
Stream ; le capteur binaire indique la disponibilité SDP RTSP/H.264. Le
capteur **Session** expose l'état du superviseur et une catégorie d'erreur
non sensible ; le bouton **Redémarrer la session** est disponible en état
arrêté ou en échec et ne garantit pas la disponibilité immédiate du flux. Une session
supervisée peut reconnecter, sans exposer dans HA la cause de chaque erreur
transitoire.

### Métadonnées locales et diagnostic

Si le pont les fournit, la fiche appareil indique le fabricant **Netvue**, le
numéro de série et le firmware. Aucun modèle n'est inféré. La série n'est pas
un identifiant : deux entrées avec une même série restent des appareils séparés.
Les identifiants existants des appareils et des entités sont conservés.

Les métadonnées sont facultatives, y compris avec un ancien pont. Les mises à
jour du coordinator actualisent le firmware sans recréer les entités ni
redémarrer le flux. Si une réponse omet la série ou le firmware, le registre
conserve la dernière valeur connue.

Le capteur **Diagnostic** existant regroupe les attributs facultatifs
`connection_port`, `wifi_ssid`, `wifi_signal_level` (entier brut **sans unité
connue**) et `capabilities` (`audio`, `flip`, `light`, `sd_card`). Une capacité
absente ou nulle est inconnue, pas fausse ; seules les valeurs booléennes
connues sont exposées. À chaque réponse valide, les valeurs facultatives absentes
sont effacées des attributs de diagnostic; série et firmware conservent leur
dernière valeur non vide. Une absence de réponse ne purge pas le dernier état
connu. Aucune entité supplémentaire ni requête cloud n'est ajoutée, et
l'intégration ne journalise pas ces métadonnées.

### Miniature de caméra

HA demande une image JPEG de 80 × 80 pixels pour le badge à côté du nom.
L'intégration y inscrit l'image complète, sans déformation, sur fond noir ;
les marges protègent aussi les badges arrondis. Ce traitement s'applique à
toute demande explicite de 80 × 80, pas uniquement à ce badge. Les autres
captures ne sont pas rendues carrées et HA peut les réduire proportionnellement.
Le flux vidéo principal n'est pas modifié.

### Carte Lovelace `netvue-camera-card` (seul lecteur interactif)

L'intégration sert la carte `netvue-camera-card` (dossier `frontend/`, sans
étape de build) et l'ajoute automatiquement aux modules JS de Home Assistant,
y compris si `frontend` se charge après l'intégration. Ajoutez-la à un tableau
de bord :

```yaml
type: custom:netvue-camera-card
entity: camera.ma_camera
name: Entrée   # facultatif
```

La version 0.2.2 corrige le plantage `Illegal invocation` du lecteur. La
version 0.2.3 ajoute le plein écran et deux cartes Lovelace complémentaires.
La version 0.2.4 corrige l'ajout de la synthèse d'état sans configuration :
elle détecte les entités Netvue liées au même appareil.
La version 0.2.5 ajoute les éditeurs visuels des trois cartes.
La version 0.2.6 versionne aussi le chemin des modules importés afin d'éviter
qu'un ancien fichier JavaScript reste en cache après une mise à jour.
La version 0.2.7 remplace les emojis des contrôles par des icônes SVG
uniformes et place le microphone, le son puis le plein écran dans cet ordre.
La version 0.2.8 corrige la sortie du plein écran et ajoute des flèches pour
parcourir les caméras du mur sans quitter le plein écran.
Le bouton plein écran permet de revenir au tableau de bord avec Échap ou en
appuyant de nouveau sur le bouton. Il est désactivé si le navigateur ne prend
pas cette fonction en charge.

Après mise à jour, redémarrez Home Assistant puis rechargez complètement la
page du tableau de bord si l'ancienne version reste en cache.

Les trois cartes peuvent être ajoutées et configurées depuis l'éditeur visuel
de Home Assistant, sans saisir de YAML : choix d'une caméra pour le lecteur,
sélection multiple pour le mur et pour la synthèse d'état. Le YAML reste
disponible pour les réglages avancés.

La carte ouvre **une seule** `RTCPeerConnection` pour la vidéo H.264, l'audio
caméra (Opus) et le push-to-talk, via les commandes WebSocket natives de HA
(`camera/webrtc/offer`, `camera/webrtc/candidate`, `camera/webrtc/get_client_config`)
et le fournisseur `webrtc.py`. La vidéo démarre seule, en sourdine si le
navigateur l'exige ; le bouton haut-parleur active le son de la même balise
`<video>`. Le bouton « Maintenir pour parler » (souris, tactile, Espace ou
Entrée) remplace la piste micro par `replaceTrack` : aucune renégociation, aucun
nouveau lecteur, la vidéo n'est jamais interrompue. Le micro est relâché dès
que le bouton, la page, la fenêtre ou la connexion se ferme. Le navigateur
demande l'autorisation du micro ; HTTPS est requis hors de `localhost`.

La carte est le seul lecteur interactif : il n'y a plus d'option WebRTC ni
micro, et la capacité native HA `two_way_audio` n'est plus exposée. Le
lecteur natif de HA ne sert plus que pour les aperçus ; RTSP et `stream_source`
restent disponibles pour les intégrations externes. Les anciennes options
`webrtc_enabled` et `experimental_talkback` sont retirées silencieusement des
entrées existantes.

### Mur multi-caméras `netvue-camera-wall-card`

Le mur affiche plusieurs lecteurs `netvue-camera-card` dans une grille qui
s'adapte à la largeur disponible :

```yaml
type: custom:netvue-camera-wall-card
entities:
  - camera.entree
  - camera.garage
```

Chaque caméra ouvre son propre lecteur WebRTC et conserve les commandes audio,
micro et plein écran. En plein écran, les flèches latérales parcourent les
caméras dans l'ordre de la liste, avec retour au début ou à la fin.
Afficher plusieurs flux simultanément augmente
l'utilisation du réseau et des ressources ; limiter la liste aux caméras à
surveiller en même temps.

### Synthèse d'état `netvue-camera-status-card`

La synthèse regroupe la disponibilité, la session et, facultativement, le
diagnostic de chaque caméra. Les identifiants ci-dessous sont des exemples :
remplacez-les par les entités réellement créées dans Home Assistant.

```yaml
type: custom:netvue-camera-status-card
cameras:
  - camera: camera.entree
    unavailable: binary_sensor.entree_camera_indisponible
    session: sensor.entree_etat_de_la_session
    diagnostic: sensor.entree_diagnostic_camera
```

`camera` et `unavailable` sont requis ; `session` et `diagnostic` sont
facultatifs. Le compteur « disponibles » repose sur le capteur
**Caméra indisponible**. Sans bloc `cameras`, la carte tente de retrouver
automatiquement les entités liées à chaque caméra Netvue.

La caméra Netvue est traitée en **demi-duplex** : pendant le push-to-talk,
le pont coupe l'audio caméra envoyé à ce navigateur. Plusieurs spectateurs
(4 sessions WebRTC par caméra au maximum) peuvent regarder en même temps ;
le serveur gère `busy`/`unavailable` si le micro est déjà pris. En cas de
coupure, la carte se reconnecte (1 s, 2 s, 4 s, 3 tentatives) puis affiche un
bouton « Réessayer ».

Le pont reçoit le microphone WebRTC en Opus, le convertit en AAC-LC/ADTS et
l'envoie sur la session Netvue supervisée ; FFmpeg est nécessaire. Le
canal de données `netvue-ptt` (`start`/`stop`/`keepalive`) pilote la prise de
parole. **Non validé sur matériel** : la restitution par le haut-parleur de la
caméra n'a jamais été confirmée ; seuls des essais synthétiques et des tests
de la carte avec faux navigateur existent.

La commande Go expérimentale `talk` lit un fichier AAC préencodé dans une
session distincte et bornée ; elle ne fait pas partie du parcours HA et ne doit
pas être lancée en parallèle de la session supervisée.

### Signalisation WebRTC

Le serveur Go et le fournisseur HA implémentent la signalisation et le trajet
média ; le fournisseur est toujours actif. Les essais d'intégration média nécessitent FFmpeg et une validation acoustique
est encore requise. L'API est :

| Méthode et chemin, sous `/api/v1` | Corps ou réponse JSON |
|---|---|
| `PUT /cameras/{host}/webrtc/{session_id}` | `{"offer":"<SDP>"}` ; `201` crée la session identifiée par HA, `200` confirme une répétition identique, `409` refuse un identifiant réutilisé avec une autre offre. |
| `POST /cameras/{host}/webrtc/{session_id}/candidates` | `{"candidate":{"candidate":"...","sdpMid":"0","sdpMLineIndex":0,"usernameFragment":null}}` ; `204` accepte. Les champs optionnels gardent leurs valeurs ou `null`. |
| `GET /cameras/{host}/webrtc/{session_id}/events?after=<n>` | Attente bornée à 30 s maximum, puis `200 {"events":[{"seq":1,"type":"answer","answer":"<SDP>"},{"seq":2,"type":"candidate","candidate":{...}}]}` ou liste vide. Une erreur est `{"seq":3,"type":"error","code":"...","message":"..."}`. |
| `DELETE /cameras/{host}/webrtc/{session_id}` | `204` même si déjà fermée ; libère le pair et ses ressources. |

Les séquences commencent à 1, restent ordonnées et sont rejouables après le
curseur fourni : `after=0` récupère les premiers événements. Réponse et
candidates serveur sont donc possibles après l'offre. Le client sérialise
l'ouverture et les candidates ; une fermeture demandée pendant l'ouverture
attend son résultat puis supprime la session. Le pont limite taille SDP,
nombre de sessions/événements et durée de vie, et applique un tombstone à la
fermeture. Une expiration ou session inconnue renvoie `404`/`410`, un corps
invalide `400`, une caméra non gérée `404`, et une indisponibilité `503`. Une
erreur de négociation est un événement `error` terminal. Les SDP, adresses ICE
et identifiants ne sont pas journalisés. L'API reste sur loopback, non
authentifiée comme le reste du gestionnaire. Le contrat limite à 4 pairs
WebRTC par caméra (`409` au-delà, remonté comme `netvue_webrtc_busy`) : vidéo et audio proviennent
du RTSP/TCP ; le microphone Opus est converti en AAC-LC/ADTS et joint à la
même session Netvue supervisée, jamais une seconde session caméra. Le retour
AAC caméra → Opus WebRTC est également implémenté.

Si l'offre WebRTC ne peut pas recevoir le profil et le niveau H.264 du flux
RTSP, le pont peut réencoder la vidéo en H.264 Constrained Baseline 3.1
(jusqu'à 720p, 15 images/s) avec FFmpeg. Ce traitement ne s'applique qu'au
lecteur WebRTC et n'affecte ni la publication RTSP ni la caméra.

Audit du code officiel [HA 2026.11.2](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/components/camera/webrtc.py) :
`CameraWebRTCProvider` sélectionne les sources RTSP via `async_is_supported`,
reçoit offre et candidates, envoie `WebRTCAnswer`, `WebRTCCandidate` ou
`WebRTCError`, puis ferme la session par callback synchrone. HA conserve HLS
avec un fournisseur ; une méthode WebRTC native remplaçant celle de `Camera`
supprime ce repli. La sélection du premier fournisseur compatible n'assure
pas une priorité stable entre fournisseurs. `two_way_audio` n'est pas exposé : le micro passe par la carte.
L'intégration
officielle [go2rtc](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/components/go2rtc/__init__.py)
utilise un WebSocket de signalisation ; le seul RTSP ne fournit pas le retour
audio Netvue. Le choix du fournisseur HA se fait uniquement par URL : une
autre entité utilisant exactement la même URL pourrait le sélectionner, mais
ce fournisseur refuse les offres d'une autre entité. L'API HA ne permet pas
de sélectionner un fournisseur par entité ni de garantir sa priorité face à
go2rtc. Sans découverte de capacité Go convenue, l'activation manuelle peut
annoncer WebRTC même si le serveur est incompatible ; l'offre échouera alors,
sans repli automatique garanti côté lecteur. HLS reste annoncé par HA.
Sans TURN, le pair Go annonce uniquement l'IPv4 source configurée en UDP.
Un accès HTTPS à Home Assistant via Cloudflare Tunnel ne relaie pas ce média.
Si [Cloudflare TURN est configuré](../../deployment/go-portainer.md#4-facultatif--utiliser-le-micro-à-distance-avec-cloudflare-turn),
Netvue utilise le mode WebRTC natif de HA pour fournir au navigateur des
identifiants ICE temporaires pour ses caméras. Dans ce mode, HA ne propose
plus HLS comme repli et ne sélectionne pas go2rtc pour ces entités.
Le pont garde des candidats directs et ajoute les candidats du relais.
Les conversions et la
signalisation sont testées synthétiquement, mais ne prouvent ni l'acceptation
du talkback par la caméra ni la restitution audio réelle. Si le `DELETE` côté
HA est interrompu, le pont a déjà installé un tombstone et poursuit la fermeture
en arrière-plan ; le client peut répéter la suppression.

[MediaMTX](https://mediamtx.org/docs/read/webrtc) propose la lecture WHEP
(H.264 et Opus/G.711, pas AAC WebRTC), mais la lecture n'établit pas à elle
seule un trajet microphone vers la session Netvue. [go2rtc](https://github.com/AlexxIT/go2rtc/blob/master/internal/webrtc/README.md)
documente un mode WebRTC bidirectionnel ; il faut néanmoins relier et
transcoder l'audio entrant vers le protocole Netvue. La connectivité ICE
nécessite des adresses et ports accessibles ([MediaMTX](https://mediamtx.org/docs/features/webrtc-specific-features)).
Le pont ajoute Pion WebRTC/Opus et utilise FFmpeg pour les conversions AAC ↔
Opus. Le paquet officiel
[HA 2026.11.2](https://github.com/home-assistant/core/blob/2026.11.2/pyproject.toml)
requiert Python **3.14.2 minimum**, et non 3.13.2.

L'entité **Caméra indisponible** passe à `on` quand le flux RTSP/H.264 n'est
pas disponible ; elle sert directement de déclencheur à une automatisation
ou une notification HA. Pour limiter les faux positifs, configurez le
déclencheur d'état sur `on` avec un délai (par exemple deux minutes). L'entité
**Diagnostic caméra** distingue notamment une panne du gestionnaire, un refus
d'authentification, une erreur de connexion et un flux interrompu. Son attribut
`details` conserve le diagnostic technique utile aux journaux ; l'état de la
session reste disponible séparément.

La configuration n'exige que la découverte (ou une adresse IPv4 saisie) et un
nom. L'intégration génère l'URL RTSP et crée les entités automatiquement.

Le nom affiché se modifie depuis l'appareil/l'entité ; les options de
l'intégration changent l'IPv4, arrêtent l'ancien flux et démarrent le nouveau.
La suppression de l'intégration arrête et retire la session. Une ancienne
entrée sans nom géré ou avec URL personnalisée est refusée, jamais convertie
ou réécrite automatiquement.

## Dépannage et erreurs de cycle de vie

- **Gestionnaire absent** : vérifier service `netvue-bridge` `1/1`, réseau
  partagé et écoute `127.0.0.1:8765`. HA réessaie l'entrée.
- **Pas de flux** : vérifier MediaMTX `127.0.0.1:8554`, état de session et
  journaux du pont ; pour un échec définitif, consulter `error`, corriger la
  cause puis reprendre explicitement.
- Au déchargement, HA retire les plateformes avant de supprimer la session.
  En cas d'échec, il tente de restaurer les plateformes ; le pont conserve
  session/état si leur retrait échoue. Une suppression échouée peut nécessiter
  un redémarrage HA. Si la réponse est incertaine, le coordinator ne recrée
  pas automatiquement une session manquante pendant cette exécution ; il
  faut confirmer la suppression avant toute nouvelle configuration. Cette
  protection en mémoire n'est pas un budget persistant.
- Si la création n'a pas reçu de réponse, cette caméra est bloquée pour
  nouvelles créations, reprises et suppressions durant cette exécution :
  une requête précédente peut encore aboutir. Un redémarrage ne dissipe pas ce
  doute : vérifier les producteurs et la session avant toute action.
- L'API est joignable par les processus du même réseau host. Les tests
  synthétiques n'utilisent aucune caméra réelle et ne prouvent ni compatibilité
  des autres modèles ni disponibilité prolongée.
