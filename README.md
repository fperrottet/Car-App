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
- Graphique : coût par km cumulé et km parcourus par mois.

Réglages sur la page **Véhicule** : entité de l'odomètre, adresse d'EVCC (par exemple `http://192.168.1.20:7070`) et, si plusieurs voitures partagent la borne, le nom du véhicule dans EVCC.

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
