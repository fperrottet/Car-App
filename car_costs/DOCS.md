# Frais de voiture

Ouvre l'add-on depuis la barre latérale. Au premier lancement, renseigne la fiche du véhicule (prix et date d'achat pour la dépréciation), puis ajoute tes contrats et tes dépenses.

- **Plusieurs voitures** : page **Voitures** > « Ajouter une voiture ». Choisis sa motorisation (électrique, essence, diesel, hybride) et si elle est reliée à Home Assistant. Le menu en haut de page change de voiture ; chaque voiture a ses dépenses, contrats, pneus, rappels et documents. Une voiture vendue s'archive et garde son historique.
- **Carburant** : pour une voiture thermique ou hybride, note chaque plein (litres, montant, km) sur la page Carburant ; la consommation en l/100 km se calcule entre deux pleins complets.
- **Contrats** : un contrat avec un montant et une périodicité crée tout seul ses échéances dans les dépenses. Pour une nouvelle prime au renouvellement, termine l'ancien contrat et crée-en un nouveau.
- **Coût par km** : calculé à partir des relevés du compteur (page Véhicule) et des km saisis dans les entretiens et dépenses.
- **Pneus** : saisis chaque achat (2 ou 4 pneus) ; il est découpé en paires. Sur la frise, glisse une paire du garage sur l'avant ou l'arrière (au téléphone : touche la paire puis l'essieu), tire les poignées pour corriger une date, touche une période pour noter la sculpture, la démonter ou éliminer la paire. Les jeux saisis avant la 0.7 sont repris automatiquement (un jeu de 4 : paire A à l'avant, paire B à l'arrière).
- **Économies** : renseigne sur la page Véhicule la consommation d'une voiture à essence équivalente et un prix de l'essence relevé près de chez toi ; les autres mois suivent l'indice suisse de l'OFEN.
- **Batterie** : les entités OVMS sont cherchées automatiquement ; la page Batterie liste celles trouvées, et tu peux en imposer une sur la page Véhicule.
- **Rappels** : la prochaine expertise se calcule depuis la 1re immatriculation (case B de la carte grise) ; ajoute ensuite chaque expertise passée.
- **Capteurs HA** : `sensor.frais_voiture_*` sont mis à jour à chaque synchronisation, utilisables dans les tableaux de bord et automatisations. La première voiture garde ce nom, les suivantes ajoutent le leur (`sensor.frais_voiture_vw_golf_*`, préfixe affiché sur la page Véhicule).
- **Carte Lovelace** : ajoute la ressource `/local/car-costs-card.js?v=0.6.0` (type Module JavaScript) puis la carte « Frais de voiture » (`type: custom:car-costs-card`). Détails dans le README du dépôt.
- **Sauvegarde** : la base et les documents sont dans `/data`, inclus dans les sauvegardes Home Assistant.
