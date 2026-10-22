"""Récupère les données du passage précédent depuis le site déjà publié.

GitHub Actions repart d'une machine vierge à chaque exécution. Plutôt que d'enregistrer les
données dans le dépôt (l'historique Git gonflerait de plusieurs Mo par jour), on relit les
fichiers JSON que le site publie déjà : le site sert de mémoire d'un passage à l'autre.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from . import config
from .util import ecrire_json

log = logging.getLogger(__name__)


def url_du_site() -> str | None:
    url = os.environ.get("SITE_URL", "").strip()
    if url:
        return url.rstrip("/") + "/"
    depot = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in depot:
        return None
    proprietaire, nom = depot.split("/", 1)
    proprietaire = proprietaire.lower()
    if nom.lower() == f"{proprietaire}.github.io":
        return f"https://{proprietaire}.github.io/"
    return f"https://{proprietaire}.github.io/{nom}/"


def restaurer(data_dir: Path, url: str, session: requests.Session | None = None) -> list[str]:
    session = session or requests.Session()
    recuperes = []
    for cle, nom in config.FICHIERS.items():
        if cle == "etat":
            continue
        try:
            reponse = session.get(f"{url}data/{nom}", timeout=config.TIMEOUT, headers={"Cache-Control": "no-cache"})
            if reponse.status_code != 200:
                continue
            ecrire_json(data_dir / nom, reponse.json())
            recuperes.append(nom)
        except (requests.RequestException, ValueError) as exc:
            log.warning("Impossible de récupérer %s : %s", nom, exc)
    log.info("État précédent récupéré : %s", ", ".join(recuperes) or "aucun fichier (premier passage)")
    return recuperes
