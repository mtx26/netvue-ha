# Netvue Local pour Home Assistant

L'intégration conserve les entrées HA et pilote le pont Go ; seul le pont
effectue découverte LAN, login et réception vidéo.

## Installation

- Pont Go et MediaMTX sur le même nœud et réseau host que HA : MediaMTX
  `127.0.0.1:8554`, API `127.0.0.1:8765`.
- L'IPv4 de `NETVUE_SOURCE` doit couvrir le réseau des caméras.
- Ne publiez pas ces ports : API sans authentification distincte.

Dans HACS, ajouter `https://github.com/mtx26/netvue-ha` comme dépôt
personnalisé de catégorie **Intégration**, installer **Netvue Local**, puis
redémarrer Home Assistant. Le dépôt doit être public.

Suivre le [guide Portainer](../../deployment/go-portainer.md) pour installer le
pont Go et MediaMTX.

## Ajouter et gérer une caméra

Dans **Paramètres → Appareils et services → Ajouter une intégration**, choisir
**Netvue Local**, saisir un nom puis l'IPv4 privée. L'intégration génère le nom
et l'URL RTSP ; elle ne demande ni URL, port ni identifiants :

```text
192.168.10.2 → netvue_192_168_10_2
             → rtsp://127.0.0.1:8554/netvue_192_168_10_2
```

Cette opération démarre une session caméra. Une entrée HA restaurée peut
également être réconciliée dès qu'un gestionnaire répond : voir les
[précautions de déploiement](../../deployment/go-portainer.md) avant tout
essai ou redémarrage. L'adresse est manuelle ; HA ne parle pas le protocole
Netvue.

Une session et un superviseur Go sont gérés par entrée ; le nombre maximal
n'est pas fixé et la capacité dépend des ressources/caméras/MediaMTX. Le
registre Go est volatil, HA restaure ses entrées. L'entité caméra lit via HA
Stream ; le capteur binaire indique la disponibilité SDP RTSP/H.264. Le
capteur **Session** expose l'état du superviseur et une catégorie d'erreur
non sensible ; le bouton **Redémarrer la session** est disponible en état
arrêté ou en échec et ne garantit pas la disponibilité immédiate du flux. Une session
supervisée peut reconnecter, sans exposer dans HA la cause de chaque erreur
transitoire.

Le nom affiché se modifie depuis l'appareil/l'entité ; les options de
l'intégration changent l'IPv4, arrêtent l'ancien flux et démarrent le nouveau.
La suppression de l'intégration arrête et retire la session. Une ancienne
entrée sans nom géré ou avec URL personnalisée est refusée, jamais convertie
ou réécrite automatiquement.

## Dépannage et erreurs de cycle de vie

- **Gestionnaire absent** : vérifier service `netvue-bridge` `1/1`, réseau
  partagé et écoute `127.0.0.1:8765`. HA réessaie l'entrée.
- **Pas de flux** : vérifier MediaMTX `127.0.0.1:8554`, état de session et
  journaux du pont ; pour un échec définitif, consulter `error`, corriger la
  cause puis reprendre explicitement.
- Au déchargement, HA retire les plateformes avant de supprimer la session.
  En cas d'échec, il tente de restaurer les plateformes ; le pont conserve
  session/état si leur retrait échoue. Une suppression échouée peut nécessiter
  un redémarrage HA. Si la réponse est incertaine, le coordinator ne recrée
  pas automatiquement une session manquante pendant cette exécution ; il
  faut confirmer la suppression avant toute nouvelle configuration. Cette
  protection en mémoire n'est pas un budget persistant.
- Si la création n'a pas reçu de réponse, cette caméra est bloquée pour
  nouvelles créations, reprises et suppressions durant cette exécution :
  une requête précédente peut encore aboutir. Un redémarrage ne dissipe pas ce
  doute : vérifier les producteurs et la session avant toute action.
- L'API est joignable par les processus du même réseau host. Les tests
  synthétiques n'utilisent aucune caméra réelle et ne prouvent ni compatibilité
  des autres modèles ni disponibilité prolongée.
