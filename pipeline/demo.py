"""Données fictives pour voir le site sans aucune clé (python -m pipeline.run --demo).

Les pétroliers sont placés le long des grandes routes pétrolières, les séries PortWatch et les
cours sont simulés. Une anomalie volontaire (baisse à Bab el-Mandeb, détour par le Cap) permet
de voir à quoi ressemble l'analyse quand un signal apparaît. Rien de tout cela n'est réel.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from pathlib import Path

from . import config, portwatch
from .collect_ais import compter_zones
from .markets import resumer
from .util import ecrire_json, iso, maintenant

# Routes maritimes (lat, lon), points choisis en mer
ROUTES = {
    "golfe_asie": [(26.7, 50.4), (26.3, 52.5), (26.5, 56.4), (25.0, 58.0), (22.0, 61.5), (15.0, 66.0), (10.0, 72.0),
                   (5.8, 80.5), (6.0, 90.0), (5.5, 95.5), (3.5, 99.5), (2.0, 102.0), (1.2, 104.1), (5.0, 107.5),
                   (10.0, 111.0), (16.0, 114.0), (22.0, 118.0), (29.0, 123.0)],
    "golfe_europe": [(26.5, 56.4), (25.0, 58.0), (22.5, 60.5), (14.5, 52.0), (12.5, 45.0), (12.6, 43.3), (15.5, 41.5),
                     (20.0, 38.5), (24.0, 36.0), (27.5, 33.8), (29.9, 32.55), (31.5, 32.3), (33.0, 28.0), (34.5, 22.0),
                     (36.5, 15.5), (37.6, 11.0), (38.0, 5.0), (36.3, -2.5), (35.95, -5.6), (36.0, -9.5), (43.5, -10.0),
                     (48.0, -6.0), (50.0, -1.5), (51.0, 1.5), (52.0, 3.8)],
    "baltique": [(60.2, 28.5), (59.8, 26.0), (59.3, 21.5), (57.5, 19.8), (55.6, 15.0), (54.7, 12.6), (55.3, 10.9),
                 (57.0, 11.3), (57.9, 10.8), (58.0, 9.5), (56.5, 5.0), (53.5, 3.0)],
    "mer_noire": [(44.6, 37.9), (43.5, 35.0), (42.0, 30.0), (41.15, 29.1), (40.8, 28.3), (40.2, 26.4), (39.5, 25.6),
                  (37.0, 25.0), (36.0, 24.0), (34.5, 22.0)],
    "us_europe": [(29.2, -94.5), (27.0, -90.0), (24.5, -84.0), (24.2, -81.5), (25.5, -79.7), (30.0, -78.0), (37.0, -60.0),
                  (45.0, -30.0), (48.0, -10.0), (49.5, -4.0), (50.0, -1.5)],
    "afrique_asie": [(4.0, 7.0), (2.0, 5.0), (-10.0, 5.0), (-25.0, 10.0), (-35.2, 18.5), (-36.0, 25.0), (-30.0, 40.0),
                     (-15.0, 60.0), (0.0, 80.0), (6.0, 90.0)],
    "cap_detour": [(12.0, 52.0), (5.0, 55.0), (-10.0, 50.0), (-25.0, 40.0), (-35.5, 25.0), (-35.0, 15.0), (-20.0, 5.0),
                   (0.0, -12.0), (20.0, -20.0), (36.0, -10.0)],
    "golfe_inde": [(25.0, 58.0), (22.5, 63.0), (21.5, 67.5), (22.4, 69.0)],
    "asie_nord": [(29.0, 123.0), (33.0, 126.0), (34.8, 129.4), (35.3, 129.5)],
}
POIDS_ROUTES = {"golfe_asie": 5, "golfe_europe": 2.2, "baltique": 1.3, "mer_noire": 1.1, "us_europe": 1.6,
                "afrique_asie": 1.4, "cap_detour": 1.6, "golfe_inde": 0.6, "asie_nord": 0.5}

# Terminaux et mouillages : navires à l'arrêt
PORTS = [(26.65, 50.25, 40), (25.2, 56.45, 45), (29.65, 48.85, 18), (29.2, 50.35, 16), (1.2, 104.15, 60),
         (52.0, 3.95, 30), (29.15, -94.45, 30), (29.85, 122.45, 30), (35.9, 120.55, 20), (60.25, 28.65, 15),
         (44.6, 37.95, 14), (22.6, 69.4, 18), (35.35, 129.5, 14), (27.75, -97.0, 14), (4.2, 7.1, 16)]

PREFIXES = ["NORDIC", "AEGEAN", "OCEAN", "SEA", "PACIFIC", "ATLANTIC", "GULF", "STAR", "CAPE", "PEARL", "SILVER",
            "BLUE", "EASTERN", "ROYAL", "GOLDEN", "ARCTIC", "CORAL", "IONIAN", "BALTIC", "DESERT"]
SUFFIXES = ["KESTREL", "DAWN", "SPIRIT", "VOYAGER", "HORIZON", "MERIDIAN", "PIONEER", "LEGEND", "GRACE", "FALCON",
            "ORION", "AURORA", "TIDE", "HARMONY", "SPLENDOUR", "BREEZE", "GLORY", "CROWN", "VENTURE", "WAVE"]
DESTINATIONS = ["FUJAIRAH", "SINGAPORE", "ROTTERDAM", "NINGBO", "QINGDAO", "SIKKA", "ULSAN", "HOUSTON", "AUGUSTA",
                "TRIESTE", "GDANSK", "PRIMORSK", "RAS TANURA", "BASRAH", "YOSU", "FOR ORDERS"]

# Moyenne de passages de pétroliers par jour (ordre de grandeur plausible, fictif)
NIVEAUX_DETROITS = {"chokepoint6": 38, "chokepoint4": 14, "chokepoint1": 18, "chokepoint7": 22, "chokepoint5": 62,
                    "chokepoint3": 13, "chokepoint10": 21, "chokepoint28": 7, "chokepoint8": 30, "chokepoint9": 41,
                    "chokepoint2": 11}


def _interpoler(a, b, t):
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def _cap(a, b):
    dlat = b[0] - a[0]
    dlon = (b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return (math.degrees(math.atan2(dlon, dlat)) + 360) % 360


def _longueur(route):
    return sum(math.dist(route[i], route[i + 1]) for i in range(len(route) - 1))


def petroliers_fictifs(rng: random.Random, instant) -> list[dict]:
    navires = []
    mmsi = 200000000

    def ajouter(lat, lon, cap, vitesse, age_min):
        nonlocal mmsi
        mmsi += rng.randint(1, 900)
        navires.append({
            "mmsi": str(mmsi),
            "nom": f"{rng.choice(PREFIXES)} {rng.choice(SUFFIXES)}",
            "lat": round(lat, 4), "lon": round(lon, 4), "cap": int(cap) % 360,
            "vitesse": round(vitesse, 1), "statut": 0 if vitesse > 0.5 else 1,
            "vu": iso(instant - timedelta(minutes=age_min)),
            "type": rng.choice([80, 80, 80, 81, 82, 83, 84, 89]),
            "imo": rng.randint(9100000, 9899999),
            "destination": rng.choice(DESTINATIONS),
            "tirant_eau": round(rng.uniform(7, 21), 1),
            "longueur": rng.choice([183, 228, 250, 274, 333]),
        })

    total_poids = sum(POIDS_ROUTES[r] * _longueur(ROUTES[r]) for r in ROUTES)
    for nom, route in ROUTES.items():
        part = POIDS_ROUTES[nom] * _longueur(route) / total_poids
        for _ in range(int(1300 * part)):
            i = rng.randrange(len(route) - 1)
            a, b = route[i], route[i + 1]
            lat, lon = _interpoler(a, b, rng.random())
            lat += rng.gauss(0, 0.18)
            lon += rng.gauss(0, 0.18)
            cap = _cap(a, b) if rng.random() < 0.55 else (_cap(a, b) + 180) % 360
            age = rng.choice([rng.randint(1, 50)] * 6 + [rng.randint(200, 900)])
            ajouter(lat, lon, cap + rng.gauss(0, 6), rng.uniform(10, 14.5), age)
    for lat, lon, n in PORTS:
        for _ in range(n):
            ajouter(lat + rng.gauss(0, 0.07), lon + rng.gauss(0, 0.07), rng.uniform(0, 359), rng.uniform(0, 0.3), rng.randint(1, 60))
    return navires


def lignes_portwatch(rng: random.Random, fin: date, jours: int = 420) -> list[dict]:
    lignes = []
    for ident in config.DETROITS:
        niveau = NIVEAUX_DETROITS.get(ident, rng.uniform(3, 25))
        for k in range(jours, -1, -1):
            jour = fin - timedelta(days=k)
            saison = 1 + 0.06 * math.sin(2 * math.pi * jour.timetuple().tm_yday / 365)
            facteur = 1.0
            if ident == "chokepoint4" and k <= 6:  # baisse simulée à Bab el-Mandeb
                facteur = 0.55
            if ident == "chokepoint7" and k <= 6:  # détour simulé par le Cap
                facteur = 1.3
            moyenne = niveau * saison * facteur
            n_tanker = max(0, round(rng.gauss(moyenne, math.sqrt(moyenne) * 0.6)))
            lignes.append({
                "date": jour.isoformat(), "portid": ident, "portname": ident,
                "n_tanker": n_tanker, "n_total": n_tanker + max(0, round(rng.gauss(moyenne * 1.6, 4))),
                "capacity_tanker": n_tanker * rng.uniform(45000, 95000),
            })
    return lignes


def series_marches(rng: random.Random, fin: date) -> list[dict]:
    """Marches aléatoires qui reviennent vers un niveau de référence (pour rester plausibles)."""
    depart = {"brent": 72.0, "gaz_eu": 36.0, "sp500": 5900.0, "vix": 16.0, "taux10": 4.2,
              "dollar": 101.0, "eurusd": 1.09, "or": 2650.0}
    vol = {"brent": 0.018, "gaz_eu": 0.028, "sp500": 0.009, "vix": 0.05, "taux10": 0.012,
           "dollar": 0.003, "eurusd": 0.003, "or": 0.008}
    jours = [fin - timedelta(days=k) for k in range(370, -1, -1)]
    jours = [j for j in jours if j.weekday() < 5]
    trajectoires = {}
    for ident, niveau in depart.items():
        x, valeurs = math.log(niveau), []
        for i in range(len(jours)):
            choc = 0.0
            if ident == "brent" and i >= len(jours) - 8:
                choc = 0.012
            if ident == "vix" and i >= len(jours) - 8:
                choc = 0.05
            if ident == "sp500" and i >= len(jours) - 8:
                choc = -0.003
            x += 0.03 * (math.log(niveau) - x) + choc + rng.gauss(0, vol[ident])
            valeurs.append(math.exp(x))
        trajectoires[ident] = valeurs
    ecart = 3.6
    trajectoires["wti"] = []
    for b in trajectoires["brent"]:
        ecart += 0.1 * (3.6 - ecart) + rng.gauss(0, 0.15)
        trajectoires["wti"].append(b - ecart)
    return [
        resumer(cfg, list(zip([j.isoformat() for j in jours], trajectoires[cfg["id"]])), "données fictives")
        for cfg in config.MARCHES
    ]


def generer(data_dir: Path, graine: int = 7) -> dict:
    rng = random.Random(graine)
    instant = maintenant()
    navires = petroliers_fictifs(rng, instant)
    ecrire_json(data_dir / config.FICHIERS["petroliers"], {
        "genere_le": iso(instant), "ecoute_minutes": 8, "messages_recus": 0,
        "nouvelles_positions": len(navires), "petroliers": navires, "demo": True,
    })
    ecrire_json(data_dir / config.FICHIERS["registre"], {})

    historique = []
    for h in range(24 * 8, -1, -1):
        t = instant - timedelta(hours=h)
        echantillon = [n for n in navires if rng.random() < 0.9 + 0.1 * math.sin(h / 9)]
        point = compter_zones([{**n, "vu": iso(t)} for n in echantillon], t)
        historique.append(point)
    ecrire_json(data_dir / config.FICHIERS["zones"], {
        "zones": [{"id": z["id"], "nom": z["nom"], "bbox": z["bbox"]} for z in config.ZONES],
        "historique": historique,
    })

    fin_pw = instant.date() - timedelta(days=5)  # PortWatch a quelques jours de retard
    ecrire_json(data_dir / config.FICHIERS["detroits"], portwatch.construire(lignes_portwatch(rng, fin_pw), instant))

    series = series_marches(rng, instant.date() - timedelta(days=1))
    semaines = [(instant.date() - timedelta(days=7 * k + 5)).isoformat() for k in range(60, -1, -1)]
    niveau, stocks = 425000.0, []
    for s in semaines:
        niveau += rng.gauss(-300, 3500)
        stocks.append([s, round(niveau)])
    stocks_us = {
        "nom": "Stocks commerciaux de brut aux États-Unis (hors réserve stratégique)",
        "unite": "milliers de barils", "source": "données fictives",
        "semaine": stocks[-1][0], "niveau": stocks[-1][1],
        "variation_semaine": stocks[-1][1] - stocks[-2][1],
        "variation_vs_an_dernier": stocks[-1][1] - stocks[-53][1], "points": stocks[-52:],
    }
    ecrire_json(data_dir / config.FICHIERS["marches"], {
        "genere_le": iso(instant), "series": series, "stocks_us": stocks_us, "erreurs": [], "demo": True,
    })
    return {"petroliers": len(navires)}
