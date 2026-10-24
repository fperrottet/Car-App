"""Point d'entrée : `python -m app` (dans le conteneur) ou en local."""

import logging
import os
import shutil
from pathlib import Path

import uvicorn

from .main import HERE, create_app

# Dossier de configuration de HA, monté par l'add-on (map: homeassistant_config).
HA_CONFIG = Path(os.environ.get("HA_CONFIG_DIR", "/homeassistant"))


def install_card() -> None:
    """Copie la carte Lovelace dans /config/www (servie par HA sous /local/)."""
    if not HA_CONFIG.is_dir():
        return
    target = HA_CONFIG / "www" / "car-costs-card.js"
    try:
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(HERE / "static" / "car-costs-card.js", target)
    except OSError:
        logging.getLogger("car_costs").exception("Copie de la carte Lovelace impossible")


if __name__ == "__main__":
    install_card()
    uvicorn.run(
        create_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8099")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
