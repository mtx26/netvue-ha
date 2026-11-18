# Pont Netvue Go

Une instance Go gère plusieurs caméras ; `netvue_local` reste l'interface
Home Assistant. Pour Docker/Portainer, image GHCR et précautions avant
déploiement, voir le [guide dédié](../deployment/go-portainer.md).

## Prérequis et tests

Go 1.26+ ; MediaMTX local pour la publication. Les tests unitaires ne
nécessitent ni caméra, ni compte, ni serveur RTSP. Depuis la racine :

```sh
go test ./...
go test -race ./...
go vet ./...
go build ./cmd/netvue-bridge
```

Le test vidéo de bout en bout fabrique un flux, lance MediaMTX temporaire sur
loopback et le lit avec FFmpeg. Sous PowerShell, adapter les chemins locaux :

```powershell
$env:NETVUE_TEST_MEDIAMTX = "C:\tools\mediamtx.exe"
$env:NETVUE_TEST_FFMPEG = "C:\tools\ffmpeg.exe"
$env:NETVUE_TEST_FFPROBE = "C:\tools\ffprobe.exe"
go test -run 'TestMediaMTX.*PublicationDecodesWithFFmpeg' -v ./internal/netvue
```

Le test A/V vérifie également les pistes H.264 et AAC avec FFprobe lorsque
`NETVUE_TEST_FFPROBE` est défini ; la CI le définit systématiquement.

Les autres tests de transport utilisent des réponses fabriquées ou un pair
local. Aucun test ne doit recevoir l'adresse d'une caméra réelle.

## Commandes et limites d'exécution

