"""Indicateurs clés : tous les chiffres affichés sont calculés ici, jamais par le modèle.

L'agent choisit ensuite les indicateurs les plus importants du jour (par leur identifiant) et
écrit une ligne d'explication pour chacun. Ainsi un chiffre faux ne peut pas venir de l'IA.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from . import config
from .util import arrondi, ecart_pct, ecrire_json, iso, lire_iso, lire_json, maintenant, moyenne

# Détroits dont une perturbation compte le plus pour le pétrole
DETROITS_MAJEURS = {"chokepoint6": 1.4, "chokepoint4": 1.25, "chokepoint1": 1.25, "chokepoint5": 1.15, "chokepoint7": 1.1}

# Variation sur un mois considérée comme forte, par série (en %, ou en points de base pour le taux)
SEUILS_MARCHE = {"brent": 8, "wti": 8, "gaz_eu": 15, "sp500": 4, "vix": 25, "taux10": 30, "dollar": 2.5, "eurusd": 2.5, "or": 5}
HORIZONS = [("var_1j", "1 j", 0.3), ("var_5j", "5 j", 0.6), ("var_1m", "1 mois", 1.0)]


def _moyenne_fenetre(jours: list[date], valeurs: list[float], fin: date, nb: int) -> float | None:
    debut = fin - timedelta(days=nb - 1)
    return moyenne([v for j, v in zip(jours, valeurs) if debut <= j <= fin])


# En dessous de ce trafic moyen (navires-citernes par jour sur un an), les variations sont surtout
# du bruit statistique : 1 navire de plus ou de moins fait varier le chiffre de 50 %.
TRAFIC_MIN_FIABLE = 5


def etat_detroit(p7: float | None, p7_prec: float | None, p365: float | None) -> str | None:
    """Qualifie le niveau de passage par rapport à la moyenne sur un an."""
    if p7 is None or not p365 or p365 < TRAFIC_MIN_FIABLE:
        return None
    ratio = p7 / p365
    if p7_prec is not None and p7_prec / p365 <= 0.2 and p7 >= 2 * max(p7_prec, 0.5) and ratio >= 0.25:
        return "reprise"
    if ratio <= 0.15:
        return "quasi à l'arrêt"
    if ratio <= 0.6:
        return "ralenti"
    if ratio >= 1.5:
        return "inhabituellement actif"
    return None


def indicateurs_detroits(donnees: dict) -> list[dict]:
    resultat = []
    for d in donnees.get("detroits", []):
        serie = d.get("serie") or {}
        if not serie.get("dates"):
            continue
        jours = [date.fromisoformat(j) for j in serie["dates"]]
        valeurs = serie["petroliers"]
        fin = jours[-1]
        p7 = _moyenne_fenetre(jours, valeurs, fin, 7)
        p7_prec = _moyenne_fenetre(jours, valeurs, fin - timedelta(days=7), 7)
        p28 = _moyenne_fenetre(jours, valeurs, fin - timedelta(days=7), 28)
        p7_an = _moyenne_fenetre(jours, valeurs, fin - timedelta(days=364), 7)
        p365 = _moyenne_fenetre(jours, valeurs, fin, 365)
        variations = [
            {"libelle": "1 sem.", "valeur": ecart_pct(p7, p7_prec), "unite": "%"},
            {"libelle": "4 sem.", "valeur": ecart_pct(p7, p28), "unite": "%"},
            {"libelle": "1 an", "valeur": ecart_pct(p7, p7_an), "unite": "%"},
        ]
        normes = [abs(v["valeur"] or 0) / s for v, s in zip(variations, (30, 25, 40))]
        poids = DETROITS_MAJEURS.get(d["id"], 1.0 if d.get("petrole") else 0.5)
        if not p365 or p365 < TRAFIC_MIN_FIABLE:
            poids *= 0.25
        etat = etat_detroit(p7, p7_prec, p365)
        bonus = 1.5 if etat in ("quasi à l'arrêt", "reprise") and d["id"] in DETROITS_MAJEURS else 0
        resultat.append({
            "id": f"detroit:{d['id']}",
            "categorie": "Détroit",
            "titre": d["nom"],
            "valeur": arrondi(p7, 1),
            "unite": "navires-citernes / j visibles à l'AIS",
            "variations": variations,
            "principale": max(range(3), key=lambda i: normes[i]),
            "etat": etat,
            "score": round((max(normes) + bonus) * poids, 2),
            "date": fin.isoformat(),
            "source": "IMF PortWatch",
            "carte": [d["lat"], d["lon"]],
        })
    return resultat


def indicateurs_marches(donnees: dict) -> list[dict]:
    resultat = []
    for s in donnees.get("series", []):
        if s.get("dernier") is None:
            continue
        seuil = SEUILS_MARCHE.get(s["id"], 5)
        unite_var = "pb" if s.get("variation_en") == "points de base" else "%"
        variations = [{"libelle": libelle, "valeur": s.get(cle), "unite": unite_var} for cle, libelle, _ in HORIZONS]
        normes = [abs(s.get(cle) or 0) / (seuil * facteur) for cle, _, facteur in HORIZONS]
        resultat.append({
            "id": f"marche:{s['id']}",
            "categorie": "Marché",
            "titre": s["nom"],
            "valeur": s["dernier"],
            "unite": s["unite"],
            "variations": variations,
            "principale": max(range(3), key=lambda i: normes[i]),
            "etat": None,
            "score": round(max(normes) * (1.2 if s["id"] in ("brent", "vix") else 1.0), 2),
            "date": s.get("derniere_date"),
            "source": s.get("source"),
        })
    return resultat


def indicateur_ecart_brent_wti(donnees: dict) -> list[dict]:
    series = {s["id"]: s for s in donnees.get("series", [])}
    brent, wti = series.get("brent"), series.get("wti")
    if not brent or not wti or not brent.get("points") or not wti.get("points"):
        return []
    wti_par_jour = dict(map(tuple, wti["points"]))
    ecarts = [(j, b - wti_par_jour[j]) for j, b in brent["points"] if j in wti_par_jour and b is not None and wti_par_jour[j] is not None]
    if len(ecarts) < 22:
        return []
    actuel, il_y_a_1m = ecarts[-1][1], ecarts[-22][1]
    variation = round(actuel - il_y_a_1m, 2)
    return [{
        "id": "marche:ecart_brent_wti",
        "categorie": "Marché",
        "titre": "Écart Brent – WTI",
        "valeur": round(actuel, 2),
        "unite": "$/baril",
        "variations": [{"libelle": "1 mois", "valeur": variation, "unite": "$"}],
        "principale": 0,
        "etat": None,
        "score": round(abs(variation) / 2.5, 2),
        "date": ecarts[-1][0],
        "source": brent.get("source"),
    }]


def indicateurs_zones(donnees: dict) -> list[dict]:
    historique = donnees.get("historique", [])
    if not historique:
        return []
    dernier = historique[-1]
    noms = {z["id"]: z for z in donnees.get("zones", [])}
    t = dernier["t"]
    cible = lire_iso(t) - timedelta(days=7)
    proches = [h for h in historique[:-1] if abs((lire_iso(h["t"]) - cible).total_seconds()) <= 6 * 3600]
    il_y_a_7j = min(proches, key=lambda h: abs((lire_iso(h["t"]) - cible).total_seconds())) if proches else None
    resultat = []
    for ident, (total, arret) in dernier["z"].items():
        if total < 15:
            continue
        avant_total, avant_arret = (il_y_a_7j["z"].get(ident) or [None, None]) if il_y_a_7j else (None, None)
        variation = ecart_pct(total, avant_total)
        part = round(100 * arret / total)
        # Les ports et mouillages sont toujours pleins de navires à l'arrêt : on ne parle de
        # congestion que si la part à l'arrêt a nettement augmenté en une semaine.
        part_avant = round(100 * avant_arret / avant_total) if avant_total else None
        congestion = part_avant is not None and part - part_avant >= 15
        zone = noms.get(ident, {})
        bbox = zone.get("bbox")
        resultat.append({
            "id": f"zone:{ident}",
            "categorie": "Zone AIS",
            "titre": zone.get("nom", ident),
            "valeur": total,
            "unite": f"navires-citernes, {part} % à l'arrêt",
            "variations": [{"libelle": "7 j", "valeur": variation, "unite": "%"}],
            "principale": 0,
            "etat": "congestion" if congestion else None,
            "score": round(0.5 * abs(variation or 0) / 40 + (0.8 if congestion else 0), 2),
            "date": t,
            "source": "aisstream.io",
            "carte": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2] if bbox else None,
        })
    return resultat


def indicateur_stocks(donnees: dict) -> list[dict]:
    stocks = donnees.get("stocks_us") or {}
    if not stocks.get("niveau"):
        return []
    variation = stocks.get("variation_semaine") or 0
    return [{
        "id": "stocks:us",
        "categorie": "Stocks",
        "titre": "Stocks de brut américains",
        "valeur": round(stocks["niveau"] / 1000, 1),
        "unite": "millions de barils",
        "variations": [{"libelle": "1 sem.", "valeur": round(variation / 1000, 1), "unite": "M"}],
        "principale": 0,
        "etat": None,
        "score": round(abs(variation) / 5000, 2),
        "date": stocks.get("semaine"),
        "source": stocks.get("source"),
    }]


def construire(data_dir: Path) -> dict:
    lire = lambda cle: lire_json(data_dir / config.FICHIERS[cle], {}) or {}
    marches = lire("marches")
    indicateurs = (
        indicateurs_detroits(lire("detroits"))
        + indicateurs_marches(marches)
        + indicateur_ecart_brent_wti(marches)
        + indicateurs_zones(lire("zones"))
        + indicateur_stocks(marches)
    )
    indicateurs.sort(key=lambda i: -i["score"])
    contenu = {"genere_le": iso(maintenant()), "indicateurs": indicateurs}
    ecrire_json(data_dir / config.FICHIERS["indicateurs"], contenu)
    return {"indicateurs": len(indicateurs)}
