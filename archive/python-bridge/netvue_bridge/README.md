# Passerelle Netvue LAN vers RTSP

Prototype pour la branche LAN classique analysée dans l'APK. La publication ne transcode pas : PyAV décode au démarrage pour vérifier le flux et ses dimensions, puis remuxe les paquets H.264 vers MediaMTX. L'audio n'est pas publié. Toute session caméra passe par le superviseur v4 ; le module `stream` ne fournit que le travailleur interne.

## Sécurité et limites

- La configuration [mediamtx.local.yml](./mediamtx.local.yml) écoute uniquement sur `127.0.0.1:8554`, en RTSP/TCP ; lecture et publication ne demandent aucun identifiant. RTMP, HLS, WebRTC, SRT et les interfaces de contrôle sont désactivés. Tout utilisateur local pouvant accéder à cette boucle locale peut lire ou publier sur le chemin configuré : ce n'est pas une isolation entre utilisateurs. La stack ne publie aucun port RTSP sur une interface réseau.
- La connexion caméra n'utilise pas TLS. Les données de découverte et le flux peuvent être visibles sur le chemin réseau ; ne pas partager de paquets bruts ou de journaux sensibles.
- Chaque connexion authentifiée au protocole LAN Netvue consomme une tentative enregistrée dans l'état persistant v4. L'état conserve le total et un backoff progressif, sans quota journalier/hebdomadaire. Tout essai réel exige une autorisation. Un refus d'authentification LAN reste bloquant ; les erreurs réseau et vidéo sont réessayées avec backoff.
- Le prototype attend une unité d'accès H.264 Annex B par paquet vidéo et démarre après SPS, PPS et IDR. Après un changement SPS/PPS, il attend une IDR et vérifie le décodage ; les tampons et l'attente sont bornés. Une modification de résolution a été observée sur la caméra 2 en [phase 7](../findings/phase7-real-validation.md). Un retour du PTS à zéro sur IDR est uniquement accepté pendant une reconfiguration après une seconde confirmation LIVE ; la continuité à cette frontière est estimée à partir du temps d'arrivée. La correction expérimentale de certains PTS identiques, décrite en [phase 12 bis](../findings/phase12b-timestamp-recovery.md), utilise une cadence connue et une borne temporelle ultérieure ; les cas ambigus échouent explicitement et cette correction n'a pas été validée sur une caméra réelle. Les autres horodatages non monotones, les B-frames détectées et les fragmentations différentes restent non pris en charge.
- Les données en mémoire peuvent toutefois être exposées au swap ou aux dumps système. Le fonctionnement prolongé et les reconnexions avec une caméra réelle restent à valider.

## Utilisation future, après nouvelle autorisation

Prérequis : Python avec [PyAV](./requirements.txt), MediaMTX compatible, configuration locale disponible, port 8554 libre, interface et caméra explicitement autorisées. Le serveur doit être arrêté manuellement après usage ; aucune unité de service ou configuration Home Assistant n'est installée. Les adresses sont fournies par le propriétaire et remplacées ci-dessous par des variables.

```sh
# Terminal 1 : démarre uniquement le serveur RTSP local.
tools/mediamtx-1.21.4/mediamtx netvue_bridge/mediamtx.local.yml

# Initialiser une fois le budget de la caméra dans le répertoire persistant.
tools/venv/bin/python -B -m netvue_bridge.continuous init \
  --source "$SOURCE_LAN" --target "$CAMERA_2" --state-dir /state

# Le test borné utilise ensuite le même verrou et le même budget v4.
tools/venv/bin/python -B -m netvue_bridge.continuous test \
  --source "$SOURCE_LAN" --target "$CAMERA_2" --state-dir /state \
  --max-duration 10 --max-attempts 1
```

Le superviseur commun se lance avec `python -m netvue_bridge.continuous run`.
Le mode d'essai borné exige à la fois `test --max-duration N --max-attempts M`
et partage le même budget, le même verrou et les mêmes contrôles. Un état
bloqué ou `in_progress` ne peut pas être contourné par un autre répertoire.
Ctrl+C ferme les connexions ; une interruption ambiguë reste `in_progress`.
La clé d'authentification LAN reste en mémoire et n'est ni transmise en argument ni écrite dans un fichier.

Le diagnostic `check_rtsp` exige le même `--state-dir` déjà initialisé et lance le mode test du superviseur : il ne crée plus de session directe. Il peut ajouter `--bridge-timeout` après une durée de session inférieure ; ce garde-fou arrête de force le pont si sa limite est dépassée. Il collecte les sorties disponibles après l'arrêt des lecteurs et rapporte leurs codes de retour ; une sortie FFprobe tronquée ne fournit que les paquets JSON complets. Avec `--home-assistant-check`, la commande `timeout` doit aussi être disponible dans le conteneur pour borner les lecteurs temporaires. Une interruption forcée peut empêcher le pont d'imprimer ses dernières métriques.

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
