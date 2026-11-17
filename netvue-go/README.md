# Netvue Bridge en Go

Cette implémentation est un nouveau programme séparé de la passerelle Python.
Le Python et l'intégration Home Assistant existants ne sont pas modifiés.

## Prérequis

- Go 1.26.0 ou plus récent pour compiler le module (les dépendances RTSP
  utilisées exigent cette version).
- MediaMTX local pour recevoir les publications RTSP.
- Les tests unitaires n'ont pas besoin de caméra, de compte ou de serveur RTSP.
- Le test RTSP de bout en bout utilise les chemins fournis par les variables
  `NETVUE_TEST_MEDIAMTX` et `NETVUE_TEST_FFMPEG`. Il crée une vidéo synthétique,
  démarre un MediaMTX temporaire sur loopback et la lit avec FFmpeg.

## Compiler et tester

Depuis `netvue-go/` :

```text
go test ./...
go vet ./...
go build -o netvue-bridge ./cmd/netvue-bridge
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

## Session unique

La commande `session` envoie effectivement une requête de découverte LAN,
ouvre une connexion au endpoint annoncé, effectue un login puis demande le
flux. Elle doit être utilisée uniquement avec une autorisation spécifique
pour l'appareil concerné. Elle ne contient pas de mode simulation présenté
comme un accès réel.

```text
netvue-bridge session \
  --source 192.168.10.10 \
  --camera 192.168.10.20 \
  --rtsp rtsp://127.0.0.1:8554/netvue_test \
  --duration 15s
```

Les valeurs sont des exemples. La durée est obligatoire, positive et limitée à
24 heures ; l'interruption clavier ferme la session. Le pont n'écrit pas de
vidéo ni de clé sur disque et ne journalise pas la clé obtenue pendant la
découverte. Les erreurs d'authentification arrêtent l'essai avant LIVE.
L'URL RTSP de sortie doit avoir un hôte loopback, un port explicite et un
chemin ; la publication force RTSP sur TCP.

La durée couvre aussi le préflight RTSP local, la découverte, l'authentification
et l'établissement de la publication. Le démarrage vidéo a une échéance absolue
de cinq secondes, qui n'est pas repoussée par les commandes seules ; l'annulation
du contexte ferme les connexions Netvue et RTSP.

Le protocole Netvue observé ne chiffre pas la découverte ni le login : la clé
locale envoyée au login peut être visible à un observateur du LAN. Son
éventuelle réutilisabilité n'est pas établie. N'utiliser le pont que sur un
réseau local de confiance ; cette passerelle ne fournit pas de protection
cryptographique au transport caméra.

## Limites actuelles

- Une caméra et une session bornée par processus ; pas de superviseur,
  reconnexion automatique, API de contrôle, configuration multi-caméra ou
  intégration Home Assistant Go.
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
- La caméra 1, l'autonomie sans cloud, la stabilité prolongée et toute
  compatibilité de déploiement ne sont pas établies par les tests synthétiques.

Voir le [plan Go](../docs/go-plan.md), la [synthèse du protocole](../docs/netvue-protocol.md)
et le [rapport de phase 23](../findings/phase23-go-reprise.md).
