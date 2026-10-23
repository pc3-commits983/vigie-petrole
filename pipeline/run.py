"""Orchestrateur lancé chaque heure par GitHub Actions.

    python -m pipeline.run --out _site            passage normal
    python -m pipeline.run --out _site --demo     site complet avec des données fictives (sans clé)

À chaque passage (toutes les heures) : on récupère l'état précédent, on écoute le flux AIS,
on actualise les cours, PortWatch deux fois par jour, puis on recalcule les indicateurs clés.
Toutes les 6 heures, l'agent choisit les indicateurs du jour et écrit une ligne pour chacun.
Chaque étape est isolée : si l'une échoue, les autres continuent et le site est quand même publié.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from . import agent, collect_ais, config, kpi, markets, portwatch, state
from .util import ecrire_json, iso, lire_iso, lire_json, maintenant, masquer

log = logging.getLogger("vigie")


def copier_site(sortie: Path) -> None:
    sortie.mkdir(parents=True, exist_ok=True)
    for element in config.SITE_SRC.iterdir():
        cible = sortie / element.name
        if element.is_dir():
            shutil.copytree(element, cible, dirs_exist_ok=True)
        else:
            shutil.copy2(element, cible)


def etape(etat: dict, nom: str, fonction, *args, **kwargs):
    try:
        details = fonction(*args, **kwargs)
        etat["etapes"][nom] = {"ok": True, "details": details}
        log.info("Étape %s : ok", nom)
        return details
    except Exception as exc:
        log.exception("Étape %s en échec", nom)
        etat["etapes"][nom] = {"ok": False, "erreur": masquer(f"{type(exc).__name__} : {exc}")}
        return None


def analyse_a_refaire(data_dir: Path, forcer: bool, cle_gemini: str) -> bool:
    """Vrai si l'analyse est trop ancienne, demandée, ou si la dernière a dû se passer de Gemini.

    Dans ce dernier cas (surcharge passagère, par exemple), on retente à chaque passage horaire
    au lieu d'attendre plusieurs heures avec une sélection par règles.
    """
    if forcer or agent.analyse_perimee(data_dir, config.ANALYSE_TOUTES_LES_HEURES):
        return True
    derniere = lire_json(data_dir / config.FICHIERS["analyse"], {}) or {}
    return bool(cle_gemini) and derniere.get("mode") == "regles"


def portwatch_a_refaire(data_dir: Path) -> bool:
    donnees = lire_json(data_dir / config.FICHIERS["detroits"], {}) or {}
    genere = lire_iso(donnees.get("genere_le"))
    return genere is None or (maintenant() - genere).total_seconds() > config.PORTWATCH_TOUTES_LES_HEURES * 3600


def resume_analyse(resultat: dict) -> dict:
    return {"mode": resultat["mode"], "modele": resultat["modele"], "titre": resultat.get("titre"), "erreur_ia": resultat["erreur_ia"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vigie pétrole : collecte, analyse et construction du site")
    parser.add_argument("--out", default="_site", help="dossier du site généré")
    parser.add_argument("--demo", action="store_true", help="données fictives, aucune clé nécessaire")
    parser.add_argument("--forcer-analyse", action="store_true", help="régénérer l'analyse même si elle est récente")
    parser.add_argument("--sans-ais", action="store_true", help="ne pas écouter le flux AIS")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sortie = Path(args.out)
    data_dir = sortie / "data"
    copier_site(sortie)
    etat = {"debut": iso(maintenant()), "demo": args.demo, "etapes": {}}

    if args.demo:
        from . import demo

        etape(etat, "demo", demo.generer, data_dir)
        etape(etat, "indicateurs", kpi.construire, data_dir)
        etape(etat, "analyse", lambda: resume_analyse(agent.produire_analyse(data_dir, cle="")))
    else:
        url = state.url_du_site()
        if url:
            etape(etat, "restauration", state.restaurer, data_dir, url)

        cle_ais = os.environ.get("AISSTREAM_API_KEY", "").strip()
        if args.sans_ais:
            etat["etapes"]["ais"] = {"ok": True, "details": "désactivé"}
        elif not cle_ais:
            etat["etapes"]["ais"] = {"ok": False, "erreur": "Clé AISSTREAM_API_KEY absente"}
        else:
            etape(etat, "ais", collect_ais.collecter, data_dir, cle_ais)

        etape(etat, "marches", markets.actualiser, data_dir)
        if portwatch_a_refaire(data_dir):
            etape(etat, "portwatch", portwatch.actualiser, data_dir)
        else:
            etat["etapes"]["portwatch"] = {"ok": True, "details": "déjà à jour"}
        etape(etat, "indicateurs", kpi.construire, data_dir)

        forcer = args.forcer_analyse or os.environ.get("FORCER_ANALYSE", "").lower() == "true"
        if analyse_a_refaire(data_dir, forcer, os.environ.get("GEMINI_API_KEY", "").strip()):
            etape(etat, "analyse", lambda: resume_analyse(agent.produire_analyse(data_dir)))
        else:
            etat["etapes"]["analyse"] = {"ok": True, "details": "sélection récente conservée"}

    etat["fin"] = iso(maintenant())
    ecrire_json(data_dir / config.FICHIERS["etat"], etat, compact=False)
    echecs = [n for n, e in etat["etapes"].items() if not e["ok"]]
    print("Étapes en échec :", ", ".join(echecs) if echecs else "aucune")
    return 0


if __name__ == "__main__":
    sys.exit(main())
