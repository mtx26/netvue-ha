# Paramètres des caméras indisponibles

- Objectif : permettre la modification d'une caméra même si son entité n'est
  pas disponible, notamment après un changement d'adresse IP.
- Cause reproduite : le sélecteur dépendait des attributs des entités caméra ;
  une configuration sans entité correspondante était absente de la liste.
- Correction : le sélecteur utilise les configurations Home Assistant et
  associe les entités existantes pour les noms et l'aperçu des zones. Sans
  entité, l'absence d'aperçu est signalée. Les erreurs de chargement sont
  affichées avec une possibilité de réessayer.
- Tests : sélection et enregistrement simulés d'une caméra sans entité, échec
  de chargement et nouvelle tentative, déconnexion pendant le chargement,
  absence d'aperçu et suite frontend.
- Limites : validation sur l'installation réelle nécessaire. Le changement
  d'adresse exige toujours que le gestionnaire local puisse arrêter l'ancienne
  session ; aucune caméra réelle n'a été contactée.
