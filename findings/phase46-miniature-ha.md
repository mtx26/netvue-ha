# Phase 46 — Miniature Home Assistant

## Objectif et diagnostic

Afficher toute l'image sans déformer ni modifier le flux principal. Le point
de départ retenu est le badge d'une ligne d'entité HA ; l'écran exact de
l'utilisateur n'a pas été observé.

Dans HA 2026.11.2, l'entité caméra fournit `entity_picture` vers
`/api/camera_proxy/{entity_id}?token=…`. Le
[frontend 20261028.6](https://github.com/home-assistant/frontend/blob/20261028.6/src/components/entity/state-badge.ts)
ajoute `width=80&height=80` et affiche le résultat dans un carré de 40 px avec
`background-size: cover` et des coins éventuellement arrondis. La
[ligne d'entité](https://github.com/home-assistant/frontend/blob/20261028.6/src/panels/lovelace/components/hui-generic-entity-row.ts)
utilise ce badge à gauche du nom.

L'intégration utilisait `use_stream_for_stills=True`. Le
[proxy HA](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/components/camera/__init__.py)
transmet les dimensions au flux, dont le
[convertisseur](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/components/stream/core.py)
redimensionne la trame à la taille exacte et conserve la dernière image en
cache : une demande 80 × 80 peut donc déformer une image non carrée et
influencer une demande suivante. Le frontend peut également recadrer une
image non carrée avec `cover`.

Le format est JPEG. Sans dimensions demandées, ses dimensions dépendent du flux
et de l'orientation configurée dans HA ; la résolution exacte de la caméra
réelle n'a pas été mesurée pendant cette phase. Les essais synthétiques du
projet vérifiaient déjà le JPEG, mais utilisaient une source carrée de 64 × 64.

## Correction et validation

L'intégration récupère désormais la trame sans imposer de dimensions à HA
Stream. Pour une demande exactement 80 × 80, elle produit un JPEG carré avec
image proportionnelle centrée et marges noires, y compris pour un badge
circulaire. Les autres demandes reçoivent l'image non remaniée ; HA peut
ensuite la réduire proportionnellement avec TurboJPEG. Les captures sans
dimensions attendent la prochaine image-clé, comme le service de capture HA
précédemment. Le flux RTSP, HLS et WebRTC reste inchangé.

Ce ciblage repose sur les dimensions et non sur l'identité de l'écran : une
autre demande explicite de 80 × 80 recevra aussi les marges. La solution dépend
de la taille actuelle du badge HA. La fonction de récupération du flux est
publique dans HA 2026.11.2 et privée dans 2026.7.1 ; les deux noms sont pris
en charge, mais seule la version épinglée a été exécutée.

Tests hors ligne : proxy caméra HA 2026.11.2, TurboJPEG, JPEG synthétiques
160 × 90, 90 × 160 et 120 × 120, proportions, coins, badge circulaire,
captures non carrées, absence d'image et JPEG invalide. Aucun affichage HA
réel, aucune caméra ni aucun service de production n'ont été contactés.
