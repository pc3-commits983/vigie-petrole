"""Collecte des positions de pétroliers depuis le flux AIS gratuit d'aisstream.io.

Principe : on écoute le flux mondial quelques minutes. Les messages de position
(PositionReport) ne disent pas quel est le type du navire ; ce sont les messages
statiques (ShipStaticData, émis environ toutes les 6 minutes) qui le donnent.
On garde donc un registre des navires-citernes (types AIS 80 à 89), conservé
d'un passage à l'autre, et on ne retient que les positions des navires du registre.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .util import ecrire_json, iso, lire_iso, lire_json, maintenant

log = logging.getLogger(__name__)

URL_AIS = "wss://stream.aisstream.io/v0/stream"
MONDE = [[[-90.0, -180.0], [90.0, 180.0]]]
REGISTRE_RETENTION_JOURS = 180


class ErreurAIS(RuntimeError):
    """Erreur renvoyée par aisstream (clé refusée, abonnement invalide...)."""


def _nettoyer_texte(valeur) -> str:
    if not isinstance(valeur, str):
        return ""
    return valeur.replace("@", " ").strip()


def _heure_message(meta: dict) -> datetime | None:
    """aisstream envoie par exemple « 2026-10-22 14:03:11.123456789 +0000 UTC »."""
    texte = (meta or {}).get("time_utc")
    if not isinstance(texte, str) or len(texte) < 19:
        return None
    try:
        return datetime.strptime(texte[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _cap(position: dict) -> int | None:
    """Cap vrai si disponible (511 = inconnu), sinon route fond (360 = inconnue)."""
    cap = position.get("TrueHeading")
    if isinstance(cap, (int, float)) and 0 <= cap < 360:
        return int(round(cap))
    route = position.get("Cog")
    if isinstance(route, (int, float)) and 0 <= route < 360:
        return int(round(route))
    return None


class Collecteur:
    """Traite les messages AIS un par un et garde la dernière position de chaque navire."""

    def __init__(self, registre: dict[str, dict] | None = None, horloge=maintenant):
        self.registre: dict[str, dict] = registre if registre is not None else {}
        self.positions: dict[str, dict] = {}
        self.noms_vus: dict[str, str] = {}
        self.horloge = horloge
        self.erreur: str | None = None
        self.compteurs = {"messages": 0, "positions": 0, "statiques": 0, "ignores": 0}
        self.vus_statiques: set[str] = set()  # navires distincts dont on a reçu le message statique
        self.confirmation: dict | None = None  # accusé d'abonnement envoyé par aisstream

    def traiter(self, brut) -> None:
        self.compteurs["messages"] += 1
        try:
            message = json.loads(brut) if isinstance(brut, (str, bytes, bytearray)) else brut
        except json.JSONDecodeError:
            self.compteurs["ignores"] += 1
            return
        if not isinstance(message, dict):
            self.compteurs["ignores"] += 1
            return
        if "error" in message:
            self.erreur = str(message["error"])
            return

        genre = message.get("MessageType")
        if genre is None and "Message" not in message:
            # Accusé d'abonnement : il indique notamment si la compression est active
            if self.confirmation is None:
                self.confirmation = {k: v for k, v in message.items() if "key" not in k.lower()}
            self.compteurs["ignores"] += 1
            return
        contenu = (message.get("Message") or {}).get(genre) or {}
        meta = message.get("MetaData") or {}
        if genre == "ShipStaticData":
            self._statique(contenu, meta)
        elif genre == "PositionReport":
            self._position(contenu, meta)
        else:
            self.compteurs["ignores"] += 1

    def _statique(self, s: dict, meta: dict) -> None:
        mmsi = str(s.get("UserID") or meta.get("MMSI") or "")
        type_navire = s.get("Type")
        if not mmsi or not isinstance(type_navire, int):
            self.compteurs["ignores"] += 1
            return
        self.compteurs["statiques"] += 1
        self.vus_statiques.add(mmsi)
        if type_navire not in config.TYPES_CITERNE:
            # Le navire n'est pas (ou plus) déclaré comme citerne
            self.registre.pop(mmsi, None)
            return
        dimension = s.get("Dimension") or {}
        longueur = (dimension.get("A") or 0) + (dimension.get("B") or 0)
        imo = s.get("ImoNumber")
        tirant = s.get("MaximumStaticDraught")
        self.registre[mmsi] = {
            "nom": _nettoyer_texte(s.get("Name")) or _nettoyer_texte(meta.get("ShipName")),
            "type": type_navire,
            "imo": imo if isinstance(imo, int) and imo > 0 else None,
            "destination": _nettoyer_texte(s.get("Destination")),
            "tirant_eau": round(tirant, 1) if isinstance(tirant, (int, float)) and tirant > 0 else None,
            "longueur": longueur if longueur > 0 else None,
            "vu": iso(self.horloge()),
        }

    def _position(self, p: dict, meta: dict) -> None:
        mmsi = str(p.get("UserID") or meta.get("MMSI") or "")
        lat, lon = p.get("Latitude"), p.get("Longitude")
        if (
            not mmsi
            or p.get("Valid") is False
            or not isinstance(lat, (int, float))
            or not isinstance(lon, (int, float))
            or abs(lat) > 90
            or abs(lon) > 180
            or (lat == 0 and lon == 0)
        ):
            self.compteurs["ignores"] += 1
            return
        self.compteurs["positions"] += 1
        vitesse = p.get("Sog")
        heure = _heure_message(meta) or self.horloge()
        self.positions[mmsi] = {
            "lat": round(float(lat), 4),
            "lon": round(float(lon), 4),
            "cap": _cap(p),
            "vitesse": round(float(vitesse), 1) if isinstance(vitesse, (int, float)) and vitesse < 102.3 else None,
            "statut": p.get("NavigationalStatus"),
            "vu": iso(heure),
        }
        nom = _nettoyer_texte(meta.get("ShipName"))
        if nom:
            self.noms_vus[mmsi] = nom

    def diagnostic(self, minutes: float) -> dict:
        """Mesure de la couverture du flux pendant ce passage, pour juger la densité de la carte."""
        citernes_vues = sum(1 for m in self.positions if m in self.registre)
        return {
            "navires_distincts_positions": len(self.positions),
            "navires_distincts_statiques": len(self.vus_statiques),
            "citernes_identifiees_parmi_positions": citernes_vues,
            "messages_par_seconde": round(self.compteurs["messages"] / max(minutes * 60, 1), 1),
            "accuse_abonnement": self.confirmation,
        }

    def petroliers(self) -> list[dict]:
        """Positions des navires-citernes connus du registre."""
        resultat = []
        for mmsi, pos in self.positions.items():
            fiche = self.registre.get(mmsi)
            if not fiche:
                continue
            resultat.append(
                {
                    "mmsi": mmsi,
                    "nom": fiche.get("nom") or self.noms_vus.get(mmsi, ""),
                    **pos,
                    "type": fiche.get("type"),
                    "imo": fiche.get("imo"),
                    "destination": fiche.get("destination"),
                    "tirant_eau": fiche.get("tirant_eau"),
                    "longueur": fiche.get("longueur"),
                }
            )
        return resultat


async def ecouter(cle: str, minutes: float, collecteur: Collecteur, url: str = URL_AIS, boites=None) -> None:
    """Écoute le flux pendant `minutes`, avec reconnexion si le flux coupe (il est en bêta)."""
    import websockets  # importé ici pour que le reste du module marche sans la bibliothèque

    fin = time.monotonic() + minutes * 60
    abonnement = json.dumps(
        {
            "APIKey": cle,
            "BoundingBoxes": boites or MONDE,
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }
    )
    echecs = 0
    while time.monotonic() < fin:
        try:
            # La compression « deflate » est exigée par aisstream pour servir tout le débit.
            async with websockets.connect(
                url, compression="deflate", open_timeout=30, ping_interval=20, ping_timeout=60, max_size=2**23
            ) as ws:
                await ws.send(abonnement)
                while (reste := fin - time.monotonic()) > 0:
                    try:
                        brut = await asyncio.wait_for(ws.recv(), timeout=min(reste, 60))
                    except asyncio.TimeoutError:
                        continue
                    collecteur.traiter(brut)
                    if collecteur.erreur:
                        raise ErreurAIS(collecteur.erreur)
                return
        except ErreurAIS:
            raise
        except Exception as exc:  # coupure réseau, fermeture côté serveur...
            if collecteur.erreur:
                raise ErreurAIS(collecteur.erreur) from exc
            echecs += 1
            log.warning("Flux AIS interrompu (%s), tentative %d", exc, echecs)
            if echecs >= 8:
                raise ErreurAIS(f"flux AIS indisponible après {echecs} tentatives : {exc}") from exc
            await asyncio.sleep(min(30, 2**echecs))


def fusionner(anciens: list[dict], nouveaux: list[dict], instant: datetime, retention_h: float) -> list[dict]:
    """Garde les pétroliers récents : les nouvelles positions remplacent les anciennes."""
    limite = instant - timedelta(hours=retention_h)
    par_mmsi = {p["mmsi"]: p for p in anciens or [] if "mmsi" in p}
    for p in nouveaux:
        par_mmsi[p["mmsi"]] = p
    garde = []
    for p in par_mmsi.values():
        vu = lire_iso(p.get("vu"))
        if vu and vu >= limite:
            garde.append(p)
    garde.sort(key=lambda p: p["mmsi"])
    return garde


def nettoyer_registre(registre: dict[str, dict], instant: datetime) -> dict[str, dict]:
    limite = instant - timedelta(days=REGISTRE_RETENTION_JOURS)
    return {m: f for m, f in registre.items() if (lire_iso(f.get("vu")) or instant) >= limite}


def dans_zone(lat: float, lon: float, bbox) -> bool:
    lat_min, lon_min, lat_max, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def compter_zones(petroliers: list[dict], instant: datetime, fraicheur_h: float = 6) -> dict:
    """Nombre de pétroliers vus récemment par zone, et combien sont à l'arrêt (< 0,5 nœud)."""
    limite = instant - timedelta(hours=fraicheur_h)
    recents = [p for p in petroliers if (lire_iso(p.get("vu")) or instant) >= limite]
    zones = {}
    for zone in config.ZONES:
        dedans = [p for p in recents if dans_zone(p["lat"], p["lon"], zone["bbox"])]
        a_l_arret = sum(1 for p in dedans if p.get("vitesse") is not None and p["vitesse"] < 0.5)
        zones[zone["id"]] = [len(dedans), a_l_arret]
    return {"t": iso(instant), "n": len(recents), "z": zones}


