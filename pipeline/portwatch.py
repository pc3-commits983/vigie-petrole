"""Passages quotidiens dans les détroits, publiés par IMF PortWatch (FMI, données AIS satellitaires).

Champs utilisés : date, portid, n_tanker (pétroliers/citernes), n_total, capacity_tanker
(tonnes transportées estimées). PortWatch publie chaque semaine avec quelques jours de décalage.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config
from .util import ecart_pct, ecrire_json, iso, maintenant, moyenne

log = logging.getLogger(__name__)

URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)
CHAMPS = "date,portid,portname,n_tanker,n_total,capacity_tanker"
JOURS_PUBLIES = 400  # assez pour comparer à la même période un an plus tôt


def _date(valeur) -> date | None:
    """La date arrive en texte « AAAA-MM-JJ » ou, selon le format du service, en millisecondes."""
    if isinstance(valeur, str):
        try:
            return date.fromisoformat(valeur[:10])
        except ValueError:
            return None
    if isinstance(valeur, (int, float)):
        return datetime.fromtimestamp(valeur / 1000, tz=timezone.utc).date()
    return None


def telecharger(depuis_annee: int, session: requests.Session | None = None, par_page: int = 2000) -> list[dict]:
    """Télécharge toutes les lignes depuis une année donnée, page par page."""
    session = session or requests.Session()
    lignes: list[dict] = []
    decalage = 0
    for _ in range(100):  # garde-fou
        params = {
            "where": f"year >= {depuis_annee}",
            "outFields": CHAMPS,
            "orderByFields": "ObjectId ASC",
            "resultOffset": decalage,
            "resultRecordCount": par_page,
            "f": "json",
        }
        reponse = session.get(URL, params=params, timeout=config.TIMEOUT, headers={"User-Agent": config.USER_AGENT})
        reponse.raise_for_status()
        donnees = reponse.json()
        if "error" in donnees:
            raise RuntimeError(f"PortWatch a répondu une erreur : {donnees['error']}")
        page = [f.get("attributes", {}) for f in donnees.get("features", [])]
        lignes.extend(page)
        if not page or not donnees.get("exceededTransferLimit"):
            break
        decalage += len(page)
    return lignes


def series_par_detroit(lignes: list[dict]) -> dict[str, list[tuple[date, int, int, float]]]:
    """Regroupe les lignes par détroit : liste triée de (date, pétroliers, total, capacité)."""
    series: dict[str, dict[date, tuple]] = {}
    for ligne in lignes:
        jour = _date(ligne.get("date"))
        ident = ligne.get("portid")
        if not jour or not ident:
            continue
        series.setdefault(ident, {})[jour] = (
            jour,
            int(ligne.get("n_tanker") or 0),
            int(ligne.get("n_total") or 0),
            float(ligne.get("capacity_tanker") or 0),
        )
    return {ident: [jours[j] for j in sorted(jours)] for ident, jours in series.items()}


def statistiques(serie: list[tuple[date, int, int, float]]) -> dict:
    """Compare les 7 derniers jours publiés aux 4 semaines précédentes et à l'an dernier."""
    if not serie:
        return {}
    par_jour = {j: (t, n, c) for j, t, n, c in serie}
    dernier = serie[-1][0]

    def fenetre(fin: date, jours: int, indice: int) -> list[float]:
        return [par_jour[d][indice] for k in range(jours) if (d := fin - timedelta(days=k)) in par_jour]

    p7 = moyenne(fenetre(dernier, 7, 0))
    p28 = moyenne(fenetre(dernier - timedelta(days=7), 28, 0))
    p7_an = moyenne(fenetre(dernier - timedelta(days=364), 7, 0))
    p365 = moyenne(fenetre(dernier, 365, 0))
    c7 = moyenne(fenetre(dernier, 7, 2))
    c28 = moyenne(fenetre(dernier - timedelta(days=7), 28, 2))
    n7 = moyenne(fenetre(dernier, 7, 1))

    ecart = ecart_pct(p7, p28)
    if ecart is None:
        niveau = "inconnu"
    elif ecart <= -35:
        niveau = "forte baisse"
    elif ecart <= -15:
        niveau = "en baisse"
    elif ecart >= 35:
        niveau = "forte hausse"
    elif ecart >= 15:
        niveau = "en hausse"
    else:
        niveau = "normal"

    return {
        "derniere_date": dernier.isoformat(),
        "petroliers_7j": round(p7, 1) if p7 is not None else None,
        "petroliers_4sem_avant": round(p28, 1) if p28 is not None else None,
        "petroliers_meme_periode_an_dernier": round(p7_an, 1) if p7_an is not None else None,
        "petroliers_moyenne_1an": round(p365, 1) if p365 is not None else None,
        "ecart_vs_4sem_pct": ecart,
        "ecart_vs_an_dernier_pct": ecart_pct(p7, p7_an),
        "capacite_7j_tonnes": round(c7) if c7 is not None else None,
        "ecart_capacite_vs_4sem_pct": ecart_pct(c7, c28),
        "navires_tous_types_7j": round(n7, 1) if n7 is not None else None,
        "niveau": niveau,
    }


def construire(lignes: list[dict], instant: datetime | None = None) -> dict:
    """Prépare chokepoints.json : séries quotidiennes compactes + statistiques par détroit."""
    instant = instant or maintenant()
    series = series_par_detroit(lignes)
    points = []
    for ident, infos in config.DETROITS.items():
        serie = series.get(ident, [])[-JOURS_PUBLIES:]
        points.append(
            {
                "id": ident,
                "nom": infos["nom"],
                "lat": infos["lat"],
                "lon": infos["lon"],
                "petrole": infos["petrole"],
                "stats": statistiques(serie),
                "serie": {
                    "dates": [j.isoformat() for j, *_ in serie],
                    "petroliers": [t for _, t, _, _ in serie],
                    "total": [n for _, _, n, _ in serie],
                    "capacite": [round(c) for _, _, _, c in serie],
                },
            }
        )
    dates = [p["stats"].get("derniere_date") for p in points if p["stats"].get("derniere_date")]
    return {
        "genere_le": iso(instant),
        "source": "IMF PortWatch (portwatch.imf.org), données AIS du UN Global Platform",
        "derniere_date": max(dates) if dates else None,
        "detroits": points,
    }


def actualiser(data_dir: Path, session: requests.Session | None = None) -> dict:
    annee = (maintenant() - timedelta(days=JOURS_PUBLIES)).year
    lignes = telecharger(annee, session=session)
    log.info("PortWatch : %d lignes téléchargées", len(lignes))
    contenu = construire(lignes)
    ecrire_json(data_dir / config.FICHIERS["detroits"], contenu)
    return {"lignes": len(lignes), "derniere_date": contenu["derniere_date"]}
