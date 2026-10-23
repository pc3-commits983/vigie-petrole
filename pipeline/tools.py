"""Outils de l'agent : uniquement de la lecture des fichiers de données déjà collectés.

Chaque outil renvoie un petit dictionnaire JSON. L'agent ne peut ni écrire, ni appeler
Internet, ni exécuter de code : il ne voit le monde qu'à travers ces sept fonctions.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from . import config
from .util import ecart_pct, lire_iso, lire_json

FICHIER_LITTERATURE = Path(__file__).with_name("litterature.json")


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(c) != "Mn")


def charger_litterature() -> dict:
    with open(FICHIER_LITTERATURE, encoding="utf-8") as f:
        return json.load(f)


class Outils:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.appels: list[dict] = []

    def _lire(self, cle: str) -> dict:
        return lire_json(self.data_dir / config.FICHIERS[cle], {}) or {}

    # --- Indicateurs clés (calculés par pipeline/kpi.py) ------------------------------------
    def indicateurs_cles(self) -> dict:
        donnees = self._lire("indicateurs")
        champs = ("id", "categorie", "titre", "valeur", "unite", "variations", "etat", "score", "date")
        indicateurs = [{k: i.get(k) for k in champs} for i in donnees.get("indicateurs", [])]
        return {
            "lecture": "Trié par score d'importance décroissant (règles fixes). Variations en % sauf mention (pb = points de base).",
            "indicateurs": indicateurs[:40],
        }

    # --- Détroits -------------------------------------------------------------------------
    def etat_des_detroits(self) -> dict:
        donnees = self._lire("detroits")
        detroits = [
            {"nom": d["nom"], "detroit_petrolier_majeur": d["petrole"], **d.get("stats", {})}
            for d in donnees.get("detroits", [])
            if d.get("stats")
        ]
        detroits.sort(key=lambda d: (not d["detroit_petrolier_majeur"], -abs(d.get("ecart_vs_4sem_pct") or 0)))
        return {
            "source": donnees.get("source", "IMF PortWatch"),
            "derniere_date_publiee": donnees.get("derniere_date"),
            "lecture": "petroliers_7j = moyenne quotidienne de passages de navires-citernes sur les 7 derniers jours publiés ; "
            "ecart_vs_4sem_pct compare aux 4 semaines précédentes ; capacite en tonnes estimées par jour.",
            "avertissement": "PortWatch publie chaque semaine avec quelques jours de retard : ce ne sont pas des données du jour.",
            "detroits": detroits,
        }

    def _trouver_detroit(self, nom: str) -> dict | None:
        cible = _sans_accents(nom or "")
        for d in self._lire("detroits").get("detroits", []):
            if cible and (cible == d["id"] or cible in _sans_accents(d["nom"])):
                return d
        return None

    def serie_detroit(self, detroit: str, semaines: int = 12) -> dict:
        d = self._trouver_detroit(detroit)
        if not d:
            noms = [x["nom"] for x in self._lire("detroits").get("detroits", [])]
            return {"erreur": f"Détroit inconnu : {detroit}", "detroits_disponibles": noms}
        semaines = max(2, min(int(semaines or 12), 52))
        serie = d.get("serie", {})
        jours = [date.fromisoformat(j) for j in serie.get("dates", [])]
        if not jours:
            return {"nom": d["nom"], "semaines": []}
        fin = jours[-1]
        resultat = []
        for k in range(semaines - 1, -1, -1):
            debut_sem = fin - timedelta(days=7 * k + 6)
            fin_sem = fin - timedelta(days=7 * k)
            idx = [i for i, j in enumerate(jours) if debut_sem <= j <= fin_sem]
            if not idx:
                continue
            resultat.append(
                {
                    "semaine_finissant_le": fin_sem.isoformat(),
                    "petroliers_par_jour": round(sum(serie["petroliers"][i] for i in idx) / len(idx), 1),
                    "capacite_tonnes_par_jour": round(sum(serie["capacite"][i] for i in idx) / len(idx)),
                }
            )
        return {"nom": d["nom"], "source": "IMF PortWatch", "semaines": resultat}

    # --- Positions AIS --------------------------------------------------------------------
    def petroliers_par_zone(self) -> dict:
        zones = self._lire("zones")
        historique = zones.get("historique", [])
        if not historique:
            return {"erreur": "Pas encore de positions AIS collectées."}
        dernier = historique[-1]
        instant = lire_iso(dernier["t"])

        def proche(heures: float) -> dict | None:
            cible = instant - timedelta(hours=heures)
            candidats = [h for h in historique[:-1] if abs((lire_iso(h["t"]) - cible).total_seconds()) < 6 * 3600]
            return min(candidats, key=lambda h: abs((lire_iso(h["t"]) - cible).total_seconds())) if candidats else None

        il_y_a_1j, il_y_a_7j = proche(24), proche(24 * 7)
        noms = {z["id"]: z["nom"] for z in zones.get("zones", [])}
        lignes = []
        for ident, (total, arret) in dernier["z"].items():
            ligne = {
                "zone": noms.get(ident, ident),
                "petroliers": total,
                "dont_a_l_arret": arret,
                "part_a_l_arret_pct": round(100 * arret / total) if total else None,
            }
            if il_y_a_1j and ident in il_y_a_1j["z"]:
                ligne["ecart_vs_veille_pct"] = ecart_pct(total, il_y_a_1j["z"][ident][0])
            if il_y_a_7j and ident in il_y_a_7j["z"]:
                ligne["ecart_vs_7j_pct"] = ecart_pct(total, il_y_a_7j["z"][ident][0])
            lignes.append(ligne)
        lignes.sort(key=lambda x: -x["petroliers"])
        return {
            "source": "aisstream.io (antennes AIS, couverture surtout côtière)",
            "instant": dernier["t"],
            "petroliers_vus_6h_monde": dernier.get("n"),
            "avertissement": "Comptage d'un échantillon : les navires hors de portée des antennes ou qui coupent leur AIS n'apparaissent pas.",
            "zones": lignes,
        }

    # --- Marchés --------------------------------------------------------------------------
    def marches(self) -> dict:
        donnees = self._lire("marches")
        series = [{k: v for k, v in s.items() if k != "points"} for s in donnees.get("series", [])]
        par_id = {s["id"]: s for s in series}
        ecart = None
        if "brent" in par_id and "wti" in par_id and par_id["brent"]["dernier"] and par_id["wti"]["dernier"]:
            ecart = round(par_id["brent"]["dernier"] - par_id["wti"]["dernier"], 2)
        return {
            "genere_le": donnees.get("genere_le"),
            "lecture": "var_1j, var_5j et var_1m en % (en points de base pour les taux) ; volatilite_30j_pct annualisée.",
            "series": series,
            "ecart_brent_wti_dollars": ecart,
            "erreurs_de_collecte": donnees.get("erreurs", []),
        }

    def stocks_petrole_us(self) -> dict:
        stocks = self._lire("marches").get("stocks_us") or {}
        if not stocks:
            return {"erreur": "Stocks EIA non disponibles (clé EIA absente ou service indisponible)."}
        return {k: v for k, v in stocks.items() if k != "points"}

    # --- Littérature ----------------------------------------------------------------------
    def chercher_litterature(self, theme: str) -> dict:
        biblio = charger_litterature()
        mots = [m for m in _sans_accents(theme or "").replace(",", " ").split() if len(m) > 2]
        resultats = []
        for article in biblio["articles"]:
            texte = _sans_accents(" ".join(article["themes"]) + " " + article["resume"])
            score = sum(texte.count(m) for m in mots) + 3 * sum(
                1 for m in mots for t in article["themes"] if m in _sans_accents(t)
            )
            if score:
                resultats.append((score, article))
        resultats.sort(key=lambda x: -x[0])
        articles = [
            {k: a[k] for k in ("citation", "reference", "resume", "usage")} for _, a in resultats[:5]
        ]
        if not articles:
            themes = sorted({t for a in biblio["articles"] for t in a["themes"]})
            return {"articles": [], "themes_disponibles": themes}
        return {"articles": articles, "consigne": "Citer sous la forme (citation) exactement comme indiqué."}


DECLARATIONS = [
    {
        "name": "indicateurs_cles",
        "description": "Liste des indicateurs chiffrés disponibles (détroits, marchés, zones, stocks) avec leur identifiant, "
        "leur valeur, leurs variations, leur état éventuel et un score d'importance. À appeler en premier.",
    },
    {
        "name": "etat_des_detroits",
        "description": "Passages quotidiens de pétroliers dans les détroits (IMF PortWatch) : moyenne des 7 derniers jours publiés, "
        "comparaison aux 4 semaines précédentes et à la même période l'an dernier, tonnage estimé.",
    },
    {
        "name": "serie_detroit",
        "description": "Évolution semaine par semaine des passages de pétroliers dans un détroit donné.",
        "parameters": {
            "type": "object",
            "properties": {
                "detroit": {"type": "string", "description": "Nom du détroit, par exemple « Ormuz », « Suez », « Bab el-Mandeb »."},
                "semaines": {"type": "integer", "description": "Nombre de semaines (2 à 52, 12 par défaut)."},
            },
            "required": ["detroit"],
        },
    },
    {
        "name": "petroliers_par_zone",
        "description": "Nombre de pétroliers repérés par l'AIS dans chaque grande zone maritime et part à l'arrêt, comparés à la veille et à 7 jours.",
    },
    {
        "name": "marches",
        "description": "Derniers cours et variations : Brent, WTI, gaz européen, S&P 500, VIX, taux US à 10 ans, dollar, euro, or.",
    },
    {
        "name": "stocks_petrole_us",
        "description": "Stocks commerciaux hebdomadaires de pétrole brut aux États-Unis (EIA) et leur variation.",
    },
    {
        "name": "chercher_litterature",
        "description": "Cherche dans la bibliothèque d'articles scientifiques ceux qui portent sur un thème "
        "(exemples : « actions précaution », « inflation anticipations », « fret détour », « fiabilité AIS »).",
        "parameters": {
            "type": "object",
            "properties": {"theme": {"type": "string", "description": "Quelques mots-clés en français."}},
            "required": ["theme"],
        },
    },
]


def executer(outils: Outils, nom: str, arguments: dict | None) -> dict:
    """Exécute un outil demandé par le modèle, sans jamais lever d'exception."""
    fonction = getattr(outils, nom, None) if nom in {d["name"] for d in DECLARATIONS} else None
    arguments = arguments or {}
    if fonction is None:
        resultat = {"erreur": f"Outil inconnu : {nom}"}
    else:
        try:
            resultat = fonction(**arguments)
        except TypeError as exc:
            resultat = {"erreur": f"Arguments invalides pour {nom} : {exc}"}
        except Exception as exc:  # un outil ne doit jamais faire planter l'agent
            resultat = {"erreur": f"{nom} a échoué : {exc}"}
    outils.appels.append({"outil": nom, "arguments": arguments, "erreur": resultat.get("erreur")})
    return resultat
