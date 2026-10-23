# Car App : frais de voiture

Add-on Home Assistant pour suivre tous les frais d'une voiture électrique : dépenses, contrats d'assurance et charges fixes, carnet d'entretien et factures (PDF, photos). Le tableau de bord donne le coût total, par mois et par km, dépréciation comprise.

## Fonctionnalités (version 0.1)

- Fiche véhicule avec prix d'achat et dépréciation linéaire.
- Dépenses avec catégories (fixes ou variables), km au compteur et pièce jointe.
- Contrats (assurance, garantie, taxe...) : préavis, franchise, couverture ; un montant périodique crée automatiquement les échéances dans les dépenses.
- Carnet d'entretien avec facture et prochain service (date ou km).
- Documents : téléversement, recherche, téléchargement.
- Tableau de bord : coût total, CHF/mois, CHF/km, répartition par catégorie, rappels (fin de contrat, délai de résiliation, service).
- Export CSV des dépenses (séparateur `;`, s'ouvre directement dans Excel).

Prochaine étape : import automatique de l'odomètre (`sensor.216980_odometer`) depuis Home Assistant et des sessions de charge EVCC avec leur coût.

## Installation dans Home Assistant

Le dépôt est privé : la boutique d'add-ons ne peut pas le lire, on l'installe donc comme add-on local.

1. Installe l'add-on **Samba share** ou **Advanced SSH & Web Terminal** si ce n'est pas déjà fait.
2. Copie le dossier `car_costs` de ce dépôt dans le dossier `addons` de Home Assistant (`\\homeassistant\addons\car_costs` via Samba, ou `/addons/car_costs` en SSH).
3. Dans HA : **Paramètres > Modules complémentaires > Boutique**, menu ⋮ en haut à droite, **Rechercher des mises à jour**. L'add-on apparaît sous « Modules complémentaires locaux ».
4. Ouvre **Frais de voiture**, clique **Installer** (la construction prend quelques minutes sur un Pi), puis **Démarrer** et active **Afficher dans la barre latérale**.

Pour une mise à jour : recopie le dossier `car_costs`, puis clique **Reconstruire** sur la page de l'add-on.

Les données (base SQLite et documents) sont dans le dossier `/data` de l'add-on et sont incluses dans les sauvegardes de Home Assistant.

## Développement

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r car_costs/requirements.txt pytest httpx
pytest                                    # tests
cd car_costs && CAR_COSTS_DATA=../data python -m app   # http://localhost:8099
```
