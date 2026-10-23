"""Réglages communs : chemins, zones surveillées, détroits et séries de marché."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_SRC = ROOT / "site"

# Fichiers de données publiés avec le site (dans <sortie>/data/)
FICHIERS = {
    "petroliers": "tankers.json",
    "registre": "tanker_registry.json",
    "zones": "zones.json",
    "detroits": "chokepoints.json",
    "marches": "markets.json",
    "analyse": "analysis.json",
    "indicateurs": "kpis.json",
    "etat": "status.json",
}

# Durée d'écoute du flux AIS à chaque passage (minutes)
AIS_MINUTES = float(os.environ.get("AIS_MINUTES", "15"))
# Un pétrolier non revu depuis ce délai disparaît de la carte (heures)
AIS_RETENTION_HEURES = float(os.environ.get("AIS_RETENTION_HEURES", "36"))
# Fréquence de régénération de l'analyse (heures)
ANALYSE_TOUTES_LES_HEURES = float(os.environ.get("ANALYSE_TOUTES_LES_HEURES", "6"))
# PortWatch ne publie qu'une fois par semaine : inutile de tout retélécharger à chaque heure
PORTWATCH_TOUTES_LES_HEURES = float(os.environ.get("PORTWATCH_TOUTES_LES_HEURES", "12"))
# Historique des comptages par zone conservé (jours)
ZONES_HISTORIQUE_JOURS = 45

# Codes AIS « type de navire » 80 à 89 : navires-citernes (pétrole brut, produits, chimie, gaz)
TYPES_CITERNE = range(80, 90)
LIBELLES_TYPE = {
    80: "Navire-citerne",
    81: "Navire-citerne, cargaison dangereuse A",
    82: "Navire-citerne, cargaison dangereuse B",
    83: "Navire-citerne, cargaison dangereuse C",
    84: "Navire-citerne, cargaison dangereuse D",
    89: "Navire-citerne, autre",
}


def libelle_type(code: int | None) -> str:
    if code is None:
        return "Navire-citerne"
    return LIBELLES_TYPE.get(code, "Navire-citerne")


# Zones suivies sur la carte : (lat_min, lon_min, lat_max, lon_max)
ZONES = [
    {"id": "golfe_persique", "nom": "Golfe persique", "bbox": (23.5, 47.5, 30.6, 56.3)},
    {"id": "ormuz_oman", "nom": "Ormuz et golfe d'Oman", "bbox": (21.5, 56.3, 27.2, 61.5)},
    {"id": "mer_rouge", "nom": "Mer Rouge et golfe d'Aden", "bbox": (11.0, 32.3, 28.5, 51.5)},
    {"id": "med_orientale", "nom": "Méditerranée orientale et Suez", "bbox": (29.8, 22.0, 37.8, 36.5)},
    {"id": "mer_noire", "nom": "Mer Noire et Bosphore", "bbox": (40.8, 27.3, 47.5, 42.0)},
    {"id": "baltique", "nom": "Baltique", "bbox": (53.5, 9.5, 66.0, 30.5)},
    {"id": "mer_du_nord", "nom": "Mer du Nord et Manche", "bbox": (48.5, -6.0, 62.0, 9.5)},
    {"id": "golfe_mexique", "nom": "Golfe du Mexique", "bbox": (18.0, -98.0, 31.0, -80.5)},
    {"id": "afrique_ouest", "nom": "Golfe de Guinée", "bbox": (-8.0, -5.0, 8.0, 12.0)},
    {"id": "cap", "nom": "Cap de Bonne-Espérance", "bbox": (-38.0, 14.0, -30.0, 30.0)},
    {"id": "inde_ouest", "nom": "Inde, côte ouest", "bbox": (8.0, 66.0, 24.5, 77.0)},
    {"id": "malacca", "nom": "Malacca et Singapour", "bbox": (-1.0, 95.0, 7.0, 105.0)},
    {"id": "chine", "nom": "Côtes chinoises", "bbox": (18.0, 108.0, 41.0, 126.0)},
]

# Détroits suivis par IMF PortWatch (identifiant -> nom français, position, rôle pétrolier)
DETROITS = {
    "chokepoint6": {"nom": "Détroit d'Ormuz", "lat": 26.55, "lon": 56.35, "petrole": True},
    "chokepoint4": {"nom": "Bab el-Mandeb", "lat": 12.6, "lon": 43.35, "petrole": True},
    "chokepoint1": {"nom": "Canal de Suez", "lat": 30.6, "lon": 32.34, "petrole": True},
    "chokepoint7": {"nom": "Cap de Bonne-Espérance", "lat": -34.6, "lon": 18.4, "petrole": True},
    "chokepoint5": {"nom": "Détroit de Malacca", "lat": 2.5, "lon": 101.3, "petrole": True},
    "chokepoint3": {"nom": "Bosphore", "lat": 41.12, "lon": 29.07, "petrole": True},
    "chokepoint10": {"nom": "Øresund", "lat": 55.9, "lon": 12.68, "petrole": True},
    "chokepoint28": {"nom": "Détroit de Kertch", "lat": 45.3, "lon": 36.55, "petrole": True},
    "chokepoint8": {"nom": "Détroit de Gibraltar", "lat": 35.96, "lon": -5.6, "petrole": True},
    "chokepoint9": {"nom": "Pas de Calais", "lat": 51.0, "lon": 1.45, "petrole": True},
    "chokepoint2": {"nom": "Canal de Panama", "lat": 9.12, "lon": -79.73, "petrole": True},
    "chokepoint11": {"nom": "Détroit de Taïwan", "lat": 24.4, "lon": 119.6, "petrole": False},
    "chokepoint12": {"nom": "Détroit de Corée", "lat": 34.4, "lon": 129.0, "petrole": False},
    "chokepoint13": {"nom": "Détroit de Tsugaru", "lat": 41.55, "lon": 140.5, "petrole": False},
    "chokepoint14": {"nom": "Détroit de Luçon", "lat": 20.5, "lon": 121.3, "petrole": False},
    "chokepoint15": {"nom": "Détroit de Lombok", "lat": -8.75, "lon": 115.75, "petrole": False},
    "chokepoint16": {"nom": "Détroit d'Ombai", "lat": -8.45, "lon": 125.2, "petrole": False},
    "chokepoint17": {"nom": "Détroit de Bohai", "lat": 38.2, "lon": 120.9, "petrole": False},
    "chokepoint18": {"nom": "Détroit de Torres", "lat": -10.3, "lon": 142.2, "petrole": False},
    "chokepoint19": {"nom": "Détroit de la Sonde", "lat": -6.0, "lon": 105.8, "petrole": False},
    "chokepoint20": {"nom": "Détroit de Makassar", "lat": -2.5, "lon": 118.2, "petrole": False},
    "chokepoint21": {"nom": "Détroit de Magellan", "lat": -52.6, "lon": -69.8, "petrole": False},
    "chokepoint22": {"nom": "Canal du Yucatán", "lat": 21.8, "lon": -85.6, "petrole": False},
    "chokepoint23": {"nom": "Passage du Vent", "lat": 19.9, "lon": -73.9, "petrole": False},
    "chokepoint24": {"nom": "Passage de la Mona", "lat": 18.3, "lon": -67.8, "petrole": False},
    "chokepoint25": {"nom": "Détroit de Balabac", "lat": 7.7, "lon": 117.0, "petrole": False},
    "chokepoint26": {"nom": "Détroit de Béring", "lat": 65.8, "lon": -168.8, "petrole": False},
    "chokepoint27": {"nom": "Détroit de Mindoro", "lat": 12.6, "lon": 120.6, "petrole": False},
}

# Séries de marché : (id, libellé, symbole Yahoo, série FRED de secours, unité, type de variation)
# type de variation : "pct" = en %, "pb" = en points de base (pour les taux)
MARCHES = [
    {"id": "brent", "nom": "Brent", "yahoo": "BZ=F", "fred": "DCOILBRENTEU", "unite": "$/baril", "variation": "pct"},
    {"id": "wti", "nom": "WTI", "yahoo": "CL=F", "fred": "DCOILWTICO", "unite": "$/baril", "variation": "pct"},
    {"id": "gaz_eu", "nom": "Gaz européen (TTF)", "yahoo": "TTF=F", "fred": None, "unite": "€/MWh", "variation": "pct"},
    {"id": "sp500", "nom": "S&P 500", "yahoo": "^GSPC", "fred": "SP500", "unite": "points", "variation": "pct"},
    {"id": "vix", "nom": "VIX", "yahoo": "^VIX", "fred": "VIXCLS", "unite": "points", "variation": "pct"},
    {"id": "taux10", "nom": "Taux US à 10 ans", "yahoo": "^TNX", "fred": "DGS10", "unite": "%", "variation": "pb"},
    {"id": "dollar", "nom": "Dollar (DXY)", "yahoo": "DX-Y.NYB", "fred": None, "unite": "points", "variation": "pct"},
    {"id": "eurusd", "nom": "EUR/USD", "yahoo": "EURUSD=X", "fred": "DEXUSEU", "unite": "$ pour 1 €", "variation": "pct"},
    {"id": "or", "nom": "Or", "yahoo": "GC=F", "fred": None, "unite": "$/once", "variation": "pct"},
]

# Délai réseau par requête (secondes)
TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (compatible; vigie-petrole/1.0; +https://github.com)"
