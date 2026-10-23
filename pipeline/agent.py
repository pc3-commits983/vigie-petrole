"""Agent : Gemini choisit les indicateurs clés du jour et écrit une ligne pour chacun.

Les chiffres sont calculés par le code (pipeline/kpi.py). Le modèle ne fait que choisir, par
leur identifiant, les 4 à 8 indicateurs qui comptent le plus pour les marchés aujourd'hui, et
expliquer chacun en une ligne. L'interface affiche toujours les valeurs calculées, jamais des
chiffres recopiés par le modèle.

La boucle est écrite à la main avec l'API REST de Gemini (pas de SDK) : le modèle demande un
outil, on l'exécute, on lui renvoie le résultat, jusqu'à sa réponse finale en JSON. Sans clé
Gemini, ou si l'appel échoue, une sélection par règles fixes prend le relais.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import requests

from . import config
from .tools import DECLARATIONS, Outils, charger_litterature, executer
from .util import ecrire_json, iso, lire_iso, lire_json, maintenant, masquer

log = logging.getLogger(__name__)

URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta"
# gemini-2.5-flash n'est plus ouvert aux nouveaux comptes (constaté le 22/10/2026)
MODELES_PAR_DEFAUT = ["gemini-flash-latest", "gemini-flash-lite-latest"]
# Erreurs passagères (quota, surcharge) : on réessaie, puis on passe au modèle suivant
STATUTS_PASSAGERS = {429, 500, 502, 503, 504}
ATTENTES_SECONDES = (15, 45)
ETAPES_MAX = 12
FORMAT = 2

CONSIGNES = """Tu es l'analyste de « Vigie pétrole ». L'application affiche des cartes d'indicateurs chiffrés.
Ton travail : choisir les indicateurs les plus importants aujourd'hui pour les marchés financiers dans leur
ensemble (actions, taux, devises, matières premières, inflation) et expliquer chacun en une ligne.

Méthode :
1. Appelle indicateurs_cles. Chaque indicateur a un identifiant, une valeur, des variations, parfois un état,
   et un score d'importance calculé par des règles.
2. Si c'est utile, appelle etat_des_detroits, serie_detroit, petroliers_par_zone ou marches pour le contexte,
   et chercher_litterature pour appuyer tes explications.
3. Choisis de 4 à 8 indicateurs, du plus au moins important pour les marchés. Tu peux t'écarter du score si
   le contexte le justifie : un détroit pétrolier majeur quasi à l'arrêt passe avant une petite variation.

Réponds uniquement par un objet JSON, sans texte autour, de cette forme :
{"titre": "...", "indicateurs": [{"id": "...", "pourquoi": "...", "reference": null}], "a_surveiller": ["..."]}

Règles :
- titre : 10 mots maximum, résume la situation du jour, sans chiffre.
- id : exactement un identifiant renvoyé par indicateurs_cles.
- pourquoi : 15 mots maximum, sans aucun chiffre (les valeurs sont déjà affichées). Dis pourquoi cela compte
  pour les marchés : offre, prix, inflation, taux, actions, devises.
- reference : la forme de citation exacte d'un article renvoyé par chercher_litterature si l'explication
  s'appuie dessus, sinon null.
- a_surveiller : 2 ou 3 signaux précis à guetter, 12 mots maximum chacun.
- N'invente aucun fait, aucun événement, aucune cause : appuie-toi seulement sur les outils.
- Aucune recommandation d'achat ou de vente."""


class ErreurAgent(RuntimeError):
    def __init__(self, message: str, statut: int | None = None):
        super().__init__(message)
        self.statut = statut

    @property
    def essayer_un_autre_modele(self) -> bool:
        """Modèle introuvable ou surchargé : un autre modèle peut réussir. Clé refusée : inutile."""
        return self.statut is None or self.statut == 404 or self.statut in STATUTS_PASSAGERS


# --- Appel à Gemini ----------------------------------------------------------------------------


def appeler_gemini(session: requests.Session, base: str, modele: str, cle: str, corps: dict) -> dict:
    url = f"{base}/models/{modele}:generateContent"
    for essai in range(len(ATTENTES_SECONDES) + 1):
        try:
            reponse = session.post(url, json=corps, headers={"x-goog-api-key": cle}, timeout=120)
        except requests.RequestException as exc:
            raise ErreurAgent(f"Gemini injoignable : {exc}") from exc
        if reponse.status_code in STATUTS_PASSAGERS and essai < len(ATTENTES_SECONDES):
            attente = ATTENTES_SECONDES[essai]
            log.warning("Gemini %s a répondu %s, nouvel essai dans %s s", modele, reponse.status_code, attente)
            time.sleep(attente)
            continue
        if reponse.status_code >= 400:
            raise ErreurAgent(f"Gemini ({modele}) a répondu {reponse.status_code} : {reponse.text[:300]}", reponse.status_code)
        return reponse.json()
    raise ErreurAgent("Gemini indisponible")