def ajouter_historique(historique: list[dict], point: dict, instant: datetime) -> list[dict]:
    limite = instant - timedelta(days=config.ZONES_HISTORIQUE_JOURS)
    garde = [h for h in historique or [] if (lire_iso(h.get("t")) or instant) >= limite]
    garde.append(point)
    return garde


def mettre_a_jour(data_dir: Path, collecteur: Collecteur, minutes: float, instant: datetime | None = None) -> dict:
    """Écrit tankers.json, tanker_registry.json et zones.json à partir d'un collecteur rempli."""
    instant = instant or maintenant()
    anciens = (lire_json(data_dir / config.FICHIERS["petroliers"], {}) or {}).get("petroliers", [])
    petroliers = fusionner(anciens, collecteur.petroliers(), instant, config.AIS_RETENTION_HEURES)
    registre = nettoyer_registre(collecteur.registre, instant)

    ecrire_json(
        data_dir / config.FICHIERS["petroliers"],
        {
            "genere_le": iso(instant),
            "ecoute_minutes": minutes,
            "messages_recus": collecteur.compteurs["messages"],
            "nouvelles_positions": len(collecteur.petroliers()),
            "diagnostic": collecteur.diagnostic(minutes),
            "petroliers": petroliers,
        },
    )
    ecrire_json(data_dir / config.FICHIERS["registre"], registre)

    zones = lire_json(data_dir / config.FICHIERS["zones"], {}) or {}
    historique = ajouter_historique(zones.get("historique", []), compter_zones(petroliers, instant), instant)
    ecrire_json(
        data_dir / config.FICHIERS["zones"],
        {"zones": [{"id": z["id"], "nom": z["nom"], "bbox": z["bbox"]} for z in config.ZONES], "historique": historique},
    )
    return {
        "petroliers_sur_la_carte": len(petroliers),
        "positions_de_ce_passage": len(collecteur.petroliers()),
        "registre": len(registre),
        **collecteur.compteurs,
        **collecteur.diagnostic(minutes),
    }


def collecter(data_dir: Path, cle: str, minutes: float | None = None, url: str = URL_AIS) -> dict:
    """Point d'entrée : écoute le flux puis met à jour les fichiers de données."""
    minutes = config.AIS_MINUTES if minutes is None else minutes
    registre = lire_json(data_dir / config.FICHIERS["registre"], {}) or {}
    collecteur = Collecteur(registre)
    log.info("Écoute du flux AIS pendant %.1f min (registre : %d citernes)", minutes, len(registre))
    asyncio.run(ecouter(cle, minutes, collecteur, url=url))
    return mettre_a_jour(data_dir, collecteur, minutes)
