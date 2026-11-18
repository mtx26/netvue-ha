# Phase 19 — Bibliothèques éprouvées

## OBJECTIF

Rechercher si des bibliothèques existantes peuvent remplacer utilement du code
réseau, H.264/RTSP, de supervision ou Home Assistant, sans ajouter de
dépendance disproportionnée.

## MODIFICATIONS

- `netvue_bridge/manager.py` utilise `http.HTTPStatus` pour la phrase HTTP.
  La table locale `_reason` est supprimée ; aucun paquet n'est ajouté.
- Les autres fonctionnalités examinées restent dans leurs bibliothèques
  actuelles ou dans le code spécifique au protocole.

## FONCTIONNEMENT

`asyncio` demeure adapté au gestionnaire : les mêmes tâches possèdent le
serveur local, les verrous et les sous-processus. Un serveur HTTP synchrone
introduirait une passerelle entre threads et boucle asyncio ; un framework
tiers ajouterait un besoin d'installation au conteneur.

Le format Netvue est propriétaire : les paquets utilisent les primitives
standard `socket`, `struct` et `json`, tandis que ses validations restent
spécifiques. PyAV, déjà dépendance du pont et utilisé avec FFmpeg pour le
décodage H.264 et le mux RTSP, ne remplace pas les règles particulières de
PTS, de changements SPS/PPS et de reprise sur IDR. Home Assistant fournit déjà
`aiohttp` et son client partagé est utilisé par `bridge_api.py`. Le budget
persistant dépend de `fcntl` et d'écritures atomiques ; une bibliothèque
générique de retry ne fournit pas ces garanties. La sonde RTSP de Home
Assistant n'effectue qu'un `DESCRIBE` local et vérifie le SDP H.264 ; un
client RTSP complet élargirait les dépendances sans utilité pour cette sonde.

En conséquence, aucune dépendance tierce supplémentaire n'est justifiée.

## TESTS

- `test_bridge_manager.py` : **5/5 réussis**.
- Vérification de syntaxe Python et `git diff --check` : réussis.
- Aucun paquet ni manifeste de dépendances n'a été modifié.

## ÉTAT FINAL

Le seul remplacement direct identifié est la table de phrases HTTP par
l'énumération standard. Le protocole Netvue, le traitement H.264/PTS, la
publication RTSP, la supervision et l'intégration Home Assistant conservent
leurs outils actuels : aucun substitut n'apportait ici une réduction sûre de
complexité. Aucun contact avec les caméras ni changement de production n'a
été effectué.
