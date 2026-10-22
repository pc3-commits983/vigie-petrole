"""Agent d'analyse : Gemini rédige l'analyse du jour en appelant les outils en lecture seule.

La boucle est écrite à la main avec l'API REST de Gemini (pas de SDK), pour que chaque étape
soit visible : le modèle demande un outil, on l'exécute, on lui renvoie le résultat, et ainsi
de suite jusqu'au texte final. Sans clé Gemini (ou si l'appel échoue), un rapport construit
par des règles fixes prend le relais, pour que l'application ait toujours une analyse.
"""

from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import requests

from . import config
from .tools import DECLARATIONS, Outils, charger_litterature, executer
from .util import ecrire_json, iso, maintenant, masquer

log = logging.getLogger(__name__)

URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta"
# gemini-2.5-flash n'est plus ouvert aux nouveaux comptes (constaté le 22/10/2026)
MODELES_PAR_DEFAUT = ["gemini-flash-latest", "gemini-flash-lite-latest"]
# Erreurs passagères (quota, surcharge) : on réessaie, puis on passe au modèle suivant
STATUTS_PASSAGERS = {429, 500, 502, 503, 504}
ATTENTES_SECONDES = (15, 45)
ETAPES_MAX = 14

SECTIONS = [
    "Flux dans les détroits",
    "Pétrole et prix",
    "Canaux vers les marchés",
    "Points à surveiller",
    "Limites des données",
]

