# Phase 4 — passerelle RTSP locale

## Conclusion

**OBSERVÉ :** un essai sur la caméra 2 a obtenu le résultat de login `0` et publié sur `rtsp://127.0.0.1:8554/netvue2`. FFprobe a identifié un flux **H.264 640 × 360**, et FFmpeg a décodé **22 images** en local, sans enregistrer ni transmettre les images. Les composants ont été arrêtés après le test ; le port loopback a ensuite pu être lié de nouveau.

**Limites :** la connexion n'a duré que dix secondes au maximum. Home Assistant n'a pas encore été configuré ni testé ; l'URL ne fonctionne pas lorsque les processus de test sont arrêtés. Une tentative de login a été utilisée dans cette phase : **trois sur trois** au total depuis la phase 3. Pas de nouvel essai authentifié avant nouvelle autorisation.

## Architecture et preuves

Caméra 2 → découverte UDP et login TCP existants → sélection LIVE → extraction des unités H.264 → PyAV/FFmpeg remuxe sans réencodage → MediaMTX en boucle locale RTSP/TCP → lecteur local ou, à terme, Home Assistant en réseau Docker host.

- [stream.py](../netvue_bridge/stream.py) : confirmation explicite du login et de LIVE, détection d'interruption vidéo, fermeture et reconnexion limitée. L'interface utilise une tentative par défaut ; `--max-attempts` est un plafond **par exécution**, pas un compteur global. Un refus ou une interruption pendant le login ne déclenche pas de nouvelle tentative.
- [rtsp.py](../netvue_bridge/rtsp.py) : inspection Annex B, conservation SPS/PPS, attente d'une IDR, décodage initial pour vérifier dimensions et absence de réordonnancement B détecté, puis remuxage H.264. La préparation initiale est limitée à 4 Mio et 128 unités ; les paquets réseau sont limités à moins de 1 Mio par [protocol.py](../netvue_bridge/protocol.py). Aucune accumulation de vidéo continue n'est prévue.
- Horodatages : le code Android [NVVideoFFmpegCLTConvertor.java](./decompiled/sources/com/netviewtech/client/media/mux/NVVideoFFmpegCLTConvertor.java#L28) calcule une fréquence avec un facteur 1000 ; [NVTSimpleChannelParamParser.java](./decompiled/sources/com/netviewtech/client/media/mux/NVTSimpleChannelParamParser.java#L76) divise le PTS par 1000. Ces indices et les mesures de phase 3 motivent une interprétation **en millisecondes**, corroborée par la lecture RTSP courte mais non validée pour tous les firmwares. Le pont normalise les PTS, gère le rebouclage 32 bits et refuse les doublons, retours en arrière ou sauts supérieurs à dix secondes. Il pose DTS = PTS et n'accepte pas les B-frames détectées.
- [mediamtx.local.yml](../netvue_bridge/mediamtx.local.yml) : seul RTSP/TCP écoute sur boucle locale ; autres serveurs et API désactivés. L'accès n'est pas authentifié entre utilisateurs locaux : tout utilisateur local capable de joindre ce port peut voir ou publier ce flux pendant l'exécution. La liaison LAN Netvue n'a pas de TLS.
- [check_rtsp.py](../netvue_bridge/check_rtsp.py) lance temporairement MediaMTX, une seule session caméra, FFprobe et FFmpeg, vérifie les métadonnées et le nombre d'images décodées, échantillonne les ressources et ferme les processus. Aucune image n'est affichée ni enregistrée. Les buffers en mémoire restent soumis aux mécanismes de swap ou de dump du système.
- Le client de phase 3 et ses tests restent disponibles ; les nouvelles consignes d'utilisation figurent dans [README.md](../netvue_bridge/README.md).

## Tests hors ligne

Sur Debian, PyAV 16.1.0, [MediaMTX v1.21.4](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.4) et une compilation portable [BtbN FFmpeg](https://github.com/BtbN/FFmpeg-Builds) n8.1.3 ont été utilisés dans l'environnement de développement isolé. Les archives ont été téléchargées depuis leurs dépôts de publication respectifs et comparées à leurs fichiers SHA-256 de publication ; aucune installation système ni `sudo`.

- Test vidéo synthétique en boucle locale : **55 unités publiées**, **6 IDR**, FFprobe a reçu **20 paquets horodatés** avec intervalles de 100 ms et au moins une image clé ; FFmpeg a décodé le flux vers un puits `null`. L'écriture et la fermeture du serveur et des lecteurs ont été vérifiées.
- Test complet avec caméra **simulée** : découverte, un login fictif, sélection LIVE, **90 unités**, **9 IDR**, FFprobe H.264 64 × 64 et **23 images décodées** par FFmpeg. Les connexions et le serveur se sont arrêtés.
- Les tests unitaires couvrent notamment les restrictions loopback, les limites d'horodatage et le rebouclage 32 bits, l'attente de SPS/PPS et d'une IDR, les limites du tampon initial, le refus de média avant confirmation LIVE, une reconnexion simulée et l'absence de réessai après refus ou interruption d'authentification.
- Sur Windows, 12 tests sans dépendances ont réussi et les 2 tests multimédia ont été ignorés faute des exécutables/PyAV locaux. Sur Debian, les **14 tests** ont réussi avec les outils portables. Ces tests synthétiques ne démontrent pas la stabilité prolongée d'une caméra réelle.

Lors du premier test synthétique, FFprobe ne fournissait pas de PTS pour le premier paquet RTSP, mais les suivants étaient horodatés ; le contrôle a été corrigé pour tenir compte de cette initialisation sans masquer les valeurs manquantes suivantes. Un avertissement de fichier de diagnostic non fermé a aussi été corrigé avant le test réel.

## Test réel borné et performances

Après validation synthétique, [check_rtsp.py](../netvue_bridge/check_rtsp.py) a exécuté exactement **une** découverte et **un** login sur la caméra 2, suivi d'une sélection LIVE et d'une session limitée à dix secondes. Aucun accès à la caméra 1 ni à un compte n'a eu lieu.

Après cet essai, la règle de reconnexion a été renforcée pour ne pas réessayer après une interruption pendant l'authentification ; cette correction finale a été validée hors ligne uniquement, sans nouvelle connexion réelle.

| Mesure | Résultat observé |
|---|---:|
| Login / sélection LIVE | `0` / confirmation requise par le pont |
| Unités publiées / IDR détectées | 81 / 6 |
| FFprobe : paquets / images clés | 26 / 2 |
| FFprobe : paquets avec PTS | 25 ; PTS disponibles strictement croissants |
| FFprobe : codec et dimensions | H.264, 640 × 360 |
| FFmpeg : images décodées vers `null` | 22 |

Mesures `/proc`, échantillonnées approximativement toutes les 100 ms pendant la fenêtre du test ; le pourcentage est le temps CPU échantillonné divisé par la fenêtre de mesure depuis le lancement du processus, pas une mesure de charge soutenue. Le RSS maximal est un pic échantillonné par processus ; les mémoires partagées interdisent de les additionner comme mémoire physique totale.

| Processus | Temps CPU échantillonné | CPU moyen sur fenêtre | Pic RSS échantillonné |
|---|---:|---:|---:|
| MediaMTX | 0,04 s | 0,4 % | 45 644 Kio |
| Pont Python/PyAV | 0,12 s | 1,2 % | 49 812 Kio |
| FFprobe, diagnostic seulement | 0,02 s | 0,2 % | 31 708 Kio |
| FFmpeg, décodage de contrôle seulement | 0,06 s | 0,6 % | 43 320 Kio |

Ces chiffres ne prédisent ni la consommation en continu ni celle de Home Assistant ; FFprobe et FFmpeg sont ici des lecteurs de contrôle, pas des composants nécessaires au remuxage.

## Commandes principales et risques

Les commandes suivantes ont été exécutées dans le répertoire isolé Debian `/tmp/netvue-phase3.YUfIYT`, via `ssh home`, sauf mention contraire. Le répertoire est temporaire et peut être supprimé par le système. Les variables de la commande réelle remplacent les adresses privées fournies par le propriétaire.

1. `python3 --version`, `uname -m`, `command -v ...` et `tools/venv/bin/python -m pip show av` : inventaire en lecture locale, sans caméra ; nécessite SSH et Python.
2. `gh release view` et `gh release download` depuis les dépôts MediaMTX et BtbN, puis `Get-FileHash -Algorithm SHA256` et comparaison aux sommes publiées : téléchargements publics sur le PC dans `tools/`, sans accès caméra ; les archives ont ensuite été transférées par SSH et extraites sous `tools/` du répertoire isolé Debian.
3. `python -B -m unittest discover -s tests -p 'test*bridge.py' -v` sur Windows : tests hors ligne, pas de contact caméra.
4. `NETVUE_MEDIAMTX=tools/mediamtx-1.21.4/mediamtx NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe tools/venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tests -p test_rtsp_bridge.py -v` : tests synthétiques Debian, création temporaire d'écouteurs loopback uniquement ; les ports utilisés doivent être libres.
5. `tools/venv/bin/python -B -m netvue_bridge.check_rtsp --source SOURCE_LAN --target CAMERA_2 --mediamtx tools/mediamtx-1.21.4/mediamtx --ffprobe tools/ffmpeg-btbn/ffprobe --ffmpeg tools/ffmpeg-btbn/ffmpeg` : test réel exécuté **une seule fois**, sur autorisation ; transmet un login et une demande LIVE, et consomme la dernière tentative. Ne pas rejouer sans nouvelle autorisation.
6. Vérification finale par liaison temporaire sur `127.0.0.1:8554` : le port était libre, sans contact caméra. Aucun service persistant ni modification de pare-feu, tunnel, conteneur ou configuration Home Assistant.

## Préparation Home Assistant

Le serveur étant en réseau Docker host, une future instance Home Assistant sur ce même hôte devrait pouvoir joindre l'URL loopback du pont lorsqu'il fonctionne. Dans **Generic Camera**, préparer comme *Stream Source* `rtsp://127.0.0.1:8554/netvue2`, transport RTSP TCP ; l'intégration [Stream](https://www.home-assistant.io/integrations/stream/) doit être disponible. La documentation officielle [Generic Camera](https://www.home-assistant.io/integrations/generic/) prévoit cette source et précise que Home Assistant peut servir les images à distance : contrôler les autorisations et accès externes avant activation. Cette configuration est une proposition, **non appliquée et non testée dans Home Assistant**.

## Inconnues et suite

- Une unité vidéo Netvue est supposée correspondre à une unité d'accès H.264 Annex B ; une fragmentation différente, un changement SPS/PPS et les B-frames détectées sont rejetés, pas réparés. Le FPS nominal de sortie est 10 ; les PTS réellement transmis proviennent de la caméra.
- Reconnexion et relance du lecteur après interruption sont testées synthétiquement seulement ; perte réseau, dérive d'horloge, stabilité longue durée, son, caméra 1 et fonctionnement hors cloud restent non vérifiés.
- Aucun flux Internet n'est ouvert par la configuration testée. Loopback ne protège pas contre les autres utilisateurs locaux ; un déploiement partagé exige un mécanisme d'isolation/authentification adapté.
- **Prochaine action sûre :** obtenir une nouvelle autorisation et un nouveau budget de connexions, puis effectuer un essai temporaire plus long et vérifier l'affichage Home Assistant, sans installer de service permanent.
