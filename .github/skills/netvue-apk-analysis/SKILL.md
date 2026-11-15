---
name: netvue-apk-analysis
description: Analyser de manière statique et reproductible une application Android Netvue fournie par son propriétaire pour identifier des composants vidéo et réseaux sans lancer l'APK ni contacter les appareils. Utiliser ce skill pour les phases d'inventaire, de décompilation, de recherche de protocoles et de rapport de preuves.
---

# Analyse statique de l'APK Netvue

1. Lire `docs/observations.md` avant toute hypothèse sur la famille de caméra.
2. Vérifier la provenance de l'APK ; s'il manque, demander sa mise à disposition dans `input/netvue.apk`. Ne pas télécharger silencieusement depuis un miroir tiers.
3. Préserver l'APK original. Calculer le SHA-256 (`sha256sum input/netvue.apk`) et produire un inventaire avec `scripts/apk_inventory.py`.
4. Inspecter la structure ZIP/DEX/.so, les architectures, les bibliothèques natives et les éventuels hôtes réseau. Les hôtes présents dans un binaire sont des **indices**, pas des communications prouvées.
5. Si JADX existe déjà, décompiler en lecture seule avec `jadx -d findings/decompiled input/netvue.apk`. Si l'outil manque, documenter cette limite et demander avant installation.
6. Dans les fichiers décompilés, rechercher les familles de termes : `rtc`, `webrtc`, `rtsp`, `stun`, `turn`, `ice`, `mqtt`, `aws`, `p2p`, `stream`, `h264`, `h265`, `socket`, `native`.
7. Relier chaque conclusion à une classe/méthode/fichier ou un offset. Préciser le degré de confiance (fort, moyen, faible).
8. Écrire `findings/phase1-report.md` avec : origine/version/hash de l'APK ; artefacts ; bibliothèques pertinentes ; graphe d'appel éventuel ; hypothèses ; limites ; prochaine expérience sûre.
9. Retirer ou masquer les secrets, comptes, Device IDs, cookies, adresses MAC et jetons. Ne pas publier les captures brutes ou le code décompilé.
10. Arrêter avant toute interaction avec des caméras, tout compte cloud, ou toute modification du firmware. Demander une autorisation distincte pour passer à l'analyse dynamique.
