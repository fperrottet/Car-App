"""Point d'entrée : `python -m app` (dans le conteneur) ou en local."""

import os

import uvicorn

from .main import create_app

if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8099")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
