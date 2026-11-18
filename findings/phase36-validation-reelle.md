# Phase 36 — Validation réelle courte

## Objectif et précautions

Valider la chaîne Go → caméra → H.264 → RTSP → décodeur sur Debian, sans
modifier ni redéployer les stacks de production. L'autorisation permettait
jusqu'à dix connexions caméra de 60 s au plus.

Avant le test, le pont de production et MediaMTX étaient actifs depuis environ
deux heures. L'API du pont déclarait déjà la caméra `192.168.129.2` en état
`supervising`, et `ss` montrait une connexion TCP établie vers son endpoint
Netvue. Pour éviter une session concurrente, aucune nouvelle connexion à la
caméra n'a été ouverte et la session existante n'a pas été interrompue.

Les services, la configuration, les paramètres de la caméra et le firmware
n'ont pas été modifiés. Le lecteur FFmpeg a été lancé dans un conteneur
éphémère distinct, en lecture seule, sans capacités Linux et limité à 0,5 CPU
et 256 Mio ; `--rm` l'a supprimé à la fin.

## Résultats

- **Découverte, authentification et LIVE — preuve indirecte :** la session
  existante avait une socket TCP active vers la caméra. Le code Go ne publie
  qu'après découverte, authentification, confirmation LIVE et réception H.264.
  La vidéo RTSP décodée confirme donc que ce chemin a abouti dans le binaire
  déployé ; les codes de réponse de chaque étape n'ont pas été exposés par
  l'état HTTP et n'ont pas été rejoués dans cette phase.
- **H.264 / RTSP — confirmé sur le service actif :** FFmpeg, via RTSP/TCP sur
  `127.0.0.1:8554`, a décodé 30 s de média et 238 images. La progression a
  atteint `out_time=00:00:30.000`, avec `drop_frames=0`, `dup_frames=0`,
  `progress=end` et code de sortie 0. Aucun message d'erreur FFmpeg n'a été
  émis. Ces compteurs ne remplacent pas une analyse détaillée des écarts PTS.
- **Interruptions / erreurs / reprises :** la progression FFmpeg a continué
  jusqu'à 30 s sans erreur. Aucun journal du pont n'a été retourné sur les
  deux heures d'historique consultées, ni sur les dix dernières minutes ; la
  socket caméra était toujours établie et l'API déclarait encore la session
  `supervising` après le test. Aucune reconnexion n'a donc été observée, mais
  l'absence de journaux et de métriques dédiées ne prouve pas l'absence de
  tout événement transitoire.
- **Ressources — deux instantanés Docker, non des pics :**

  | Service | Avant : CPU ; mémoire / limite ; PIDs | Après : CPU ; mémoire / limite ; PIDs |
  |---|---|---|
  | Pont Go | 1,02 % ; 8,98 / 512 Mio ; 8 | 0,38 % ; 9,66 / 512 Mio ; 8 |
  | MediaMTX | 0,30 % ; 27,92 / 128 Mio ; 8 | 0,19 % ; 27,74 / 128 Mio ; 8 |

- **Home Assistant :** le conteneur était actif et son API locale répondait
  `401` sans authentification, comme attendu. L'accessibilité de la route RTSP
  depuis le même hôte est vérifiée ; l'état d'une entité caméra HA n'a pas été
  consulté, faute d'accès API authentifié.
- **Nettoyage :** le conteneur FFmpeg temporaire s'est terminé avec succès et
  `--rm` l'a supprimé. Le pont, MediaMTX et la session caméra sont restés actifs.

## Code et tests

Aucun défaut n'a été confirmé dans ce test ; aucun changement de code ni test
de non-régression supplémentaire n'était justifié. `go test -count=1 ./...`
a réussi sur le dépôt local, sans accès caméra. Aucun test de stabilité de
24 h n'a été lancé.

Le service déployé utilise l'image
`ghcr.io/mtx26/netvue-bridge-go:latest@sha256:e0a2b107b544bbd4744d010de18758ac0132b6159ce4b4684430b5ff98658889`.
Le commit local observé est `c0cb745ecd1e769b9f474a001747c470af475ed0`.
La révision source n'est pas intégrée aux métadonnées de cette image ; la
validation porte donc sur le chemin réellement déployé, sans prouver que son
code est identique au worktree local.

## État final et limites

La publication H.264/RTSP réelle et son décodage indépendant sont confirmés
pendant une fenêtre courte sur la session déjà active. Pour éviter de
perturber cette session, il ne s'agit pas d'un lancement isolé du binaire local
contre la caméra : le conteneur temporaire a lu le flux de production, sans
modifier sa stack. La provenance du binaire, les codes détaillés du login et
de LIVE, la continuité précise des PTS, l'état de l'entité HA, les reprises
après panne et la stabilité 24 h restent non vérifiés.
