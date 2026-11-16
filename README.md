# Netvue Vigil → Home Assistant — Phase 1

> Le projet comprend désormais une passerelle LAN → RTSP et une intégration Home Assistant en cours de stabilisation. Voir [l'architecture](docs/architecture.md), [le développement](docs/development.md), [la passerelle](netvue_bridge/README.md) et [le déploiement](deployment/README.md). Le guide ci-dessous conserve le contexte historique de la phase 1 ; il ne décrit pas l'état actuel du pont ni n'autorise une connexion caméra.

Le pont utilise un superviseur et un état de budget uniques (format v4), avec
backoff progressif et sans quotas journaliers ou hebdomadaires. Seul v4 est
pris en charge ; les autres versions sont refusées sans être modifiées. Cette
simplification est testée hors ligne, mais n'a pas été déployée et ne prouve
pas le fonctionnement réel 24 h/24.

Espace de travail prêt à ouvrir dans **VS Code avec GitHub Copilot (harness « Copilot »)**. La première phase est exclusivement consacrée à l'analyse statique de l'application Android Netvue et aux recherches documentaires. Aucun accès à un compte Netvue n'est nécessaire.

## Ce qui est inclus

- `.github/copilot-instructions.md` : règles permanentes du projet.
- `.github/agents/netvue-apk-research.agent.md` : agent GitHub Copilot spécialisé.
- `.github/skills/netvue-apk-analysis/SKILL.md` : procédure d'analyse réutilisable.
- `scripts/apk_inventory.py` : inventaire local, sans dépendance Python ni exécution de l'APK.
- `docs/observations.md` : observations confirmées et limites.
- `docs/phase1-checklist.md` : livrables et points de contrôle.
- `COPILOT_PROMPT.md` : premier message à envoyer à l'agent.

## Installation de l'agent

1. Décompresser cette archive puis ouvrir **le dossier `netvue-copilot-phase1`** dans VS Code (pas seulement un fichier).
2. Vérifier que GitHub Copilot est connecté, et sélectionner **Copilot** comme *agent harness* dans le panneau Chat.
3. `Ctrl+Shift+P` → **Chat: Open Customizations** → **Agents** : vérifier que `Netvue APK Researcher` apparaît. Le sélectionner dans la liste des agents du Chat.
4. Dans **Skills**, vérifier `netvue-apk-analysis`. Ne pas sélectionner l'ancien harness « Local » pour cette configuration.
5. Copier le texte de `COPILOT_PROMPT.md` dans une nouvelle conversation avec cet agent.

L'agent n'impose pas de modèle payant spécifique : choisir dans Copilot un modèle disposant d'un bon raisonnement et de l'accès aux outils de fichiers et au terminal.

## Ajouter un APK à analyser

1. Consulter la [page de distribution Android hébergée par Netvue](https://resource.netvue.com/android/netvue/release.html) et sélectionner une version de l'application qui prend en charge la Vigil première génération. Une page ancienne ne garantit pas qu'il s'agit de la dernière version.
2. Vérifier que l'URL de téléchargement est bien associée à Netvue et ne pas utiliser de miroir inconnu.
3. Créer le dossier `input` puis placer le fichier sous `input/netvue.apk`. **Ne pas lancer ni installer l'APK sur le serveur.**
4. Exécuter l'inventaire :

```bash
python3 scripts/apk_inventory.py input/netvue.apk --output findings/apk-inventory.json
```

Si aucune application n'est disponible, commencer par la recherche documentaire avec l'agent. Son premier rapport doit alors indiquer clairement « APK non fourni ».

Le rapport d'inventaire peut contenir des domaines observés dans le binaire. Il ne prouve pas qu'ils sont utilisés pendant la lecture vidéo.

## Précautions

- `input/`, `findings/` et `captures/` sont exclus de Git : ils peuvent contenir des informations privées.
- Ne jamais enregistrer d'identifiants, de mots de passe, de jetons, de clés, de flux privés ou de captures réseau brutes dans une réponse publique.
- Ne pas exécuter de code extrait de l'application, ni modifier les caméras ou le serveur de production pendant cette phase.
- Ne pas installer de logiciels, ouvrir de ports, désactiver la sécurité ou envoyer de commandes au port 5555 sans accord préalable.

## Objectif du premier rapport

L'agent doit produire `findings/phase1-report.md` avec une traçabilité : preuves réelles, hypothèses, niveau de confiance, fichiers analysés et prochaine expérience sans risque. Un protocole H.264, WebRTC, MQTT ou P2P ne doit jamais être présenté comme confirmé sans preuve liée à Netvue Vigil.
