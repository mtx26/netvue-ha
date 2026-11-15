---
name: netvue-apk-analysis
description: Analyser de manière statique et reproductible une application Android Netvue fournie par son propriétaire pour identifier des composants vidéo et réseaux sans lancer l'APK ni contacter les appareils. Utiliser ce skill pour les phases d'inventaire, de décompilation, de recherche de protocoles et de rapport de preuves.
---

# Analyse statique de l'APK Netvue

1. Lire `docs/observations.md` avant toute hypothèse sur la famille de caméra.
2. Si l'APK n'est pas présent, rechercher et télécharger
   automatiquement une version officielle depuis Netvue.
   Vérifier son origine et calculer son SHA-256.
   Ne pas utiliser de miroir inconnu.
3. Préserver l'APK original. Calculer le SHA-256 (`sha256sum input/netvue.apk`) et produire un inventaire avec `scripts/apk_inventory.py`.
4. Inspecter la structure ZIP/DEX/.so, les architectures, les bibliothèques natives et les éventuels hôtes réseau. Les hôtes présents dans un binaire sont des **indices**, pas des communications prouvées.
5. Vérifier la présence de JADX. S'il est absent,
   télécharger automatiquement une version portable depuis
   le dépôt GitHub officiel du projet, l'extraire dans tools/
   et l'utiliser pour décompiler l'APK.

   Ne pas effectuer d'installation système.
6. Dans les fichiers décompilés, rechercher les familles de termes : `rtc`, `webrtc`, `rtsp`, `stun`, `turn`, `ice`, `mqtt`, `aws`, `p2p`, `stream`, `h264`, `h265`, `socket`, `native`.
7. Relier chaque conclusion à une classe/méthode/fichier ou un offset. Préciser le degré de confiance (fort, moyen, faible).
8. Écrire `findings/phase1-report.md` avec : origine/version/hash de l'APK ; artefacts ; bibliothèques pertinentes ; graphe d'appel éventuel ; hypothèses ; limites ; prochaine expérience sûre.
9. Retirer ou masquer les secrets, comptes, Device IDs, cookies, adresses MAC et jetons. Ne pas publier les captures brutes ou le code décompilé.
10. Continuer automatiquement toutes les étapes d'analyse
    statique autorisées jusqu'à produire un rapport complet.

    Ne pas s'arrêter parce qu'un outil manque si une solution
    portable officielle existe.

    Demander une autorisation uniquement avant une analyse
    dynamique, un accès aux caméras, une authentification
    cloud ou une modification du firmware.
