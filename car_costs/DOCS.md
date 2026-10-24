# Frais de voiture

Ouvre l'add-on depuis la barre latérale. Au premier lancement, renseigne la fiche du véhicule (prix et date d'achat pour la dépréciation), puis ajoute tes contrats et tes dépenses.

- **Contrats** : un contrat avec un montant et une périodicité crée tout seul ses échéances dans les dépenses. Pour une nouvelle prime au renouvellement, termine l'ancien contrat et crée-en un nouveau.
- **Coût par km** : calculé à partir des relevés du compteur (page Véhicule) et des km saisis dans les entretiens et dépenses.
- **Pneus** : saisis chaque achat (2 ou 4 pneus) ; en montant un jeu, ceux qui occupaient la même place sont démontés. Note la sculpture au démontage pour être averti avant qu'ils soient usés.
- **Économies** : renseigne sur la page Véhicule la consommation d'une voiture à essence équivalente et un prix de l'essence relevé près de chez toi ; les autres mois suivent l'indice suisse de l'OFEN.
- **Batterie** : les entités OVMS sont cherchées automatiquement ; la page Batterie liste celles trouvées, et tu peux en imposer une sur la page Véhicule.
- **Rappels** : la prochaine expertise se calcule depuis la 1re immatriculation (case B de la carte grise) ; ajoute ensuite chaque expertise passée.
- **Capteurs HA** : `sensor.frais_voiture_*` sont mis à jour à chaque synchronisation, utilisables dans les tableaux de bord et automatisations.
- **Carte Lovelace** : ajoute la ressource `/local/car-costs-card.js?v=0.5.0` (type Module JavaScript) puis la carte « Frais de voiture » (`type: custom:car-costs-card`). Détails dans le README du dépôt.
- **Sauvegarde** : la base et les documents sont dans `/data`, inclus dans les sauvegardes Home Assistant.
