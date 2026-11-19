# Phase 45 — Validation réelle bornée du talkback

## Résultat

Un talkback direct Go vers la caméra a transmis une courte tonalité AAC et une
augmentation spectrale à 700 Hz a été mesurée dans le retour audio caméra.
Cela constitue un indice de retour audio, pas une preuve que le haut-parleur a
restitué un son audible. Le microphone navigateur/Home Assistant n'a pas été
utilisé ; `two_way_audio` reste désactivé par défaut.

## Essais

- `ssh home` a fonctionné. Avant les essais, aucune connexion établie vers
  `192.168.129.2` ni aucun conteneur Netvue concurrent n'était présent.
- Quatre sessions séquentielles, totalisant 84 s : réception seule 12 s
  (capture RTSP démarrée trop tôt, sans mesure exploitable), référence 25 s,
  talk 25 s, référence après talk 22 s. Au moins 15 s séparaient les sessions.
  La limite autorisée de 30 s par session et 5 min cumulées a été respectée.
- MediaMTX était un conteneur temporaire limité à `127.0.0.1:8554`. Aucun
  service de production ni paramètre caméra n'a été modifié. Les sessions,
  conteneurs et fichiers temporaires ont été arrêtés ou supprimés.
- Le signal était une sinusoïde AAC-LC/ADTS mono 8 kHz à 700 Hz, 0,5 s,
  crête -30 dBFS et RMS décodé -33,5 dBFS. Le fichier encodé représentait
  0,64 s de trames AAC. Il a été transmis par la commande Go `talk` avec un
  délai de 3 s après la première publication vidéo ; les journaux indiquent
  « audio sending » puis « audio sent », et la session s'est terminée sans
  erreur. Aucun ACK média ou statut du haut-parleur n'a été observé.
- Le retour RTSP était AAC mono 8 kHz. Sur des fenêtres de 100 ms, le niveau
  spectral maximal à 700 Hz était de -62,3 dBFS avant émission, -53,7 dBFS
  pendant le talk (fenêtre à 4,3 s), puis -64,4 dBFS après. Le contrôle
  1200 Hz mesurait -69,8 dBFS dans la fenêtre du pic 700 Hz. Les fenêtres ne
  permettent pas une comparaison synchronisée assez précise pour attribuer le
  pic au signal envoyé. L'augmentation est compatible avec un retour de la
  tonalité, sans exclure bruit ambiant, couplage électronique ou
  traitement/annulation d'écho.
- Le son caméra a été décodé et analysé en mémoire ; aucune capture audio brute
  n'a été conservée. La session Go s'est terminée normalement à sa durée
  prévue, mais les journaux signalent une reprise de publication au changement
  de format audio puis une attente d'IDR après changement H.264. La continuité
  vidéo image par image et la synchronisation n'ont donc pas été établies.

## Limites et statut

- **Démontré :** la commande Go a transmis le média AAC sans erreur ; un flux
  AAC caméra a été reçu et décodé pendant la session ; le retour présente un
  pic spectral 700 Hz supérieur aux deux références.
- **Non démontré :** acceptation explicite par le firmware, restitution
  acoustique indépendante ou son réellement entendu au haut-parleur.
- **Non validé en réel :** microphone du navigateur/téléphone → WebRTC →
  Home Assistant → Go → caméra, bouton dans le lecteur, continuité A/V et
  synchronisation pendant une longue session.
- Le contrôle microphone reste expérimental et désactivé par défaut.
  `two_way_audio` ne doit pas être annoncé comme validé avant une écoute ou une
  mesure acoustique indépendante et un essai du chemin HA réel.

Les tests Go, race, vet, build, FFmpeg/WebRTC synthétiques et les 58 tests
Home Assistant/Portainer isolés étaient réussis avant ces essais. Ils ne
remplacent pas les validations réelles encore manquantes ci-dessus.

## Suivi — bouton microphone grisé dans Home Assistant

- Le RTSP déjà publié annonçait H.264 Main 4.1 (`4d0029`) ; un Chromium de
  test proposait Main 3.1 (`4d001f`). Cette combinaison était refusée par le
  pont avant la mise en place du média. Le SDP de la tentative réelle n'a
  pas été capturé. Un redémarrage RTSP a aussi été observé indépendamment.
- Le pont garde le passage direct quand l'offre est compatible. Sinon, il
  réencode uniquement la vidéo WebRTC en Constrained Baseline 3.1 si le
  navigateur l'offre. Le test synthétique publie du Main 4.1 en RTSP,
  négocie avec un pair WebRTC local et décode quatre images de sortie.
- Le navigateur doit toujours joindre l'adresse IPv4 ICE du pont en UDP.
  Le tunnel HTTPS Cloudflare seul ne fournit pas cette route. Le microphone
  et le haut-parleur n'ont pas été testés sur la caméra réelle.
