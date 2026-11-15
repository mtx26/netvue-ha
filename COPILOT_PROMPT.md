# Premier message à envoyer à « Netvue APK Researcher »

Commence **la phase 1** du projet Netvue Vigil → Home Assistant. Lis les instructions du dépôt, le skill `netvue-apk-analysis`, `docs/observations.md` et `docs/phase1-checklist.md`.

1. Vérifie si `input/netvue.apk` est présent. S'il manque, recherche seulement les sources officielles sur les anciennes et récentes versions de l'application Netvue ; indique exactement quelle version je devrais fournir et pourquoi.
2. Si l'APK est présent, réalise l'inventaire et l'analyse statique du code et des bibliothèques natives. Identifie les voies de connexion, d'authentification et de live view, avec une attention particulière à WebRTC/P2P, MQTT, H.264 et au mystérieux TCP 5555.
3. Vérifie tes hypothèses par des éléments observables. Ne déduis jamais qu'un protocole est utilisé simplement parce que son nom apparaît dans une bibliothèque.
4. Produis `findings/phase1-report.md` avec les preuves, les chemins de fichiers, les incertitudes et **une** prochaine expérience réalisable et sûre.

N'accède à aucune caméra, aucun compte Netvue et aucune API privée. N'installe aucun programme sans me demander. N'exécute pas l'APK et ne modifie aucune configuration réseau ou aucun firmware.
