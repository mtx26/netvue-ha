# Développement et tests hors caméra

## Environnement

Le pont cible Linux ; les verrous `fcntl`, `CLOCK_BOOTTIME` et les métriques cgroup du superviseur continu ne sont pas portables sur Windows. Le [Dockerfile](../deployment/Dockerfile) utilise Python 3.13 et PyAV 16.1.0 ; [requirements.txt](../netvue_bridge/requirements.txt) indique la plage acceptée. L'intégration utilise les API de Home Assistant ; la version réellement ciblée doit être testée séparément avec ses dépendances officielles.

Installer les outils et environnements de développement dans `tools/`, sans accès administrateur ni modification système. Ne jamais placer de secrets, captures réelles ou budgets réels dans les tests. Les commandes ci-dessous sont destinées à une machine de développement, pas au serveur de production.

## Suites

Prérequis : Python, PyAV et, pour les tests Home Assistant, les dépendances
épinglées dans [requirements-homeassistant.txt](../tests/requirements-homeassistant.txt).
La commande suivante découvre toutes les suites unitaires ; elle ne doit pas
recevoir de variables d'environnement pointant vers des appareils réels :

```text
python -B -m unittest discover -s tests -p test_*.py -v
```

Sous Windows, les tests de verrou Linux sont ignorés ; sans HA, le module correspondant ne peut pas être importé. Les tests de vidéo synthétique ont besoin de PyAV et d'un encodeur H.264. Les tests RTSP complets exigent MediaMTX, FFmpeg et FFprobe portables, ainsi qu'une boucle locale et des ports de test libres. La configuration et les variables sont expliquées dans [netvue_bridge/README.md](../netvue_bridge/README.md). Les tests marqués par `NETVUE_MEDIAMTX` peuvent ouvrir des sockets fictifs et lancer des processus locaux ; vérifier leur isolation et l'absence de conflit avant de les activer.

Sur une distribution Linux où `ensurepip` est disponible, exécuter la suite HA
avec ses dépendances de test isolées :

```sh
python3 -m venv tools/venv-ha
tools/venv-ha/bin/python -m pip install -r tests/requirements-homeassistant.txt
tools/venv-ha/bin/python -B -m unittest tests.test_home_assistant_integration -v
```

Le manifeste inclut aussi les dépendances du composant HA `stream`, que
l'installation PyPI de Home Assistant n'installe pas automatiquement. Dans
le WSL utilisé pour cette phase, `ensurepip` n'est pas disponible : ne pas
utiliser la commande `venv` ci-dessus dans cet environnement. Le test a été
exécuté dans un venv persistant sous `$HOME/.venvs` en utilisant pip depuis le
venv Linux du projet :

```sh
mkdir -p "$HOME/.venvs"
python3 -m venv --without-pip "$HOME/.venvs/netvue-ha"
tools/venv-phase14-linux/bin/python -m pip \
  --python "$HOME/.venvs/netvue-ha/bin/python" \
  install -r tests/requirements-homeassistant.txt
"$HOME/.venvs/netvue-ha/bin/python" -B \
  -m unittest tests.test_home_assistant_integration -v
```

[ha_integration_synthetic.py](../tests/ha_integration_synthetic.py) exerce le vrai flux HA, un MediaMTX et une vidéo synthétique. Sous Windows avec Docker Desktop, exécuter depuis la racine du dépôt dans PowerShell ; le réseau Docker `--internal` évite tout accès externe, aucun port n'est publié et le port RTSP 8554 reste interne au conteneur :

```powershell
$docker = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
$repo = (Get-Location).Path
$network = "netvue-ha-test-$([guid]::NewGuid().ToString('N'))"
& $docker network create --internal --driver bridge $network
if ($LASTEXITCODE -ne 0) { throw "Impossible de créer le réseau Docker isolé." }
try {
    $dockerArgs = @(
        'run', '--rm', '--network', $network,
        '--mount', "type=bind,source=$repo,target=/workspace,readonly",
        '--workdir', '/workspace',
        '--env', 'PYTHONPATH=/workspace',
        '--env', 'NETVUE_TEST_COMPONENT=/workspace/custom_components/netvue_local',
        '--env', 'NETVUE_TEST_MEDIAMTX=/workspace/tools/mediamtx-1.21.4/mediamtx',
        '--env', 'NETVUE_TEST_MEDIAMTX_CONFIG=/workspace/netvue_bridge/mediamtx.local.yml',
        '--env', 'NETVUE_TEST_FFMPEG=/usr/bin/ffmpeg',
        '--entrypoint', 'python',
        'ghcr.io/home-assistant/home-assistant:2026.11.2',
        '-B', 'tests/ha_integration_synthetic.py'
    )
    & $docker @dockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Le test HA synthétique a échoué." }
}
finally {
    & $docker network rm $network
}
```

Le dossier du dépôt est monté en lecture seule ; seuls des processus et fichiers temporaires existent dans le conteneur. Les tests RTSP de l'hôte prennent un port libre ; les simulations Docker/Swarm sont distinctes et ne doivent jamais viser la stack existante.

La simulation accélérée [timestamp_long_synthetic.py](../tests/timestamp_long_synthetic.py) peut aussi fonctionner directement sous Linux avec PyAV/libx264, FFmpeg et FFprobe. Depuis la racine du projet, en adaptant les chemins des binaires :

```sh
PYTHONPATH=. NETVUE_FFPROBE=tools/ffmpeg-btbn/ffprobe \
NETVUE_FFMPEG=tools/ffmpeg-btbn/ffmpeg \
tools/venv-phase14-linux/bin/python -B tests/timestamp_long_synthetic.py
```

Elle produit uniquement des données fictives et un fichier vidéo temporaire automatiquement supprimé, puis affiche des métriques et les résultats des lecteurs. Elle consomme CPU, mémoire et espace temporaire. Elle n'ouvre aucune connexion caméra. Une métrique cgroup absente est `null` ; un cgroup partagé ne mesure pas exclusivement le processus de test. L'horloge vidéo est simulée et ne démontre pas la stabilité en temps réel.

## Organisation et diagnostic

Les tests RTSP synthétiques choisissent un port libre afin d'éviter les collisions lorsqu'ils tournent en parallèle. Le scénario HA garde 8554 uniquement à l'intérieur de son conteneur réseau isolé.

Suivre [architecture.md](architecture.md) pour le sens des dépendances : isoler le protocole de HA, préserver les contrats actuellement utilisés et tester chaque modification avec des données fictives. Ne garder ni ancien import ni ancienne commande uniquement pour compatibilité historique. Utiliser des exceptions explicites, borner les ressources et fermer sockets et processus.

Pour diagnostiquer une régression, commencer par les tests du module, puis les tests synthétiques de bout en bout ; rapporter les échecs et les tests ignorés. Les journaux de diagnostic doivent rester expurgés : ne pas partager clés, identifiants, captures ni vidéo. Ne pas réinitialiser, déplacer ou dupliquer un budget pour reprendre une connexion. Toute validation caméra réelle exige une autorisation explicite distincte.

Les tests du superviseur utilisent uniquement des répertoires temporaires :
verrou exclusif, refus explicite et conservation des états non-v4, reprise
après redémarrage avec contrôle de session/horloge, état bloqué ou corrompu
conservé, absence de quotas permanents, backoff, watchdog et erreurs critiques.
Ils ne modifient aucun budget réel.
