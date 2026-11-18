# Phase 39 — Talkback Netvue

## Objectif et décisions

Préparer l'émission audio vers la caméra avec réception A/V simultanée, sans
modifier la caméra ni les services de production. Le transport LAN classique,
la direction `TWOWAY` et le profil AAC-LC sont étayés par les analyses des
phases précédentes ; l'encapsulation ADTS émise et l'acceptation par la
caméra restent à vérifier.

Le contrôle passif a montré une connexion caméra active et recevant des
données. Après information sur le risque de perturber le flux existant,
l'utilisateur a expressément autorisé les essais concurrents ; aucune
modification ou redémarrage de production n'est autorisé.

L'analyse statique complémentaire de l'APK 5.27.0 situe l'envoi de
`TWOWAY` après l'ACK de sélection audio, sans attente explicite de l'ACK de
direction. `NVCameraPlayerImpl.onAudioRecorded` horodate les médias avec
`System.currentTimeMillis()`. La coupure du micro bloque localement les
trames après une courte fenêtre ; l'arrêt LAN ferme le connecteur, sans
commande `OFF` ou `Q=13` observée dans ce chemin. Ces constats ne prouvent
pas l'acceptation du talk par la Vigil.

L'APK envoie aussi le réglage `Q=7, V=1, P=[[0,1,[true]]]` (`audioOn=true`)
avant la sélection LIVE. Son effet matériel exact et sa persistance sont
inconnus ; le pont ne l'envoie pas car les essais n'autorisent pas de
modification de configuration. L'activation du microphone Android est
distincte. Le mode non bidirectionnel peut couper le retour audio dans
l'application ; cela ne prouve pas une coupure côté caméra.
Le cadrage statique est cohérent avec l'envoi Go : type 201, indicateur nul,
octet média `0x20`, timestamp little-endian puis charge AAC. Le choix ADTS du
code natif dépend d'options internes et n'est pas confirmé sur la Vigil.

## Home Assistant

L'entité actuelle fournit une lecture RTSP et n'a pas d'entrée microphone.
La capacité `two_way_audio` nécessite un chemin WebRTC effectif ; elle ne sera
pas annoncée sur la seule base de la publication RTSP. Le provider go2rtc ne
crée pas à lui seul un retour audio vers la passerelle. Il faut une terminaison
WebRTC ou un provider, un trajet de retour vers la session Go et une conversion
du codec microphone vers le format accepté. HA 2026.11.2 nécessite également
l'activation de la fonction expérimentale ; HLS ne véhicule pas le microphone.
[Documentation HA](https://contributors.home-assistant.io/core/entities/camera/#two-way-audio)

## Tests et état

Les 42 tests HA et Portainer réussissent dans le conteneur HA 2026.11.2
sans réseau, y compris le contrôle des écouteurs effectifs MediaMTX.
Les tests Go de chaque paquet, avec et sans détecteur de concurrence, ainsi
que `go vet` et `go build` réussissent. Les tests MediaMTX/FFmpeg et le test
HA → Go → caméra synthétique → MediaMTX réussissent en réseaux isolés.
Le test HA de bout en bout journalise néanmoins `Stream ended` à la fermeture
du lecteur.
Un flux RTSP synthétique H.264/AAC a également permis de vérifier FFprobe
et le détecteur de tonalité sur l'audio décodé en mémoire, sans sauvegarde
du média. Un harnais à source synthétique continue a également vérifié le
comptage des paquets AAC et leurs tailles, sans enregistrer le contenu.
Après changement de SPS/PPS, le pont conserve désormais la publication,
attend un IDR avec les nouveaux paramètres et continue l'audio ; un test
MediaMTX/FFmpeg synthétique vérifie les deux résolutions et un nouveau lecteur.

Les essais directs sont limités à dix connexions d'au plus 60 secondes. Le
signal synthétique préparé contient une tonalité de 700 Hz pendant 0,7 s ;
FFprobe identifie AAC-LC mono 8 kHz et son décodage présente un pic de 0,0311
en échelle normalisée.

Premier essai : Go annonce l'envoi complet et FFprobe identifie H.264 et AAC
mono 8 kHz. L'audio reçu devient silencieux pendant l'émission ; le lecteur
FFmpeg se termine après environ 6,8 s alors que la session Go reste active.
La connexion préexistante est toujours établie après l'essai. L'utilisateur
regardait alors la caméra dans l'application Netvue et n'a pas entendu la
tonalité ; la restitution par le haut-parleur n'est pas démontrée.

L'utilisateur a ensuite autorisé exceptionnellement la conservation d'un
extrait d'au plus 15 secondes du microphone de la caméra pour le prochain essai.
Cet extrait peut contenir des sons privés. Au deuxième essai, le flux reçu
est à nouveau devenu silencieux pendant l'envoi puis audible ; la tonalité
700 Hz n'est pas détectée dans l'extrait. Le lecteur RTSP s'est interrompu
après 6,5 s : le journal Go établit que la publication a redémarré à la
suite d'un changement de paramètres H.264. L'extrait de 15 s réunit trois
segments séparés par des reconnexions ; il ne prouve ni la sortie sonore du
haut-parleur ni la continuité perceptuelle. Après écoute de l'extrait,
l'utilisateur rapporte du vent mais aucune tonalité.

Troisième essai autorisé, sans enregistrement : un lecteur FFmpeg unique a
décodé environ 15 s d'A/V, y compris le passage réel de 640×360 à 1920×1080.
L'émission AAC a été envoyée pendant environ 4,2 s ; le retour audio décodé
est nul pendant environ 5 s, puis le bruit ambiant revient. Pendant cette
fenêtre, les paquets AAC restent présents mais leur charge est de 6 octets,
contre environ 400 à 500 octets avant et après. Cela confirme des trames
silencieuses reçues, pas leur cause ni la restitution par le haut-parleur.
FFmpeg a signalé un DTS de sortie non monotone au changement de résolution ;
le test ne permet pas de déterminer si l'origine est le flux ou la conversion
de l'horloge de sortie. La session préexistante était toujours établie après
l'essai. Aucun nouveau fichier audio caméra n'a été conservé.