CONSIGNES = """Tu es l'analyste de « Vigie pétrole », un tableau de bord qui suit les pétroliers dans le monde.
Chaque jour, tu rédiges une analyse approfondie des points clés du transport pétrolier qui peuvent affecter
les marchés financiers dans leur ensemble (actions, taux, devises, matières premières, inflation).

Méthode obligatoire :
1. Appelle d'abord etat_des_detroits, petroliers_par_zone, marches et stocks_petrole_us.
2. Pour les signaux importants, appelle serie_detroit si c'est utile, puis chercher_litterature avec des
   mots-clés adaptés (au moins trois recherches au total, sur des thèmes différents).
3. N'utilise que les chiffres renvoyés par les outils. N'invente aucun chiffre, aucun événement, aucune
   actualité, aucune cause. Si une donnée manque ou semble incohérente, dis-le.
4. Ne cite que des articles renvoyés par chercher_litterature, avec leur forme de citation exacte entre
   parenthèses, par exemple (Kilian, 2009).
5. Sépare clairement les faits (chiffres observés, datés) des interprétations (hypothèses appuyées sur la
   littérature, au conditionnel).
6. Aucune recommandation d'achat ou de vente.

Format : Markdown en français, 700 à 1 000 mots, avec exactement ces cinq titres de niveau 2, dans cet ordre :
## Flux dans les détroits
## Pétrole et prix
## Canaux vers les marchés
## Points à surveiller
## Limites des données
Commence directement par le premier titre. Date les données PortWatch. Dans « Points à surveiller », fais une
liste de 3 à 5 points, chacun avec le signal qui confirmerait ou infirmerait l'hypothèse. N'ajoute pas de
section Sources : elle est générée automatiquement."""


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
    contenus = [{"role": "user", "parts": [{"text": f"Rédige l'analyse du {jour}. Commence par consulter tes outils."}]}]
    corps_base = {
        "systemInstruction": {"parts": [{"text": CONSIGNES}]},
        "tools": [{"functionDeclarations": DECLARATIONS}],
        "generationConfig": {"temperature": 0.4},
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


# --- Contrôles ---------------------------------------------------------------------------------


def _normaliser(texte: str) -> str:
    texte = "".join(c for c in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texte).strip()


def controler(markdown: str) -> dict:
    """Vérifie les cinq titres et repère les citations absentes de la bibliothèque."""
    articles = charger_litterature()["articles"]
    connues = {_normaliser(a["citation"]): a for a in articles}
    titres = [m.strip() for m in re.findall(r"^##\s+(.+)$", markdown, flags=re.M)]
    sections_ok = [_normaliser(t) for t in titres[: len(SECTIONS)]] == [_normaliser(s) for s in SECTIONS]

    citees = [a for a in articles if _normaliser(a["citation"]) in _normaliser(markdown)]
    candidates = re.findall(r"\(([A-ZÀ-Ý][^()]{1,90}?,\s*(?:19|20)\d{2})\)", markdown)
    inconnues = []
    for c in candidates:
        morceaux = [m.strip() for m in c.split(";")]
        for m in morceaux:
            if _normaliser(m) not in connues and m not in inconnues:
                inconnues.append(m)
    return {"sections_ok": sections_ok, "citations_inconnues": inconnues, "articles_cites": [a["id"] for a in citees]}


def ajouter_sources(markdown: str, ids: list[str]) -> str:
    articles = {a["id"]: a for a in charger_litterature()["articles"]}
    lignes = [f"- {articles[i]['reference']}" for i in ids if i in articles]
    donnees = [
        "- IMF PortWatch, passages quotidiens dans les détroits (portwatch.imf.org)",
        "- aisstream.io, flux AIS des positions de navires",
        "- Yahoo Finance et FRED (Federal Reserve Bank of St. Louis), cours de marché",
        "- U.S. Energy Information Administration, stocks hebdomadaires de brut",
    ]
    bloc = "\n\n## Sources\n\n**Données**\n\n" + "\n".join(donnees)
    if lignes:
        bloc += "\n\n**Littérature citée**\n\n" + "\n".join(lignes)
    return markdown.rstrip() + bloc + "\n"


# --- Rapport de secours par règles fixes ------------------------------------------------------


def _pct(x: float | None, signe: bool = True) -> str:
    if x is None:
        return "n.d."
    texte = f"{x:+.1f}" if signe else f"{x:.1f}"
    return texte.replace(".", ",").replace("-", "−") + " %"


def _nombre(x: float | None, dec: int = 1) -> str:
    if x is None:
        return "n.d."
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def rapport_regles(outils: Outils) -> str:
    detroits = executer(outils, "etat_des_detroits", {})
    zones = executer(outils, "petroliers_par_zone", {})
    marches = executer(outils, "marches", {})
    stocks = executer(outils, "stocks_petrole_us", {})

    lignes_detroits = [d for d in detroits.get("detroits", []) if d.get("detroit_petrolier_majeur")]
    alertes = [d for d in lignes_detroits if d.get("niveau") in ("forte baisse", "en baisse", "forte hausse", "en hausse")]
    md = ["## Flux dans les détroits", ""]
    date_pw = detroits.get("derniere_date_publiee") or "date inconnue"
    if not lignes_detroits:
        md.append("Les données PortWatch ne sont pas disponibles pour le moment.")
    else:
        md.append(f"Données IMF PortWatch publiées jusqu'au {date_pw}. Moyenne quotidienne des passages de navires-citernes sur les 7 derniers jours, comparée aux 4 semaines précédentes :")
        md.append("")
        for d in lignes_detroits[:8]:
            md.append(
                f"- **{d['nom']}** : {_nombre(d.get('petroliers_7j'))} par jour ({_pct(d.get('ecart_vs_4sem_pct'))} ; "
                f"{_pct(d.get('ecart_vs_an_dernier_pct'))} sur un an) — {d.get('niveau')}."
            )
        md.append("")
        if alertes:
            noms = ", ".join(d["nom"] for d in alertes)
            md.append(f"Écarts notables (plus de 15 % par rapport aux 4 semaines précédentes) : {noms}.")
        else:
            md.append("Aucun détroit pétrolier majeur ne s'écarte de plus de 15 % de son niveau des 4 semaines précédentes.")

    md += ["", "## Pétrole et prix", ""]
    par_id = {s["id"]: s for s in marches.get("series", [])}
    brent, wti = par_id.get("brent"), par_id.get("wti")
    if brent:
        md.append(
            f"Le Brent cote {_nombre(brent['dernier'], 2)} $ le baril au {brent['derniere_date']} "
            f"({_pct(brent.get('var_5j'))} sur 5 séances, {_pct(brent.get('var_1m'))} sur un mois). "
            f"Sa volatilité sur 30 jours atteint {_nombre(brent.get('volatilite_30j_pct'))} % en rythme annuel."
        )
    else:
        md.append("Le cours du Brent n'a pas pu être récupéré.")
    if wti and marches.get("ecart_brent_wti_dollars") is not None:
        md.append(f"L'écart entre Brent et WTI est de {_nombre(marches['ecart_brent_wti_dollars'], 2)} $.")
    if stocks and not stocks.get("erreur"):
        variation = stocks["variation_semaine"]
        sens = "+" if variation >= 0 else "−"
        md.append(
            f"Aux États-Unis, les stocks commerciaux de brut s'établissent à {_nombre(stocks['niveau'], 0)} milliers de barils "
            f"(semaine du {stocks['semaine']}, {sens}{_nombre(abs(variation), 0)} milliers sur la semaine)."
        )

    md += ["", "## Canaux vers les marchés", ""]
    choc_offre = [d for d in alertes if d.get("niveau") in ("forte baisse", "en baisse") and d["nom"] in ("Détroit d'Ormuz", "Bab el-Mandeb", "Canal de Suez", "Bosphore", "Détroit de Malacca")]
    detour = [d for d in alertes if d["nom"] == "Cap de Bonne-Espérance" and (d.get("ecart_vs_4sem_pct") or 0) > 0]
    if choc_offre:
        md.append(
            "La baisse des passages de pétroliers (" + ", ".join(d["nom"] for d in choc_offre) + ") pourrait refléter une perturbation de l'offre "
            "ou une crainte sur l'offre future. La littérature distingue ces deux cas : les chocs de demande de précaution font monter "
            "le prix plus vite que les pertes physiques de barils (Kilian, 2009), et ils pèsent sur les actions alors que les hausses "
            "tirées par la demande ne le font pas d'emblée (Kilian et Park, 2009)."
        )
    if detour:
        md.append(
            "La hausse des passages au cap de Bonne-Espérance serait cohérente avec des contournements de l'Afrique. Des trajets plus longs "
            "renchérissent le fret, et un doublement du coût du fret relève l'inflation d'environ 0,7 point (Carrière-Swallow et al., 2023)."
        )
    if brent and (brent.get("var_1m") or 0) >= 10:
        md.append(
            "La hausse du Brent sur un mois peut se transmettre aux anticipations d'inflation des ménages (Coibion et Gorodnichenko, 2015), "
            "ce qui compliquerait la tâche des banques centrales."
        )
    if not (choc_offre or detour or (brent and (brent.get("var_1m") or 0) >= 10)):
        md.append(
            "Les données du jour ne montrent pas de signal fort. Pour mémoire, l'effet d'un choc pétrolier sur les marchés dépend de son origine "
            "(Kilian, 2009) et chaque épisode historique a combiné offre, demande et anticipations de façon différente (Baumeister et Kilian, 2016)."
        )

    md += ["", "## Points à surveiller", ""]
    points = []
    for d in alertes[:3]:
        points.append(f"- {d['nom']} : vérifier si l'écart de {_pct(d.get('ecart_vs_4sem_pct'))} se confirme dans la prochaine publication PortWatch.")
    vix = par_id.get("vix")
    if vix:
        points.append(f"- VIX à {_nombre(vix['dernier'], 1)} : une hausse durable signalerait que le risque se diffuse aux actions.")
    if brent:
        points.append("- Brent : une hausse plus rapide que celle du WTI indiquerait une tension propre aux routes maritimes.")
    zones_liste = zones.get("zones", []) if isinstance(zones, dict) else []
    arret = [z for z in zones_liste if (z.get("part_a_l_arret_pct") or 0) >= 50 and z["petroliers"] >= 20]
    if arret:
        z = arret[0]
        points.append(f"- {z['zone']} : {z['part_a_l_arret_pct']} % des pétroliers repérés sont à l'arrêt, à suivre (attente, stockage flottant ou congestion).")
    md += points[:5] or ["- Pas de point particulier aujourd'hui."]

    md += [
        "",
        "## Limites des données",
        "",
        f"- PortWatch publie avec quelques jours de retard : les derniers passages connus datent du {date_pw}.",
        "- Le flux AIS gratuit repose surtout sur des antennes côtières : la haute mer est mal couverte et les navires qui coupent leur AIS n'apparaissent pas.",
        "- Les types AIS 80 à 89 regroupent tous les navires-citernes (brut, produits raffinés, chimie, gaz), pas seulement le pétrole brut.",
        "- Les estimations de volumes fondées sur l'AIS sont fiables en agrégé mais fragiles pays par pays (Adland, Jia et Strandenes, 2017).",
        "- Ce rapport est produit par des règles fixes, sans modèle de langage : il signale des écarts, il ne les explique pas.",
    ]
    return "\n".join(md)


# --- Point d'entrée -----------------------------------------------------------------------------


def dates_des_donnees(outils: Outils) -> dict:
    lire = outils._lire
    return {
        "portwatch": lire("detroits").get("derniere_date"),
        "positions_ais": lire("petroliers").get("genere_le"),
        "marches": lire("marches").get("genere_le"),
    }


def produire_analyse(data_dir: Path, cle: str | None = None, modeles: list[str] | None = None, base: str = URL_GEMINI) -> dict:
    outils = Outils(data_dir)
    cle = (cle if cle is not None else os.environ.get("GEMINI_API_KEY", "")).strip()
    modeles = modeles or ([os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else MODELES_PAR_DEFAUT)
    texte, mode, modele_utilise, erreur = None, "regles", None, None
    incidents: list[str] = []

    if cle:
        for modele in modeles:
            try:
                texte = boucle_agent(outils, cle, modele, base=base)
                mode, modele_utilise = "ia", modele
                break
            except ErreurAgent as exc:
                erreur = str(exc)
                incidents.append(masquer(erreur)[:400])
                log.warning("Agent Gemini (%s) en échec : %s", modele, exc)
                outils.appels.clear()
                if not exc.essayer_un_autre_modele:  # clé refusée ou requête invalide
                    break

    if texte is None:
        outils.appels.clear()
        texte = rapport_regles(outils)

    controle = controler(texte)
    analyse = {
        "genere_le": iso(maintenant()),
        "mode": mode,
        "modele": modele_utilise or "règles fixes (sans IA)",
        "markdown": ajouter_sources(texte, controle["articles_cites"]),
        "controle": controle,
        "outils_appeles": [a["outil"] for a in outils.appels],
        "dates_des_donnees": dates_des_donnees(outils),
        "erreur_ia": masquer(erreur) if erreur and mode == "regles" else None,
        "incidents_ia": incidents,
    }
    ecrire_json(data_dir / config.FICHIERS["analyse"], analyse)
    return analyse


def analyse_perimee(data_dir: Path, heures: float) -> bool:
    from .util import lire_iso, lire_json

    analyse = lire_json(data_dir / config.FICHIERS["analyse"], {}) or {}
    genere = lire_iso(analyse.get("genere_le"))
    return genere is None or (maintenant() - genere).total_seconds() > heures * 3600
