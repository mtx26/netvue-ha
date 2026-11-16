# Instructions permanentes — projet Netvue Vigil

## Contexte

Le propriétaire possède deux caméras Netvue Vigil de génération non encore confirmée et un serveur Debian exécutant Home Assistant en conteneur. Les caméras n'ont actuellement pas de carte microSD installée. Des analyses TCP du réseau local ont montré uniquement le port 5555 ouvert sur chacune ; Telnet (23) et RTSP standard (554) sont fermés.

Le but final serait de récupérer un flux vidéo autorisé vers Home Assistant, idéalement localement. Le projet a terminé sa phase initiale d'analyse statique.

L'objectif actuel est de développer, tester et stabiliser
une passerelle vidéo locale Netvue LAN → RTSP,
puis de l'intégrer à Home Assistant.

Les tests réseau réels sur les caméras appartenant
au propriétaire peuvent être réalisés lorsqu'une
autorisation explicite et limitée a été accordée.

Les restrictions propres à l'analyse statique
concernent la phase 1 et ne doivent pas être
interprétées comme une interdiction permanente
des tests autorisés des phases suivantes.

## Règles de travail

1. Écrire en français clair. Distinguer systématiquement « observé », « déduit » et « non vérifié ».
2. Favoriser les sources primaires : code source, application fournie, projet officiel Netvue, documentation VS Code/Home Assistant, dépôts originaux des outils.
3. Citer pour chaque découverte le chemin de fichier, la méthode et si possible les classes, méthodes ou offsets. Ne jamais inventer un résultat d'analyse ou de scan.
4. Ne jamais supposer que `5555/tcp` correspond à Freeciv, ADB, RTSP, Telnet ou un protocole précis : ce sont des étiquettes possibles, pas des preuves.
5. Ne pas supposer que GK7101 est confirmé sur ces exemplaires : c'est une piste issue des spécifications de certaines Vigil de première génération.
6. Travailler avec des captures et binaires appartenant au propriétaire ; ne pas utiliser de secrets obtenus ailleurs.
7. MODE AUTONOME : l'agent est autorisé à télécharger des APK
   publiques depuis les serveurs officiels Netvue, à les analyser,
   à utiliser le terminal et à télécharger des outils portables
   depuis leurs dépôts officiels (GitHub notamment).

   Il doit effectuer automatiquement toutes les étapes de l'analyse
   statique sans demander de confirmation à chaque étape.

   Les outils supplémentaires doivent être installés exclusivement
   dans le dossier tools/ du projet, sans droits administrateur.

   Une autorisation reste obligatoire pour :
   - Accéder aux comptes Netvue.
   - Communiquer directement avec les caméras.
   - Modifier le firmware.
   - Modifier le réseau ou le serveur.
   - Installer des logiciels au niveau du système.
   - Exécuter l'APK ou du code extrait.
8. Toute nouvelle commande doit indiquer sa finalité, ses prérequis, les données qu'elle produit et ses risques. Les commandes de lecture locale sont prioritaires.
9. Préserver les fichiers originaux ; écrire les résultats sous `findings/` et n'écraser aucun résultat existant sans justification.
10. Ne jamais exposer dans Git, un rapport partagé ou le Chat : mots de passe, jetons, cookies, adresses MAC complètes, Device IDs, clés cryptographiques, adresses IP publiques, captures brutes contenant des secrets.
11. Si une hypothèse exige une capture réseau non visible depuis le serveur à cause de la commutation Ethernet/Wi-Fi, le signaler : un `tcpdump` exécuté sur Debian n'observe pas automatiquement le trafic unicast entre une caméra et la box.
12. Les restrictions spécifiques à la phase 1
s'appliquent uniquement à l'analyse statique.

Les phases de développement ultérieures peuvent
inclure des interactions réseau légitimes avec
les appareils du propriétaire, sous réserve
d'autorisation explicite.

Les modifications de firmware, réinitialisations
et opérations destructives nécessitent toujours
une autorisation spécifique.

## Livrable

Rédiger un rapport court, reproductible et orienté décision : API/cloud possible ? flux local plausible ? preuve manquante ? meilleure prochaine étape ?
