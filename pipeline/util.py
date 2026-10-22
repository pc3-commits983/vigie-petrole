"""Petites fonctions partagées : lecture/écriture JSON et dates."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRETS = ("AISSTREAM_API_KEY", "GEMINI_API_KEY", "EIA_API_KEY")


def masquer(texte) -> str:
    """Retire les clés d'un message d'erreur avant qu'il soit écrit dans un fichier publié.

    Les erreurs réseau de la bibliothèque requests contiennent l'adresse appelée, et l'API de
    l'EIA passe sa clé dans l'adresse : sans ce filtre, elle finirait sur le site public.
    """
    import os
    import re

    texte = str(texte)
    for nom in SECRETS:
        valeur = os.environ.get(nom, "").strip()
        if len(valeur) >= 6:
            texte = texte.replace(valeur, "***")
    return re.sub(r"(?i)\b(api_key|apikey|key|token)=[^&\s'\"]+", r"\1=***", texte)


def maintenant() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lire_iso(texte: str | None) -> datetime | None:
    if not texte:
        return None
    try:
        return datetime.fromisoformat(texte.replace("Z", "+00:00"))
    except ValueError:
        return None


def lire_json(chemin: Path, defaut: Any = None) -> Any:
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return defaut


def ecrire_json(chemin: Path, donnees: Any, compact: bool = True) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(chemin.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(donnees, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(donnees, f, ensure_ascii=False, indent=2)
    tmp.replace(chemin)


def moyenne(valeurs: list[float]) -> float | None:
    v = [x for x in valeurs if x is not None]
    return sum(v) / len(v) if v else None


def ecart_pct(valeur: float | None, reference: float | None) -> float | None:
    if valeur is None or reference in (None, 0):
        return None
    return round((valeur / reference - 1) * 100, 1)


def arrondi(x: float | None, n: int = 2) -> float | None:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(x, n)
