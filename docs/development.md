# Développement et tests hors caméra

## Environnement

Le pont cible Linux ; les verrous `fcntl`, `CLOCK_BOOTTIME` et les métriques cgroup du superviseur continu ne sont pas portables sur Windows. Le [Dockerfile](../deployment/Dockerfile) utilise Python 3.13 et PyAV 16.1.0 ; [requirements.txt](../netvue_bridge/requirements.txt) indique la plage acceptée. L'intégration utilise les API de Home Assistant ; la version réellement ciblée doit être testée séparément avec ses dépendances officielles.

Installer les outils et environnements de développement dans `tools/`, sans accès administrateur ni modification système. Ne jamais placer de secrets, captures réelles ou budgets réels dans les tests. Les commandes ci-dessous sont destinées à une machine de développement, pas au serveur de production.

## Suites

Prérequis : Python, PyAV et, pour les tests Home Assistant, les dépendances HA compatibles. La commande suivante découvre toutes les suites unitaires ; elle ne doit pas recevoir de variables d'environnement pointant vers des appareils réels :

```text
python -B -m unittest discover -s tests -p test_*.py -v
```

Sous Windows, les tests de verrou Linux sont ignorés ; sans HA, le module correspondant ne peut pas être importé. Les tests de vidéo synthétique ont besoin de PyAV et d'un encodeur H.264. Les tests RTSP complets exigent MediaMTX, FFmpeg et FFprobe portables, ainsi qu'une boucle locale et des ports de test libres. La configuration et les variables sont expliquées dans [netvue_bridge/README.md](../netvue_bridge/README.md). Les tests marqués par `NETVUE_MEDIAMTX` peuvent ouvrir des sockets fictifs et lancer des processus locaux ; vérifier leur isolation et l'absence de conflit avant de les activer.

[ha_integration_synthetic.py](../tests/ha_integration_synthetic.py) nécessite l'image HA cible et des montages dédiés ; il utilise le port 8554 **dans un conteneur réseau isolé** et ne doit pas être exécuté sur le réseau host. Les tests RTSP de l'hôte prennent un port libre ; les simulations Docker/Swarm sont distinctes et ne doivent jamais viser la stack existante.

La simulation accélérée [timestamp_long_synthetic.py](../tests/timestamp_long_synthetic.py) peut aussi fonctionner directement sous Linux avec PyAV/libx264, FFmpeg et FFprobe. Depuis la racine du projet, en adaptant les chemins des binaires :

```sh
PYTHONPATH=. NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe \
NETVUE_FFMPEG=tools/ffmpeg-btbn/ffmpeg \
tools/venv-phase14-linux/bin/python -B tests/timestamp_long_synthetic.py
```

Elle produit uniquement des données fictives et un fichier vidéo temporaire automatiquement supprimé, puis affiche des métriques et les résultats des lecteurs. Elle consomme CPU, mémoire et espace temporaire. Elle n'ouvre aucune connexion caméra. Une métrique cgroup absente est `null` ; un cgroup partagé ne mesure pas exclusivement le processus de test. L'horloge vidéo est simulée et ne démontre pas la stabilité en temps réel.

## Organisation et diagnostic

Les tests RTSP synthétiques choisissent un port libre afin d'éviter les collisions lorsqu'ils tournent en parallèle. Le scénario HA garde 8554 uniquement à l'intérieur de son conteneur réseau isolé.

Suivre [architecture.md](architecture.md) pour le sens des dépendances : isoler le protocole de HA, préserver le comportement public et tester chaque modification avec des données fictives. Ajouter un module seulement s'il clarifie une responsabilité ; garder les anciens imports et CLI lorsque leur compatibilité est requise. Utiliser des exceptions explicites, borner les ressources et fermer sockets et processus.

Pour diagnostiquer une régression, commencer par les tests du module, puis les tests synthétiques de bout en bout ; rapporter les échecs et les tests ignorés. Les journaux de diagnostic doivent rester expurgés : ne pas partager clés, identifiants, captures ni vidéo. Ne pas réinitialiser, déplacer ou dupliquer un budget pour reprendre une connexion. Toute validation caméra réelle exige une autorisation explicite distincte.

Les tests du superviseur utilisent uniquement des répertoires temporaires :
verrou exclusif, migration v2/v3 auditée, reprise après redémarrage avec
contrôle de session/horloge, état bloqué ou corrompu conservé, absence de
quotas permanents, backoff, watchdog et erreurs critiques. Ils ne modifient
aucun budget réel et ne valident pas la migration en production.
