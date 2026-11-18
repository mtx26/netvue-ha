# Phase 3 — connexion LAN et vidéo

## Résultat

**OBSERVÉ :** sur la caméra 2 uniquement, deux découvertes UDP V2 ont été suivies chacune d'une connexion TCP sur le port annoncé 5555, d'une unique demande de login et d'un résultat explicite `0`. Les deux sélections LIVE ont retourné `0`. Au second essai, un décodeur H.264 a produit **une image de 640 × 360 en mémoire sur Debian**. Aucun compte ni service externe n'a été sollicité pour ces sessions ; aucune image ou donnée vidéo n'a été envoyée à un service externe.

**Limite :** il s'agit d'un diagnostic borné, pas d'un pont continu : aucune source RTSP ou WebRTC n'est encore produite et Home Assistant n'est pas connecté. La caméra 1 n'a pas été sollicitée durant cette phase.

## Résultats des essais

| Mesure | Essai 1 | Essai 2 |
|---|---:|---:|
| Résultat explicite du login | 0 | 0 |
| Résultat explicite de sélection LIVE | 0 | 0 |
| Paquets média vidéo comptés | 82 | 83 |
| Drapeaux « image clé » | 6 | 6 |
| Paquets audio | 0 | 0 |
| Octets vidéo extraits | 231 997 | 261 861 |
| Premier / dernier horodatage brut | 0 / 9717 | 0 / 9828 |
| Test de décodage | Décodeur indisponible | Une image H.264, 640 × 360 |
| Capture mémoire tronquée | Non | Non |

Chaque réception LIVE était limitée à dix secondes ; les horodatages sont des valeurs brutes du protocole et leur unité n'a pas été vérifiée. Le compteur d'images clés reprend le drapeau du protocole, sans validation indépendante de chaque image clé. L'absence de paquets audio pendant ces deux tests ne prouve pas l'absence de capacité audio. L'essai 1 ne démontre pas à lui seul que sa vidéo était décodable ; l'essai 2 démontre que les données reçues comportaient au moins une image H.264 décodable.

Deux des trois tentatives de login autorisées ont été consommées ; aucune troisième tentative n'a été faite. Aucun échantillon ou image réelle n'a été écrit volontairement sur disque. Le système peut néanmoins utiliser le swap ou produire un dump mémoire : la seule utilisation de buffers mémoire n'exclut pas ces risques.

## Réalisation et preuves

- [discovery.py](../netvue_bridge/discovery.py) : une requête V2 UDP unicast, vérification de l'émetteur et de l'adresse annoncée, récupération de la clé en mémoire. Le port annoncé doit être 5555 dans [client.py](../netvue_bridge/client.py). L'égalité d'adresse n'est pas une authentification cryptographique de l'émetteur.
- [connection.py](../netvue_bridge/connection.py), [protocol.py](../netvue_bridge/protocol.py) : TCP avec fermeture et délais, enveloppe et messages bornés. Cette implémentation ne met pas de TLS sur le socket ; la clé et le flux peuvent être exposés à un observateur placé sur le chemin réseau. Ne pas publier les paquets ou les journaux bruts.
- [authentication.py](../netvue_bridge/authentication.py) : une demande de login avec les données locales de découverte et la valeur d'utilisateur par défaut du code officiel. Un ACK sans résultat entier explicite n'est pas accepté ; la vidéo n'est demandée qu'après le code `0`. Le succès constaté ne révèle pas comment le firmware valide les droits ni si une préparation cloud préalable est nécessaire.
- [video.py](../netvue_bridge/video.py) : sélection LIVE, séparation commandes/médias, extraction du drapeau et de l'horodatage, cumul vidéo borné à 8 Mio, décodage local en mémoire par PyAV ou FFmpeg. Il ne valide ni la continuité ni tous les horodatages ; il ne diffuse ni n'enregistre le flux.
- [test_netvue_bridge.py](../tests/test_netvue_bridge.py) : sept tests synthétiques hors ligne réussis sous Windows et Debian, notamment rejet d'un ACK sans résultat et échec explicite si LIVE ne réussit pas ou si aucune image ne peut être décodée. Le résultat d'un test synthétique ne prouve pas le comportement de la caméra.

Les éléments de code Android et les essais précédents restent décrits dans [phase1-report.md](./phase1-report.md), [lan-protocol-analysis.md](./lan-protocol-analysis.md), [active-network-tests.md](./active-network-tests.md) et [lan-login-feasibility.md](./lan-login-feasibility.md). Ces rapports historiques reflètent l'état des connaissances à leur date ; leur affirmation antérieure selon laquelle aucun login n'avait été envoyé est dépassée par les résultats ci-dessus.

## Commandes et environnement

