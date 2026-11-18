# Phase 5 — validation RTSP réelle et Home Assistant

## Résultat

**OBSERVÉ :** les deux nouvelles tentatives autorisées ont été utilisées. Chacune a obtenu un code de login `0` et le message indiquant que le pont avait commencé à publier en RTSP local. Aucune n'a cependant terminé le test demandé de 30 secondes : la première s'est arrêtée sur une modification de paramètres H.264 ; la seconde, après une correction validée hors ligne, a signalé que les nouveaux paramètres étaient non pris en charge.

**Non vérifié :** le message de la seconde tentative regroupait trois causes possibles — image non décodée, B-frame détectée ou changement de résolution — sans dire laquelle s'est produite. Les résultats des lecteurs partiels n'ont pas été conservés par la version alors exécutée. Il est donc impossible de confirmer ici la continuité réelle sur 30 secondes, la lecture réelle depuis Home Assistant, la fréquence d'images ou la latence. L'essai de phase 4 avait néanmoins confirmé **10 secondes au plus**, du H.264 640 × 360 et 22 images décodées ([rapport phase 4](./phase4-rtsp-bridge.md)).

**Budget :** deux tentatives sur deux consommées en phase 5 ; aucune tentative supplémentaire n'a été effectuée. Aucun service permanent n'a été déployé. Les processus temporaires ont été arrêtés.

## Vérifications préalables

- [mediamtx.local.yml](../netvue_bridge/mediamtx.local.yml) : RTSP/TCP sur `127.0.0.1:8554`, chemin `netvue2` ; les autres protocoles et l'API sont désactivés. Le port était disponible avant les essais.
- Le conteneur Home Assistant détecté utilise effectivement le réseau Docker `host` ; `ffprobe` et `ffmpeg` y sont disponibles. Aucun fichier de configuration ni conteneur n'a été modifié.
- Dans le répertoire Debian isolé : PyAV 16.1.0, décodeur H.264 et encodeur synthétique `libx264` disponibles ; MediaMTX 1.21.4 et FFprobe/FFmpeg portables n8.1.3 disponibles.
- La liaison RTSP étant en boucle locale, elle n'est pas exposée directement sur le LAN ou Internet. Elle n'isole cependant pas les autres utilisateurs locaux ; le protocole caméra n'est pas chiffré par ce client.

## Essais réels

Les deux essais ont été lancés séparément sur la caméra 2 avec une durée totale maximale de 30 secondes, une seule authentification chacun et aucune reconnexion automatique. Les lecteurs ont été démarrés trois secondes après la publication pour tester une période initiale sans lecteur.

| Essai | Login | Résultat observé |
|---|---|---|
| 1 | `0` | Publication annoncée, puis arrêt : `H.264 parameters changed during the session`. |
| 2 | `0` | Publication annoncée, puis arrêt : `Changed H.264 parameters are unsupported`. |

Lors des deux essais, les processus de lecture FFprobe sur Debian, FFmpeg et FFprobe dans le conteneur Home Assistant ont été lancés après la période sans lecteur. **Leurs sorties partielles n'ont pas été rapportées avant l'arrêt du pont : leur lancement ne prouve pas qu'ils ont lu ou décodé le flux réel.** Aucun fichier vidéo ni image privée n'a été enregistré ou envoyé à un service externe.

Le RSS maximal échantillonné du pont était respectivement **50 244 Kio** et **51 964 Kio**, et celui de MediaMTX **45 332 Kio** et **45 436 Kio**. Les CPU moyens sur les fenêtres de test interrompues étaient d'environ **1,2 %** pour le pont et **0,6 % / 0,5 %** pour MediaMTX. Il s'agit d'échantillons `/proc` approximatifs, pas de mesures de charge en régime permanent ; le chiffre du processus `docker exec` ne mesure pas les ressources du processus FFprobe dans le conteneur.

## Diagnostic et corrections hors ligne

1. La première erreur provient du rejet systématique d'un SPS/PPS différent dans [rtsp.py](../netvue_bridge/rtsp.py). Un changement SPS a reproduit la même erreur avec une caméra synthétique. Le code MediaMTX officiel [format_updater.go](https://github.com/bluenviron/mediamtx/blob/v1.21.4/internal/stream/format_updater.go) met à jour SPS/PPS à partir des unités H.264 reçues.
2. Une première correction attend une image IDR, réinjecte SPS/PPS et vérifie le décodage avant de continuer. Un test synthétique de 30 secondes avec changement SPS a réussi : **290 unités publiées**, **1 mise à jour**, **201 paquets FFprobe**, et lecture FFprobe depuis le conteneur Home Assistant. Cette correction a été utilisée pour la seconde tentative réelle, qui a encore échoué avec un message trop général.
3. Après la seconde tentative, une correction supplémentaire accepte les changements de résolution décodables, essaye de vider le décodeur initial si nécessaire, et différencie l'absence d'image des B-frames non prises en charge. Elle n'a **pas** été testée sur la caméra. Le diagnostic a aussi été amélioré pour signaler les résultats partiels des lecteurs en cas d'échec.
4. Test synthétique final : changement SPS et résolution **64 × 64 → 128 × 64**, **290 unités publiées**, **29 IDR**, **1 mise à jour**, FFprobe **201 paquets**, **200 PTS**, écart maximal **0,1 s**, **10 images/s** selon les PTS ; FFmpeg a décodé **193 images**. Un FFprobe supplémentaire a observé les deux résolutions dans les métadonnées des images décodées. FFprobe dans le conteneur Home Assistant a reçu **201 paquets** sans erreur. Les dimensions de flux initiales annoncées par FFprobe restaient 64 × 64 ; les dimensions des images décodées ont été vérifiées séparément.
5. Après les corrections, **16 tests** hors ligne et synthétiques ont réussi sur Debian. Le port loopback était à nouveau libre et aucun processus MediaMTX ou pont de test correspondant n'était actif.

