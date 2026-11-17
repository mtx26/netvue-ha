# Netvue Vigil → Home Assistant — Phase 1

> **Architecture actuelle, phase 26 :** une passerelle Go gère dynamiquement
> plusieurs caméras avec un superviseur indépendant par caméra ; Home
> Assistant conserve la configuration et utilise une intégration Python légère.
> Voir le [plan Go](docs/go-plan.md), le [guide du programme Go](netvue-go/README.md),
> l'[architecture](docs/architecture.md), le [guide Go Portainer](deployment/go-portainer.md)
> et le [guide Home Assistant](custom_components/netvue_local/README.md).
> `netvue_bridge/` et le reste de cette page décrivent l'ancienne référence
> Python / la phase 1, pas la stack Go actuelle.

La liste des caméras est persistée par les config entries Home Assistant ; le
gestionnaire Go conserve seulement l'état runtime et ne crée aucun budget
persistant. Les tests hors ligne n'établissent ni la compatibilité de chaque
caméra, ni la stabilité prolongée, ni le déploiement en production.

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
