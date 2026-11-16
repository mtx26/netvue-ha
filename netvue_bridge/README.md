# Passerelle Netvue LAN vers RTSP

Prototype pour la branche LAN classique analysée dans l'APK. La publication ne transcode pas : PyAV décode au démarrage pour vérifier le flux et ses dimensions, puis remuxe les paquets H.264 vers MediaMTX. L'audio n'est pas publié. Le diagnostic historique `python -m netvue_bridge.client` reste distinct.

## Sécurité et limites

- La configuration [mediamtx.local.yml](./mediamtx.local.yml) écoute uniquement sur `127.0.0.1:8554`, en RTSP/TCP. RTMP, HLS, WebRTC, SRT et les interfaces de contrôle sont désactivés. Tous les utilisateurs locaux pouvant accéder à cette boucle locale peuvent lire ou publier sur le chemin autorisé : ce n'est pas une isolation entre utilisateurs.
- La connexion caméra n'utilise pas TLS. Les données de découverte et le flux peuvent être visibles sur le chemin réseau ; ne pas partager de paquets bruts ou de journaux sensibles.
- Chaque connexion authentifiée consomme une tentative. Les deux tentatives de phase 5 et les deux tentatives de phase 7 ont été utilisées ; aucune tentative de phase 6 n'a été effectuée. Tout nouvel essai réel exige une autorisation et un budget applicables. `--max-attempts` est un plafond par exécution, pas un compteur global ; sa valeur par défaut est 1. Un refus ou une interruption pendant l'authentification ne déclenche pas de réessai automatique.
- Le prototype attend une unité d'accès H.264 Annex B par paquet vidéo et démarre après SPS, PPS et IDR. Après un changement SPS/PPS, il attend une IDR et vérifie le décodage ; les tampons et l'attente sont bornés. Une modification de résolution a été observée sur la caméra 2 en [phase 7](../findings/phase7-real-validation.md). Un retour du PTS à zéro sur IDR est uniquement accepté pendant une reconfiguration après une seconde confirmation LIVE ; la continuité à cette frontière est estimée à partir du temps d'arrivée. La correction expérimentale de certains PTS identiques, décrite en [phase 12 bis](../findings/phase12b-timestamp-recovery.md), utilise une cadence connue et une borne temporelle ultérieure ; les cas ambigus échouent explicitement et cette correction n'a pas été validée sur une caméra réelle. Les autres horodatages non monotones, les B-frames détectées et les fragmentations différentes restent non pris en charge.
- Les données en mémoire peuvent toutefois être exposées au swap ou aux dumps système. Le fonctionnement prolongé et les reconnexions avec une caméra réelle restent à valider.

## Utilisation future, après nouvelle autorisation

Prérequis : Python avec [PyAV](./requirements.txt), MediaMTX compatible, configuration locale disponible, port 8554 libre, interface et caméra explicitement autorisées. Le serveur doit être arrêté manuellement après usage ; aucune unité de service ou configuration Home Assistant n'est installée. Les adresses sont fournies par le propriétaire et remplacées ci-dessous par des variables.

```sh
# Terminal 1 : démarre uniquement le serveur RTSP local.
tools/mediamtx-1.21.4/mediamtx netvue_bridge/mediamtx.local.yml

# Terminal 2 : une tentative de login et 10 secondes au plus, sans écriture vidéo.
tools/venv/bin/python -B -m netvue_bridge.stream \
  --source "$SOURCE_LAN" --target "$CAMERA_2" --duration 10
```

Le mode `--continuous` et l'option `--max-attempts` existent pour un essai ultérieur explicitement autorisé ; une reconnexion peut consommer un login supplémentaire et interrompre les lecteurs RTSP. Un budget bloqué ne doit pas être contourné par un nouveau répertoire : sa réconciliation doit être étudiée séparément avant toute reprise. Ctrl+C ferme les connexions. Les valeurs d'authentification ne sont ni des arguments ni des fichiers.

Le diagnostic `check_rtsp` peut ajouter `--bridge-timeout` après une durée de session inférieure : ce garde-fou arrête de force le pont si sa limite est dépassée. Il collecte les sorties disponibles après l'arrêt des lecteurs et rapporte leurs codes de retour ; une sortie FFprobe tronquée ne fournit que les paquets JSON complets. Avec `--home-assistant-check`, la commande `timeout` doit aussi être disponible dans le conteneur pour borner les lecteurs temporaires. Une interruption forcée peut empêcher le pont d'imprimer ses dernières métriques.

## Tests

Sans caméra ni outils multimédia :

```sh
python -B -m unittest discover -s tests -p 'test*bridge.py' -v
```

Sur Debian, avec les outils portables et le venv du projet, la commande suivante lance aussi deux tests RTSP synthétiques sur la boucle locale et s'arrête seule :

```sh
NETVUE_MEDIAMTX=tools/mediamtx-1.21.4/mediamtx \
NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe \
tools/venv/bin/python -B -m unittest discover -s tests -p 'test*bridge.py' -v
```

Le test synthétique utilise temporairement les ports loopback 36868/UDP, 5555/TCP et 8554/TCP ; ils doivent être libres. Il n'envoie rien aux caméras. Voir les rapports [phase 4](../findings/phase4-rtsp-bridge.md), [phase 5](../findings/phase5-real-rtsp.md) et [phase 6](../findings/phase6-rtsp-stability.md) pour les résultats et les limites.