Toutes les commandes réseau de caméra ci-dessous ont été lancées depuis Debian via `ssh home`, sans broadcast et sans caméra 1. Les adresses sont masquées ; `SOURCE_LAN` et `CAMERA_2` représentent les valeurs fournies par le propriétaire. Chaque invocation `--live` réalise **un nouveau login** et consomme une tentative : ne pas les rejouer sans tenir compte de la limite autorisée.

1. Vérifications sans contact caméra : `python3 --version`, `command -v ffmpeg`, `command -v ffprobe`, `python3 -c "import av"`, `uname -m`, `python3 -m venv --help`, `python3 -m pip --version`, `command -v pip3`, `command -v uv`, `command -v curl`. Python était disponible, mais ni FFmpeg ni PyAV ne l'étaient initialement.
2. `umask 077; mktemp -d /tmp/netvue-phase3.XXXXXX` a créé un environnement privé temporaire. Les sources et tests ont été transférés via une archive tar en mémoire et SSH. Répertoire obtenu : `/tmp/netvue-phase3.YUfIYT` ; il peut disparaître au nettoyage de `/tmp`.
3. Vérification hors ligne locale : `python -B -m unittest discover -s tests -p test_netvue_bridge.py -v`. Sur Debian, depuis le répertoire isolé : `python3 -B -m unittest discover -s tests -p test_netvue_bridge.py -v`, puis la même commande avec `tools/venv/bin/python`. Résultat final : **7 tests réussis** sur chaque système.
4. Première session, depuis le répertoire isolé : `python3 -B -m netvue_bridge.client --source SOURCE_LAN --target CAMERA_2 --live`. Réception réussie, mais décodage impossible faute de décodeur.
5. `python3 -m venv tools/venv` a échoué : `ensurepip` manquait ; aucune installation système ni `sudo`. Après diagnostic, `python3 -m venv --without-pip tools/venv`, téléchargement de `https://bootstrap.pypa.io/get-pip.py` dans `tools/`, puis `tools/venv/bin/python tools/get-pip.py --no-cache-dir` et `tools/venv/bin/python -m pip install --no-cache-dir -r netvue_bridge/requirements.txt`. PyAV 16.1.0 a été installé uniquement dans le venv. Un test PyAV a encodé et décodé une image **synthétique** en mémoire. Le fichier d'amorçage téléchargé a ensuite été supprimé ; le venv de développement reste disponible temporairement.
6. Seconde session, depuis le même répertoire : `tools/venv/bin/python -B -m netvue_bridge.client --source SOURCE_LAN --target CAMERA_2 --live`. Décodage H.264 en mémoire confirmé.
7. Après les deux sessions, le code final a été synchronisé sur Debian ; les sept tests hors ligne ont été relancés. `git diff --check` et `git status --short` ont vérifié l'espace de travail local. Aucun nouveau contact caméra.

Les résultats bruts vidéo, identifiants, clés et adresses privées ne figurent pas dans ce rapport. Aucun service persistant, conteneur Home Assistant ou configuration réseau n'a été modifié.

## Architecture Home Assistant envisagée

**NON IMPLÉMENTÉE ET NON VÉRIFIÉE :** une instance de réception par caméra, découverte et authentification → extraction continue H.264 avec gestion des images clés et des horodatages → remuxage sans transcodage si possible → serveur RTSP local protégé → intégration Home Assistant. Prévoir des buffers bornés, la fermeture sur erreur, des reconnexions avec attente progressive et limites, des secrets en mémoire et des journaux expurgés. L'audio, une seconde caméra, la disponibilité des paramètres H.264 et la reprise après interruption restent à étudier.

La documentation officielle [Generic Camera](https://www.home-assistant.io/integrations/generic/) accepte une URL de flux, notamment RTSP ; [Stream](https://www.home-assistant.io/integrations/stream/) prend en charge les sources H.264/H.265 vers HLS. Cela ne démontre pas que le flux propriétaire actuel est directement compatible : il faut une source standard et vérifier les horodatages, les paramètres du codec et le comportement de lecture. La documentation Generic Camera précise que Home Assistant peut servir les images à distance : une installation accessible depuis Internet nécessite donc une vérification d'accès avant toute intégration.

## Bilan et prochaine action sûre

- **Prouvé :** caméra 2, login LAN avec résultat explicite `0`, sélection LIVE `0` et une image H.264 640 × 360 décodée localement.
- **Hypothétique ou non vérifié :** flux continu remuxable, fonctionnement indépendant du cloud, reconnexion, caméra 1, audio, exposition sécurisée et intégration Home Assistant.
- **Prochaine action sûre :** développer et valider hors ligne une sortie de remuxage à partir de données H.264 synthétiques, sans nouvelle tentative de login ni modification des services ; ensuite définir un essai local borné en tenant compte de la tentative restante.