def boucle_agent(outils: Outils, cle: str, modele: str, base: str = URL_GEMINI, session=None) -> str:
    """Fait tourner la boucle outil par outil et renvoie le texte final du modèle."""
    session = session or requests.Session()
    jour = maintenant().strftime("%d/%m/%Y")
    contenus = [{"role": "user", "parts": [{"text": f"Choisis les indicateurs clés du {jour}. Commence par appeler indicateurs_cles."}]}]
    corps_base = {
        "systemInstruction": {"parts": [{"text": CONSIGNES}]},
        "tools": [{"functionDeclarations": DECLARATIONS}],
        "generationConfig": {"temperature": 0.3},
    }
    for etape in range(ETAPES_MAX + 1):
        corps = {**corps_base, "contents": contenus}
        if etape == ETAPES_MAX:  # dernière étape : on interdit les outils pour obtenir le texte
            corps["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        reponse = appeler_gemini(session, base, modele, cle, corps)
        candidats = reponse.get("candidates") or []
        if not candidats:
            raise ErreurAgent(f"Réponse vide de Gemini : {str(reponse)[:300]}")
        contenu = candidats[0].get("content") or {}
        parties = contenu.get("parts") or []
        appels = [p["functionCall"] for p in parties if "functionCall" in p]
        if appels:
            # On renvoie au modèle son propre message tel quel (y compris ses signatures de réflexion)
            contenus.append(contenu)
            reponses = []
            for appel in appels:
                resultat = executer(outils, appel.get("name", ""), appel.get("args"))
                log.info("Outil appelé : %s", appel.get("name"))
                reponses.append({"functionResponse": {"name": appel.get("name", ""), "response": {"resultat": resultat}}})
            contenus.append({"role": "user", "parts": reponses})
            continue
        texte = "".join(p.get("text", "") for p in parties if not p.get("thought")).strip()
        if not texte:
            raise ErreurAgent(f"Gemini n'a renvoyé ni texte ni appel d'outil (motif : {candidats[0].get('finishReason')})")
        return texte
    raise ErreurAgent("Trop d'étapes sans texte final")


# --- Lecture et contrôle de la réponse ----------------------------------------------------------


def _mots(texte: str, maximum: int) -> str:
    mots = str(texte or "").split()
    return " ".join(mots[:maximum]).rstrip(",;:") + ("…" if len(mots) > maximum else "")


def extraire_json(texte: str) -> dict:
    """Le modèle entoure parfois son JSON de ```json ... ``` : on prend le premier objet complet."""
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut < 0 or fin <= debut:
        raise ErreurAgent("La réponse ne contient pas de JSON")
    try:
        return json.loads(texte[debut : fin + 1])
    except json.JSONDecodeError as exc:
        raise ErreurAgent(f"JSON illisible : {exc}") from exc


def valider(reponse: dict, indicateurs: list[dict]) -> tuple[dict, dict]:
    """Garde seulement les identifiants connus et les références de la bibliothèque."""
    connus = {i["id"] for i in indicateurs}
    citations = {a["citation"]: a for a in charger_litterature()["articles"]}
    selection, ids_inconnus, citations_inconnues, avec_chiffres = [], [], [], 0
    for item in reponse.get("indicateurs") or []:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id", ""))
        if ident not in connus:
            ids_inconnus.append(ident)
            continue
        if any(s["id"] == ident for s in selection):
            continue
        pourquoi = _mots(item.get("pourquoi", ""), 20)
        avec_chiffres += bool(re.search(r"\d", pourquoi))
        reference = item.get("reference")
        if reference and reference not in citations:
            citations_inconnues.append(str(reference))
            reference = None
        selection.append({"id": ident, "pourquoi": pourquoi, "reference": reference or None})
    selection = selection[:8]
    if len(selection) < 3:
        raise ErreurAgent(f"Sélection inutilisable ({len(selection)} indicateur(s) valide(s))")
    resultat = {
        "titre": _mots(reponse.get("titre", ""), 12) or "Indicateurs clés du jour",
        "selection": selection,
        "a_surveiller": [_mots(x, 16) for x in (reponse.get("a_surveiller") or []) if isinstance(x, str) and x.strip()][:3],
    }
    controle = {
        "indicateurs_valides": len(selection),
        "ids_inconnus": ids_inconnus,
        "citations_inconnues": citations_inconnues,
        "explications_avec_chiffres": avec_chiffres,
    }
    return resultat, controle


# --- Sélection de secours par règles fixes ----------------------------------------------------

EXPLICATIONS = {
    ("marche:brent", 1): ("Un pétrole plus cher pèse sur l'inflation et les marges des entreprises.", "Coibion et Gorodnichenko, 2015"),
    ("marche:brent", -1): ("Un pétrole moins cher allège l'inflation et les coûts des entreprises.", None),
    ("marche:wti", 1): ("Le brut américain monte avec le reste du marché pétrolier.", None),
    ("marche:wti", -1): ("Le brut américain recule avec le reste du marché pétrolier.", None),
    ("marche:gaz_eu", 1): ("Énergie plus chère pour l'industrie et les ménages européens.", None),
    ("marche:gaz_eu", -1): ("Détente sur la facture énergétique européenne.", None),
    ("marche:sp500", 1): ("Les actions progressent : le marché ne voit pas de choc durable.", None),
    ("marche:sp500", -1): ("Les actions reculent : l'appétit pour le risque diminue.", "Kilian et Park, 2009"),
    ("marche:vix", 1): ("Les investisseurs paient plus cher pour se protéger d'une baisse.", None),
    ("marche:vix", -1): ("La nervosité des marchés actions retombe.", None),
    ("marche:taux10", 1): ("Des taux plus hauts renchérissent le crédit et pèsent sur les actions.", None),
    ("marche:taux10", -1): ("Les taux baissent : le marché anticipe une détente ou un ralentissement.", None),
    ("marche:dollar", 1): ("Un dollar fort renchérit le pétrole pour les pays importateurs.", None),
    ("marche:dollar", -1): ("Un dollar plus faible allège la facture pétrolière hors États-Unis.", None),
    ("marche:eurusd", 1): ("Un euro plus fort amortit la facture énergétique européenne.", None),
    ("marche:eurusd", -1): ("Un euro plus faible renchérit le pétrole importé en Europe.", None),
    ("marche:or", 1): ("L'or monte : les investisseurs cherchent une valeur refuge.", None),
    ("marche:or", -1): ("L'or recule : moins de recherche de valeur refuge.", None),
    ("marche:ecart_brent_wti", 1): ("Le Brent se tend plus que le brut américain : routes maritimes sous pression.", None),
    ("marche:ecart_brent_wti", -1): ("L'écart se resserre : moins de tension propre aux routes maritimes.", None),
    ("stocks:us", 1): ("Les stocks américains montent : marché pétrolier moins tendu.", None),
    ("stocks:us", -1): ("Les stocks américains baissent : marché pétrolier plus tendu.", None),
}


def _sens(indicateur: dict) -> int:
    variation = indicateur["variations"][indicateur["principale"]]["valeur"] or 0
    return 1 if variation >= 0 else -1


def expliquer(indicateur: dict) -> tuple[str, str | None]:
    sens, etat = _sens(indicateur), indicateur.get("etat")
    if indicateur["id"].startswith("detroit:"):
        if etat == "quasi à l'arrêt":
            return "Route pétrolière quasi fermée : risque majeur sur l'offre mondiale.", "EIA, World Oil Transit Chokepoints"
        if etat == "reprise":
            return "Le trafic reprend : la prime de risque sur le pétrole pourrait reculer.", "Kilian, 2009"
        if indicateur["id"] == "detroit:chokepoint7" and sens > 0:
            return "Détour par l'Afrique : trajets plus longs, fret plus cher.", "Carrière-Swallow et al., 2023"
        if sens < 0:
            return "Moins de pétrole transite : tension possible sur l'offre.", "Kilian, 2009"
        return "Trafic en hausse : flux réorientés ou reprise des échanges.", None
    if indicateur["id"].startswith("zone:"):
        if etat == "congestion":
            return "Beaucoup de pétroliers à l'arrêt : attente, stockage ou congestion.", None
        return "La présence de pétroliers varie fortement dans cette zone.", None
    return EXPLICATIONS.get((indicateur["id"], sens), ("Variation notable de cet indicateur.", None))


def libelle_court(indicateur: dict) -> str:
    if indicateur.get("etat"):
        return f"{indicateur['titre']} {indicateur['etat']}"
    return f"{indicateur['titre']} en {'hausse' if _sens(indicateur) > 0 else 'baisse'}"


def selection_par_regles(indicateurs: list[dict]) -> dict:
    choisis, detroits = [], 0
    for i in sorted(indicateurs, key=lambda x: -x["score"]):
        if i["score"] < 0.4 or len(choisis) >= 6:
            continue
        if i["id"].startswith("detroit:"):
            if detroits >= 3:
                continue
            detroits += 1
        choisis.append(i)
    if not any(i["id"] == "marche:brent" for i in choisis):
        brent = next((i for i in indicateurs if i["id"] == "marche:brent"), None)
        if brent:
            choisis = choisis[:5] + [brent]
    selection = []
    for i in choisis:
        pourquoi, reference = expliquer(i)
        selection.append({"id": i["id"], "pourquoi": pourquoi, "reference": reference})
    titre = ", ".join(libelle_court(i) for i in choisis[:2]) or "Pas de signal marquant aujourd'hui"
    a_surveiller = []
    for i in choisis:
        if i["id"].startswith("detroit:") and len(a_surveiller) < 2:
            a_surveiller.append(f"{i['titre']} : confirmation dans la prochaine publication PortWatch")
    a_surveiller.append("VIX : une hausse durable signalerait une contagion aux actions")
    return {"titre": titre, "selection": selection, "a_surveiller": a_surveiller[:3]}


# --- Point d'entrée -----------------------------------------------------------------------------


def references(selection: list[dict]) -> list[str]:
    articles = {a["citation"]: a["reference"] for a in charger_litterature()["articles"]}
    vues = []
    for s in selection:
        ref = s.get("reference")
        if ref in articles and articles[ref] not in vues:
            vues.append(articles[ref])
    return vues


def dates_des_donnees(outils: Outils) -> dict:
    lire = outils._lire
    return {
        "portwatch": lire("detroits").get("derniere_date"),
        "positions_ais": lire("petroliers").get("genere_le"),
        "marches": lire("marches").get("genere_le"),
        "indicateurs": lire("indicateurs").get("genere_le"),
    }


def produire_analyse(data_dir: Path, cle: str | None = None, modeles: list[str] | None = None, base: str = URL_GEMINI) -> dict:
    outils = Outils(data_dir)
    indicateurs = outils._lire("indicateurs").get("indicateurs", [])
    cle = (cle if cle is not None else os.environ.get("GEMINI_API_KEY", "")).strip()
    modeles = modeles or ([os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else MODELES_PAR_DEFAUT)
    resultat, controle, mode, modele_utilise, erreur = None, {}, "regles", None, None
    incidents: list[str] = []

    if cle and indicateurs:
        for modele in modeles:
            try:
                texte = boucle_agent(outils, cle, modele, base=base)
                resultat, controle = valider(extraire_json(texte), indicateurs)
                mode, modele_utilise = "ia", modele
                break
            except ErreurAgent as exc:
                erreur = str(exc)
                incidents.append(masquer(erreur)[:400])
                log.warning("Agent Gemini (%s) en échec : %s", modele, exc)
                outils.appels.clear()
                if not exc.essayer_un_autre_modele:  # clé refusée ou requête invalide
                    break

    if resultat is None:
        resultat = selection_par_regles(indicateurs)
        controle = {"indicateurs_valides": len(resultat["selection"]), "ids_inconnus": [], "citations_inconnues": [], "explications_avec_chiffres": 0}

    analyse = {
        "format": FORMAT,
        "genere_le": iso(maintenant()),
        "mode": mode,
        "modele": modele_utilise or "règles fixes (sans IA)",
        **resultat,
        "references": references(resultat["selection"]),
        "controle": controle,
        "outils_appeles": [a["outil"] for a in outils.appels],
        "dates_des_donnees": dates_des_donnees(outils),
        "erreur_ia": masquer(erreur) if erreur and mode == "regles" else None,
        "incidents_ia": incidents,
    }
    ecrire_json(data_dir / config.FICHIERS["analyse"], analyse)
    return analyse


def analyse_perimee(data_dir: Path, heures: float) -> bool:
    analyse = lire_json(data_dir / config.FICHIERS["analyse"], {}) or {}
    genere = lire_iso(analyse.get("genere_le"))
    if genere is None or analyse.get("format") != FORMAT:
        return True
    return (maintenant() - genere).total_seconds() > heures * 3600
