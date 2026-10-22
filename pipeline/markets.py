"""Prix et indicateurs de marché (Yahoo Finance, FRED en secours) et stocks américains (EIA)."""

from __future__ import annotations

import csv
import io
import logging
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config
from .util import arrondi, ecrire_json, iso, maintenant, masquer

log = logging.getLogger(__name__)

URL_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbole}"
URL_FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
URL_EIA = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
SERIE_EIA = "WCESTUS1"  # stocks commerciaux de brut aux États-Unis, hors réserve stratégique (milliers de barils)


def lire_yahoo(donnees: dict) -> list[tuple[str, float]]:
    """Transforme la réponse « chart » de Yahoo en liste (date, clôture)."""
    resultat = ((donnees.get("chart") or {}).get("result") or [None])[0]
    if not resultat:
        return []
    horodatages = resultat.get("timestamp") or []
    clotures = (((resultat.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    decalage = (resultat.get("meta") or {}).get("gmtoffset") or 0
    points: dict[str, float] = {}
    for t, c in zip(horodatages, clotures):
        if c is None or (isinstance(c, float) and math.isnan(c)):
            continue
        jour = datetime.fromtimestamp(t + decalage, tz=timezone.utc).date().isoformat()
        points[jour] = float(c)
    return sorted(points.items())


def lire_fred(texte: str) -> list[tuple[str, float]]:
    """CSV FRED : première colonne la date, deuxième la valeur (« . » quand elle manque)."""
    points = []
    lecteur = csv.reader(io.StringIO(texte))
    next(lecteur, None)
    for ligne in lecteur:
        if len(ligne) < 2 or ligne[1] in ("", "."):
            continue
        try:
            points.append((ligne[0][:10], float(ligne[1])))
        except ValueError:
            continue
    return points


def telecharger_yahoo(symbole: str, session: requests.Session) -> list[tuple[str, float]]:
    reponse = session.get(
        URL_YAHOO.format(symbole=requests.utils.quote(symbole, safe="")),
        params={"range": "1y", "interval": "1d"},
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.TIMEOUT,
    )
    reponse.raise_for_status()
    return lire_yahoo(reponse.json())


def telecharger_fred(serie: str, session: requests.Session) -> list[tuple[str, float]]:
    debut = (date.today() - timedelta(days=380)).isoformat()
    reponse = session.get(
        URL_FRED, params={"id": serie, "cosd": debut}, headers={"User-Agent": config.USER_AGENT}, timeout=config.TIMEOUT
    )
    reponse.raise_for_status()
    return lire_fred(reponse.text)


def _variation(points: list[tuple[str, float]], jours_bourse: int, mode: str) -> float | None:
    if len(points) <= jours_bourse:
        return None
    avant, apres = points[-1 - jours_bourse][1], points[-1][1]
    if mode == "pb":
        return round((apres - avant) * 100)
    return round((apres / avant - 1) * 100, 2) if avant else None


def volatilite_annualisee(points: list[tuple[str, float]], jours: int = 30) -> float | None:
    valeurs = [v for _, v in points[-(jours + 1):] if v and v > 0]
    if len(valeurs) < 10:
        return None
    rendements = [math.log(b / a) for a, b in zip(valeurs, valeurs[1:])]
    m = sum(rendements) / len(rendements)
    variance = sum((r - m) ** 2 for r in rendements) / (len(rendements) - 1)
    return round(math.sqrt(variance) * math.sqrt(252) * 100, 1)


def resumer(serie_cfg: dict, points: list[tuple[str, float]], source: str) -> dict:
    mode = serie_cfg["variation"]
    an = [v for _, v in points[-252:]]
    return {
        "id": serie_cfg["id"],
        "nom": serie_cfg["nom"],
        "unite": serie_cfg["unite"],
        "source": source,
        "variation_en": "points de base" if mode == "pb" else "%",
        "derniere_date": points[-1][0] if points else None,
        "dernier": arrondi(points[-1][1], 2) if points else None,
        "var_1j": _variation(points, 1, mode),
        "var_5j": _variation(points, 5, mode),
        "var_1m": _variation(points, 21, mode),  # environ un mois de bourse (21 séances)
        "plus_haut_1an": arrondi(max(an), 2) if an else None,
        "plus_bas_1an": arrondi(min(an), 2) if an else None,
        "volatilite_30j_pct": volatilite_annualisee(points) if serie_cfg["id"] in ("brent", "wti") else None,
        "points": [[d, arrondi(v, 3)] for d, v in points[-260:]],
    }


def lire_eia(donnees: dict) -> list[tuple[str, float]]:
    lignes = ((donnees.get("response") or {}).get("data")) or []
    points = []
    for ligne in lignes:
        try:
            points.append((str(ligne["period"])[:10], float(ligne["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(points)


def telecharger_eia(cle: str, session: requests.Session) -> list[tuple[str, float]]:
    params = {
        "api_key": cle,
        "frequency": "weekly",
        "data[0]": "value",
        "facets[series][]": SERIE_EIA,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": 0,
        "length": 60,
    }
    reponse = session.get(URL_EIA, params=params, timeout=config.TIMEOUT)
    reponse.raise_for_status()
    return lire_eia(reponse.json())


def resumer_eia(points: list[tuple[str, float]]) -> dict:
    if len(points) < 2:
        return {}
    dernier, precedent = points[-1], points[-2]
    meme_semaine_an = points[-53] if len(points) >= 53 else None
    return {
        "nom": "Stocks commerciaux de brut aux États-Unis (hors réserve stratégique)",
        "unite": "milliers de barils",
        "source": "EIA, série WCESTUS1",
        "semaine": dernier[0],
        "niveau": round(dernier[1]),
        "variation_semaine": round(dernier[1] - precedent[1]),
        "variation_vs_an_dernier": round(dernier[1] - meme_semaine_an[1]) if meme_semaine_an else None,
        "points": [[d, round(v)] for d, v in points[-52:]],
    }


def actualiser(data_dir: Path, session: requests.Session | None = None) -> dict:
    session = session or requests.Session()
    series, erreurs = [], []
    for cfg in config.MARCHES:
        points, source = [], ""
        try:
            points, source = telecharger_yahoo(cfg["yahoo"], session), "Yahoo Finance"
        except Exception as exc:
            erreurs.append(masquer(f"{cfg['nom']} (Yahoo) : {exc}"))
        if not points and cfg.get("fred"):
            try:
                points, source = telecharger_fred(cfg["fred"], session), f"FRED ({cfg['fred']})"
            except Exception as exc:
                erreurs.append(masquer(f"{cfg['nom']} (FRED) : {exc}"))
        if points:
            series.append(resumer(cfg, points, source))

    stocks = {}
    cle_eia = os.environ.get("EIA_API_KEY", "").strip()
    if cle_eia:
        try:
            stocks = resumer_eia(telecharger_eia(cle_eia, session))
        except Exception as exc:
            erreurs.append(masquer(f"EIA : {exc}"))

    ecrire_json(
        data_dir / config.FICHIERS["marches"],
        {"genere_le": iso(maintenant()), "series": series, "stocks_us": stocks, "erreurs": erreurs},
    )
    log.info("Marchés : %d séries, %d erreurs", len(series), len(erreurs))
    return {"series": len(series), "erreurs": erreurs}
