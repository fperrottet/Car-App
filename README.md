# Car App : frais de voiture

Add-on Home Assistant pour suivre tous les frais d'une voiture électrique : dépenses, contrats d'assurance et charges fixes, carnet d'entretien et factures (PDF, photos). Le tableau de bord donne le coût total, par mois et par km, dépréciation comprise.

## Fonctionnalités

- Fiche véhicule avec prix d'achat et dépréciation linéaire.
- Dépenses avec catégories (fixes ou variables), km au compteur et pièce jointe.
- Contrats (assurance, garantie, taxe...) : préavis, franchise, couverture ; un montant périodique crée automatiquement les échéances dans les dépenses.
- Carnet d'entretien avec facture et prochain service (date ou km).
- Documents : téléversement, recherche, téléchargement.
- Tableau de bord : coût total, CHF/mois, CHF/km, répartition par catégorie, rappels (fin de contrat, délai de résiliation, service).
- Export CSV des dépenses (séparateur `;`, s'ouvre directement dans Excel).
- Import automatique, toutes les heures et à la demande :
  - odomètre depuis Home Assistant (entité réglable, par défaut `sensor.216980_odometer`), un relevé par jour ;
  - sessions de charge depuis l'API d'EVCC (`/api/sessions`), avec le coût calculé par EVCC tel quel.
- Page Recharges : sessions par mois, kWh, part solaire, coût ; kWh/100 km et CHF/100 km au tableau de bord.
- Recharges d'avant EVCC estimées jour par jour : km (répartis entre deux relevés) × consommation relevée × 1/0,9 de pertes × prix du kWh à cette date (tarifs saisis sur la page Recharges, sinon prix moyen d'EVCC). Désactivable sur la page Véhicule.
- Import CSV des relevés du compteur (Date ; Km ; kWh/100 km) et des dépenses (même format que l'export).
- Graphique : coût par km cumulé et km parcourus par mois.
- Pneus : jeux de 2 ou 4 pneus (saison, marque, dimension, indice, DOT), montages et démontages, km parcourus par jeu, sculpture mesurée ; rappel du changement été/hiver et des pneus usés.
- Économies face à une voiture à essence : km du mois × consommation × prix de l'essence (ton prix de référence, suivi mois par mois par l'indice des prix de l'essence de l'OFEN), moins l'électricité payée.
- Santé de la batterie : SoH global et SoH de chaque cellule lus chaque jour depuis OVMS via Home Assistant (métriques `xvu.b.soh.charge`, `xvu.b.c.soh`, `xvu.b.hist.soh.mod.*`, recherche automatique des entités), courbe dans le temps et cellule la plus faible.
- Rappels administratifs : expertise calculée depuis la 1re immatriculation (Fribourg : 5 ans, puis 3, puis tous les 2 ans), rappels libres ou périodiques (vignette, garanties, impôt).
- Capteurs envoyés à Home Assistant à chaque synchronisation : `sensor.frais_voiture_mois`, `_annee`, `_par_km`, `_economie_essence`, `_prochain_rappel`, `_cellule_min`. Ils sont recréés à chaque synchronisation (donc au plus une heure après un redémarrage de HA).

Réglages sur la page **Véhicule** : entité de l'odomètre, adresse d'EVCC (par exemple `http://192.168.1.20:7070`) et, si plusieurs voitures partagent la borne, le nom du véhicule dans EVCC ; date de 1re immatriculation, consommation et prix de l'essence de référence, entités de la batterie si la recherche automatique ne les trouve pas.

## Installation dans Home Assistant

1. Dans HA : **Paramètres > Modules complémentaires > Boutique des modules complémentaires**.
2. Menu ⋮ en haut à droite > **Dépôts**, ajoute `https://github.com/fperrottet/Car-App` et ferme la fenêtre.
3. Cherche **Frais de voiture** dans la boutique, clique **Installer** (la construction prend quelques minutes sur un Pi).
4. Clique **Démarrer** et active **Afficher dans la barre latérale**.

Mises à jour : HA propose une mise à jour quand la `version` de `car_costs/config.yaml` change sur la branche `main` (menu ⋮ > **Rechercher des mises à jour** pour forcer la vérification).

Les données (base SQLite et documents) sont dans le dossier `/data` de l'add-on et sont incluses dans les sauvegardes de Home Assistant.

## Développement

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r car_costs/requirements.txt pytest httpx
pytest                                    # tests
cd car_costs && CAR_COSTS_DATA=../data python -m app   # http://localhost:8099
```
