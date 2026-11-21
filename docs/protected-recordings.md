# Stockage protégé

Le gestionnaire conserve les exports dans `NETVUE_PROTECTED_RECORDINGS_DIR`
(par défaut le sous-dossier `protected` de `NETVUE_RECORDINGS_DIR`). Le volume
protégé du stack est distinct du volume temporaire. La rétention du gestionnaire
ignore le répertoire protégé et attend les exports en cours. MediaMTX doit
conserver `recordDeleteAfter: 0s` ; aucun autre processus ne doit supprimer les
segments pendant un export.

- `NETVUE_PROTECTED_MIN_FREE_BYTES` : espace libre minimal, par défaut 1 Gio.
  La disponibilité est vérifiée avant et pendant les traitements FFmpeg.
- `NETVUE_PROTECTED_MAX_DURATION` : durée maximale d'une demande, par défaut
  `1h`. Une seule exportation protégée est exécutée à la fois.

`POST /api/v1/cameras/{host}/protected` accepte `start`, `end` en RFC 3339,
et les champs facultatifs `title`, `category`, `protection`. Une couverture
incomplète renvoie HTTP 409 avec `gaps` et un enregistrement `failed`. La
bibliothèque globale reste disponible sur `GET /api/v1/cameras/{host}/protected` ;
le téléchargement et la suppression ciblent un identifiant par les routes
`/{id}/download` et `/{id}`. `GET /api/v1/recordings/protected/health`
retourne l'espace libre, le seuil et l'état ; une erreur renvoie HTTP 503.

Les limites de couverture proviennent de l'API MediaMTX, pas d'une analyse de
chaque image source. Les parties sont décodées puis réencodées pour éviter la
perte des images aux frontières non alignées sur une image clé : le contenu
vidéo est donc recompressé. Les formats incompatibles entre parties sont
refusés et le fichier final est décodé avant publication. Les horodatages
manquants ou répétés sont également refusés : un test synthétique avec
MediaMTX 1.21.4 a reproduit des horodatages répétés et une perte d'images
sans cette protection. La compatibilité des enregistrements réels reste à
vérifier. Le seuil d'espace
est une surveillance périodique, pas une réservation : une écriture externe
rapide peut encore saturer le disque. Un état intermédiaire dans l'index permet
de retrouver au redémarrage une publication interrompue ; les fichiers
temporaires résiduels ne sont pas supprimés automatiquement. La synchronisation
du répertoire est effectuée sous Linux ; la durabilité après panne sous Windows
n'est pas garantie.