`test` effectue une session bornée ; `run` supervise jusqu'à l'arrêt ; `serve`
expose le gestionnaire HTTP et démarre une session indépendante par caméra
ajoutée par HA. Les trois réutilisent le même moteur. **L'ajout ou la
réconciliation HA lance découverte LAN, login et demande de flux réels**.
Les prérequis sont indiqués dans le [guide d'installation](../deployment/go-portainer.md).

```text
netvue-bridge test --source 192.168.10.10 --camera 192.168.10.20 \
  --rtsp rtsp://127.0.0.1:8554/netvue_test --duration 15s
netvue-bridge run --source 192.168.10.10 --camera 192.168.10.20 \
  --rtsp rtsp://127.0.0.1:8554/netvue_test
netvue-bridge serve --source 192.168.10.10
```

Adresses d'exemple. `test` exige une durée positive explicite sans plafond
maximal ; sa durée inclut préflight RTSP, découverte, authentification et
établissement de publication. Le démarrage vidéo a une échéance absolue de
5 s, non repoussée par les commandes seules. `run` continue jusqu'à Ctrl+C ou
SIGTERM. Il réessaie séquentiellement les erreurs récupérables après 2, 4, 8,
16 puis 30 s maximum ; après 5 min de fonctionnement, le délai revient à 2 s.
Il n'y a pas de plafond global de durée ou de tentatives. Refus
d'authentification, configuration invalide et erreur critique arrêtent le
programme. Une session est active par commande `run` ; `serve` permet une
session/superviseur par caméra, en parallèle. L'annulation ferme les connexions
Netvue et RTSP.

En cas d'arrêt, l'API et les journaux distinguent protocole, trame média,
traitement H.264, connexion et publication, avec une étape et une raison
prédéfinies lorsque disponibles. Les erreurs récupérables produisent un
avertissement avant chaque tentative avec son délai ; l'état HA reste
`supervising` pendant ces reprises. Les erreurs de protocole ou de trame
restent fatales et nécessitent une reprise explicite : ne pas les ignorer
ou les réessayer indéfiniment sans preuve de leur caractère transitoire.
Les diagnostics ne contiennent ni données brutes ni erreur système reçue ;
l'adresse IPv4 de la caméra figure cependant dans les journaux.
Une réponse RTSP 503 à ANNOUNCE, SETUP ou RECORD est considérée temporaire
et suit le même backoff ; un refus 403 reste fatal.

L'URL de sortie exige hôte loopback, port explicite et chemin ; RTSP utilise
TCP. Le pont n'écrit ni vidéo ni clé sur disque et ne journalise pas la clé
reçue à la découverte. Le login Netvue observé n'est pas chiffré : la clé
locale envoyée peut être vue sur le LAN ; sa réutilisabilité est inconnue.
Utiliser un réseau de confiance (détails : [protocole](./netvue-protocol.md)).

## Talkback expérimental

`talk` ouvre une session A/V bornée et envoie un fichier AAC-LC/ADTS :

```text
netvue-bridge talk --source 192.168.10.10 --camera 192.168.10.20 \
  --rtsp rtsp://127.0.0.1:8554/netvue_test --duration 30s \
  --audio clip.aac --delay 5s
```

Le fichier doit correspondre à la fréquence et aux canaux annoncés par la
caméra et durer au plus 30 s. La durée de session doit dépasser la durée du
fichier et le délai. L'émission démarre après publication vidéo ; la réception
se poursuit ensuite jusqu'à la fin de la session. Cette commande ouvre sa
propre connexion : éviter une session concurrente, qui peut perturber un flux
existant. La lecture effective par le haut-parleur n'est pas encore confirmée.
Ce mode ne fournit pas de microphone Home Assistant.

## API et fonctionnement HA

`serve` écoute `127.0.0.1:8765` et publie vers MediaMTX
`127.0.0.1:8554`. L'API JSON fournit :

- `GET /api/v1/status` et `GET /api/v1/cameras/{host}`
- `POST /api/v1/discovery` : une diffusion UDP sur le sous-réseau de
  l'interface `--source`, puis `{ "cameras": [{ "host": "..." }] }`
- `POST /api/v1/cameras` avec `{host, stream_name}`
- `POST /api/v1/cameras/{host}/start` ou `/stop`
- `DELETE /api/v1/cameras/{host}`

L'ajout est idempotent, les noms de flux uniques et l'URL RTSP renvoyée par
le gestionnaire. L'API n'a pas d'authentification distincte ; tout processus
du réseau hôte peut l'appeler. Ne pas publier l'API ni RTSP. HA doit partager
le réseau host du nœud. Sa configuration est persistante, la liste Go
volatile : HA réajoute une entrée absente après redémarrage du pont, mais ne
relance pas implicitement une entrée déjà présente, même arrêtée ou en échec.
La suppression HA arrête et retire la session. Le guide
[d'intégration HA](../custom_components/netvue_local/README.md) décrit les
entités et erreurs de configuration.

La découverte est déclenchée explicitement depuis HA ; elle attend deux
secondes, filtre les réponses compatibles avec le protocole Go et ne renvoie
que les adresses. Une liste vide n'indique pas nécessairement l'absence de
caméras : le broadcast peut être filtré ou le modèle non pris en charge.
Une seule recherche est exécutée à la fois. La saisie manuelle reste possible.
La découverte n'ouvre pas de session vidéo ; la sélection et la configuration
de la caméra déclenchent ensuite le fonctionnement normal du pont.

## Limites vidéo

- Démarrage après SPS, PPS et IDR. Le SPS est vérifié avant et après
  recadrage avec calculs bornés. En cas de changement de paramètres, la
  publication reste ouverte et la vidéo attend l'IDR suivant avec les
  nouveaux SPS/PPS ; l'audio continue. Plusieurs SPS/PPS contradictoires dans
  une même unité d'accès sont rejetés. Ce comportement a été validé avec
  MediaMTX et FFmpeg synthétiquement et lors d'un changement réel 640×360
  vers 1920×1080.
- Annex B, SPS et limites de résolution sont vérifiés ; chaque image n'est
  pas décodée en production. B-frames et fragments répartis sur plusieurs
  paquets restent non vérifiés.
- L'horloge RTP accepte le wrap naturel 32 bits. Timestamp dupliqué, retour
  arrière ou saut supérieur à 10 s : incrément de 1 ms (90 ticks), sans
  inférer la cadence. Les données brutes de l'anomalie historique manquent ;
  cela garantit l'ordre de sortie, pas la continuité temporelle réelle.
  Un DTS non monotone a été signalé par FFmpeg lors du changement de
  résolution réel ; son origine reste à établir.
- Caméra 1, autonomie sans cloud, stabilité prolongée et déploiement réel ne
  sont pas établis par les tests synthétiques.
