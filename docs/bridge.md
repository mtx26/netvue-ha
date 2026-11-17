# Netvue Bridge en Go

Le programme Go gère plusieurs caméras dans une seule instance ; l'intégration
Python `netvue_local` reste l'interface Home Assistant. Pour préparer le
déploiement, voir le [guide Docker/Portainer](../deployment/go-portainer.md).
Le guide décrit l'image GHCR Go, l'installation Portainer et l'option d'un
digest immuable pour Portainer/Swarm.

## Prérequis

- Go 1.26.0 ou plus récent pour compiler le module (les dépendances RTSP
  utilisées exigent cette version).
- MediaMTX local pour recevoir les publications RTSP.
- Les tests unitaires n'ont pas besoin de caméra, de compte ou de serveur RTSP.
- Le test RTSP de bout en bout utilise les chemins fournis par les variables
  `NETVUE_TEST_MEDIAMTX` et `NETVUE_TEST_FFMPEG`. Il crée une vidéo synthétique,
  démarre un MediaMTX temporaire sur loopback et la lit avec FFmpeg.

## Compiler et tester

Depuis la racine du dépôt :

```text
go test ./...
go test -race ./...
go vet ./...
go build ./cmd/netvue-bridge
```

Le test de publication/décodage local peut être activé ainsi dans PowerShell,
en adaptant les chemins des binaires locaux :

```powershell
$env:NETVUE_TEST_MEDIAMTX = "C:\tools\mediamtx.exe"
$env:NETVUE_TEST_FFMPEG = "C:\tools\ffmpeg.exe"
go test -run TestMediaMTXPublicationDecodesWithFFmpeg -v ./internal/netvue
```

Les autres tests de transport utilisent des réponses fabriquées ou un pair de
boucle locale. Ils ne contactent pas les caméras.

## Commandes

`test` effectue une session bornée ; `run` supervise une session jusqu'à l'arrêt
du programme. `serve` démarre un gestionnaire HTTP local et exécute une session
supervisée indépendante par caméra ajoutée par Home Assistant. Toutes les
commandes réutilisent le même moteur de session. L'ajout d'une caméra lance
réellement sa découverte LAN, son login et sa demande de flux ; à utiliser
uniquement après autorisation de contact.

```text
netvue-bridge test \
  --source 192.168.10.10 \
  --camera 192.168.10.20 \
  --rtsp rtsp://127.0.0.1:8554/netvue_test \
  --duration 15s
```

```text
netvue-bridge run \
  --source 192.168.10.10 \
  --camera 192.168.10.20 \
  --rtsp rtsp://127.0.0.1:8554/netvue_test
```

```text
netvue-bridge serve --source 192.168.10.10
```

Les valeurs sont des exemples. `test` exige une durée explicite strictement
positive, sans plafond maximal ; la session s'arrête à son échéance ou à
l'annulation du contexte. `run` continue jusqu'à Ctrl+C ou SIGTERM ; il relance
les erreurs récupérables séquentiellement après 2, 4, 8, 16 puis 30 secondes
au maximum.
Après cinq minutes de fonctionnement d'une session, le délai repart à deux
secondes. Les refus d'authentification, les configurations invalides et les
erreurs critiques arrêtent le programme. Une seule session caméra est active
à la fois avec la commande `run`. Avec `serve`, chaque entrée HA possède sa
propre session et son propre superviseur, en parallèle des autres caméras.
Le pont n'écrit pas de vidéo ni de clé sur disque et ne journalise pas la clé
obtenue pendant la découverte.

L'URL RTSP de sortie doit avoir un hôte loopback, un port explicite et un
chemin ; la publication force RTSP sur TCP.

Pour `test`, la durée couvre aussi le préflight RTSP local, la découverte,
l'authentification et l'établissement de la publication. Le démarrage vidéo a
une échéance absolue de cinq secondes, qui n'est pas repoussée par les
commandes seules. Pour `run`, le contexte reste actif sans échéance de session.
L'annulation ferme les connexions Netvue et RTSP.

Le protocole Netvue observé ne chiffre pas la découverte ni le login : la clé
locale envoyée au login peut être visible à un observateur du LAN. Son
éventuelle réutilisabilité n'est pas établie. N'utiliser le pont que sur un
réseau local de confiance ; cette passerelle ne fournit pas de protection
cryptographique au transport caméra.

## Limites actuelles

- La liste des caméras du gestionnaire est volatile ; Home Assistant doit
  conserver ses config entries et les réconcilier après un redémarrage du pont.
- Le gestionnaire utilise MediaMTX sur `127.0.0.1:8554` et l'API HA sur
  `127.0.0.1:8765` ; ces adresses font partie du contrat de déploiement.
- Le contrôle HTTP et les flux RTSP restent accessibles dans le réseau local
  de l'hôte ; ne publiez ni le port de contrôle ni RTSP.
- Le démarrage attend SPS, PPS et IDR ; le SPS est contrôlé avant et après
  recadrage avec des calculs bornés. Un changement de paramètres ferme puis
  réouvre une publication RTSP à l'IDR suivant. Plusieurs changements
  successifs de paramètres sont acceptés ; plusieurs valeurs SPS ou PPS
  contradictoires dans une même unité d'accès sont rejetées.
- Le programme vérifie Annex B, SPS et limites de résolution, mais ne décode
  pas chaque image en production. Les B-frames et fragments distribués sur
  plusieurs paquets restent non vérifiés. L'horloge RTP accepte le wrap
  naturel 32 bits ; en cas de timestamp dupliqué, de retour arrière ou de saut
  supérieur à dix secondes, elle avance de 1 ms (90 ticks RTP) au lieu de
  deviner une cadence. Les données brutes de l'anomalie historique manquent :
  cette règle garantit l'ordre de sortie, pas la continuité temporelle réelle.
- L'intégration HA demande une adresse IPv4 manuellement et n'implémente pas le
  protocole Netvue. Seul Go effectue la découverte unicast au démarrage d'une
  session.
- La caméra 1, l'autonomie sans cloud, la stabilité prolongée et le déploiement
  réel ne sont pas établis par les tests synthétiques.

## Gestion multi-caméra

`serve` écoute uniquement sur `127.0.0.1:8765` et publie vers le MediaMTX local
sur `127.0.0.1:8554`. Son API JSON standard permet `GET /api/v1/status`,
`GET /api/v1/cameras/{host}`, `POST /api/v1/cameras` avec `{host, stream_name}`,
`POST /api/v1/cameras/{host}/start`, `POST /api/v1/cameras/{host}/stop` et
`DELETE /api/v1/cameras/{host}`. L'ajout est idempotent et l'URL RTSP est
renvoyée par le gestionnaire. Les noms de flux sont uniques.

La liste Go est uniquement runtime et n'est pas persistée : les config entries
Home Assistant sont la source de vérité. À son prochain refresh, HA réajoute
une entrée absente après un redémarrage du pont ; une entrée déjà présente,
y compris arrêtée ou en échec, n'est pas relancée implicitement. La suppression
d'une entrée HA arrête et retire sa session Go.

L'API n'ajoute pas d'authentification distincte : elle écoute seulement sur
loopback. Ne publiez ni le port 8765 ni le flux RTSP. Des processus locaux
pouvant joindre loopback peuvent appeler l'API ; Home Assistant et le pont
doivent partager le réseau host du nœud.

Voir le [plan Go](./go-plan.md) et la
[synthèse du protocole](./netvue-protocol.md).
