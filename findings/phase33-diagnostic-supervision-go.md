# Phase 33 — Diagnostic de la supervision Go

## Objectif et décisions

Identifier la source du message générique sans contacter de caméra. Le
gestionnaire convertissait toute erreur `ErrProtocol` ou `ErrMediaFrame`
en « Invalid Netvue protocol or video data », puis arrêtait la caméra.
`ErrProtocol` recouvre aussi la validation H.264 ; la connexion fermée utilise
un marqueur distinct et les erreurs réseau récupérables sont réessayées.
Le texte seul ne permet donc pas d'identifier le contrôle ayant échoué.

La confirmation LIVE version 1 après une demande version 2 a été observée et
corrigée en phase 32. Une image installée avant cette correction pourrait
reproduire l'erreur, mais rien ne démontre que c'est la cause du présent log.
Les versions LIVE 1 et 2 restent admises ; aucune validation n'est assouplie.
Les suggestions d'ignorer des types média inconnus ou de réessayer toute
erreur de données ne sont pas retenues faute de preuve et pour éviter des
reconnexions infinies sur un format incompatible.

## Modifications et tests

Les diagnostics utilisent des catégories, étapes et motifs statiques : aucun
JSON, paquet, clé ou message d'erreur arbitraire n'est recopié. L'API, les
logs du gestionnaire et les commandes directes de session utilisent ce même
diagnostic ; le superviseur journalise aussi les erreurs récupérables avant
reconnexion. L'adresse IPv4 de la caméra reste visible pour identifier la
session. Les erreurs chaînées et leur politique de reprise restent inchangées.

Tests synthétiques : cadrage, JSON, ordre LIVE, trame média, Annex B invalide,
coupure, sécurité des logs/API et reprise ; voir les tests Go du dépôt.

## État final et limites

La cause propre à la caméra n'est pas identifiable à partir du message
fourni. Sans contact réseau, vérifier d'abord auprès du propriétaire quelle
version ou quel digest de l'image était effectivement en service au moment
du log et si elle contient la correction LIVE de la phase 32. Si un journal
existant de la version diagnostique contient une ligne d'arrêt, relever
uniquement sa catégorie, étape et raison ; masquer l'IP avant partage.

Si ce journal n'existe pas, une observation nécessite une autorisation séparée :
après les préconditions de sécurité du guide Portainer, faire effectuer par
le propriétaire une seule session diagnostique bornée à 15 secondes sur la
caméra concernée avec un binaire contenant ces diagnostics, sans session
concurrente, puis relever seulement la ligne assainie et son résultat.
Ne pas capturer de paquets, vidéo, JSON, clé ou identifiant. Ne pas lancer
cet essai ni modifier ou redémarrer la production sans autorisation.