Ces tests synthétiques ne démontrent ni la cause exacte de l'échec réel ni le succès de la dernière correction sur la caméra. Le pont rejette toujours les B-frames détectées, les horodatages invalides et les fragmentations non prises en charge.

## Home Assistant et mesures restantes

L'accès depuis le conteneur à un flux RTSP **synthétique** en boucle locale a été observé. La documentation officielle [Generic Camera](https://www.home-assistant.io/integrations/generic/) accepte une source RTSP ; la configuration proposée est `rtsp://127.0.0.1:8554/netvue2`, transport TCP, avec l'intégration [Stream](https://www.home-assistant.io/integrations/stream/) disponible. **Aucune intégration n'a été créée ni validée dans l'interface Home Assistant.** L'accès de Home Assistant depuis l'extérieur peut également permettre l'affichage à distance : contrôler les autorisations avant activation.

La latence de bout en bout, le FPS réel sur 30 secondes, la stabilité prolongée, le comportement après reconnexion et les ressources en continu restent à mesurer. La publication sans réencodage est confirmée par le chemin logiciel ; le décodeur est utilisé ponctuellement pour valider les paramètres. Une modification de résolution est démontrée seulement avec des données synthétiques.

## Commandes principales

Prérequis communs : `ssh home`, répertoire isolé Debian `/tmp/netvue-phase3.YUfIYT`, outils portables et port loopback libre. `SOURCE_LAN` et `CAMERA_2` ci-dessous remplacent les adresses privées fournies par le propriétaire. Le répertoire sous `/tmp` peut être supprimé par le système.

1. `git status --short`, lecture des rapports et sources, `python3 --version`, `command -v docker`, vérification des binaires et de PyAV : inventaire sans contact caméra et sans modification de service.
2. Liaison temporaire d'une socket sur `127.0.0.1:8554` : contrôle de disponibilité, sans arrêter un processus existant. `docker ps`, `docker inspect` et `docker exec ... ffprobe -version` : inspection et exécution en lecture seule dans le conteneur existant, sans modifier sa configuration.
3. `python -B -m unittest discover -s tests -p 'test*bridge.py' -v` : tests hors ligne Windows ; deux tests multimédia ignorés en l'absence des outils locaux.
4. Depuis Debian : `NETVUE_MEDIAMTX=tools/mediamtx-1.21.4/mediamtx NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe NETVUE_TEST_DURATION=30 NETVUE_TEST_HA=1 NETVUE_TEST_RECONFIG=1 NETVUE_TEST_RESIZE=1 tools/venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tests -p test_rtsp_bridge.py -v`. Requiert les ports loopback de test libres ; utilise une caméra et des images synthétiques, sans contact matériel.
5. **Commande réelle exécutée deux fois**, une fois avant et une fois après correction hors ligne : `tools/venv/bin/python -B -m netvue_bridge.check_rtsp --source SOURCE_LAN --target CAMERA_2 --mediamtx tools/mediamtx-1.21.4/mediamtx --ffprobe tools/ffmpeg-btbn/ffprobe --ffmpeg tools/ffmpeg-btbn/ffmpeg --duration 30 --probe-duration 20 --reader-delay 3 --home-assistant-check`. Chaque exécution envoie une demande de login et peut recevoir de la vidéo privée. **Ne pas la relancer sans nouvelle autorisation.**
6. Vérification finale du port loopback après arrêt, et tests locaux après corrections : sans contact caméra.

## Bilan

- **Prouvé :** les deux logins de phase 5 ont réussi et la publication a démarré ; les deux essais ont été interrompus par la gestion des paramètres H.264. La chaîne synthétique, y compris l'accès du conteneur Home Assistant, fonctionne sur 30 secondes avec changement de résolution.
- **Hypothétique :** la dernière correction permettra de poursuivre le flux de cette caméra ; la cause précise du second arrêt reste inconnue.
- **Prochaine action sûre :** obtenir une nouvelle autorisation et un nouveau budget de connexions, puis refaire un essai borné avec les diagnostics différenciés et la collecte des résultats partiels, sans déployer de service permanent.
