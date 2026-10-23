import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline import collect_ais
from pipeline.collect_ais import Collecteur, ErreurAIS, compter_zones, fusionner
from pipeline.util import iso

try:
    import websockets  # noqa: F401

    WEBSOCKETS = True
except ImportError:
    WEBSOCKETS = False

INSTANT = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def statique(mmsi, type_navire, nom="ARETI@@@@", destination="FUJAIRAH@@"):
    return {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": mmsi, "ShipName": nom.replace("@", " ").strip()},
        "Message": {"ShipStaticData": {
            "UserID": mmsi, "Type": type_navire, "Name": nom, "Destination": destination,
            "ImoNumber": 9312345, "MaximumStaticDraught": 15.2, "Dimension": {"A": 200, "B": 50, "C": 20, "D": 24},
        }},
    }


def position(mmsi, lat, lon, sog=12.0, cap=511, cog=90.0, valide=True):
    return {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "time_utc": "2026-10-22 11:58:03.123456789 +0000 UTC"},
        "Message": {"PositionReport": {
            "UserID": mmsi, "Latitude": lat, "Longitude": lon, "Sog": sog, "Cog": cog,
            "TrueHeading": cap, "NavigationalStatus": 0, "Valid": valide,
        }},
    }


class TestCollecteur(unittest.TestCase):
    def setUp(self):
        self.c = Collecteur({}, horloge=lambda: INSTANT)

    def test_ne_garde_que_les_citernes(self):
        self.c.traiter(json.dumps(statique(111111111, 84)))
        self.c.traiter(json.dumps(statique(222222222, 70)))  # cargo
        self.c.traiter(json.dumps(position(111111111, 26.5, 56.3)))
        self.c.traiter(json.dumps(position(222222222, 26.6, 56.4)))
        self.c.traiter(json.dumps(position(333333333, 26.7, 56.5)))  # type encore inconnu
        petroliers = self.c.petroliers()
        self.assertEqual(len(petroliers), 1)
        p = petroliers[0]
        self.assertEqual(p["mmsi"], "111111111")
        self.assertEqual(p["nom"], "ARETI")
        self.assertEqual(p["destination"], "FUJAIRAH")
        self.assertEqual(p["longueur"], 250)
        self.assertEqual(p["type"], 84)
        self.assertEqual(p["vu"], "2026-10-22T11:58:03Z")

    def test_cap_vrai_puis_route_fond(self):
        self.c.traiter(position(1, 10, 10, cap=45, cog=90))
        self.c.traiter(position(2, 10, 10, cap=511, cog=90.4))
        self.c.traiter(position(3, 10, 10, cap=511, cog=360))
        self.assertEqual(self.c.positions["1"]["cap"], 45)
        self.assertEqual(self.c.positions["2"]["cap"], 90)
        self.assertIsNone(self.c.positions["3"]["cap"])

    def test_positions_invalides_ignorees(self):
        self.c.traiter(position(1, 91, 181))
        self.c.traiter(position(2, 0, 0))
        self.c.traiter(position(3, 10, 10, valide=False))
        self.c.traiter("pas du json")
        self.assertEqual(self.c.positions, {})
        self.assertEqual(self.c.compteurs["ignores"], 4)

    def test_changement_de_type_retire_du_registre(self):
        self.c.traiter(statique(5, 80))
        self.assertIn("5", self.c.registre)
        self.c.traiter(statique(5, 60))
        self.assertNotIn("5", self.c.registre)

    def test_barges_fluviales_ecartees(self):
        barge = statique(444444444, 80)
        barge["Message"]["ShipStaticData"]["Dimension"] = {"A": 100, "B": 10, "C": 5, "D": 6}  # 110 m x 11 m
        self.c.traiter(barge)
        self.c.traiter(position(444444444, 51.9, 4.1, sog=0))
        self.assertIn("444444444", self.c.registre)
        self.assertEqual(self.c.petroliers(), [])
        self.assertEqual(self.c.diagnostic(1)["dont_barges_ou_petites_citernes_ecartees"], 1)

    def test_accuse_d_abonnement_conserve_sans_la_cle(self):
        self.c.traiter({"Status": "subscribed", "CompressionEnabled": True, "APIKey": "secret"})
        self.assertEqual(self.c.confirmation, {"Status": "subscribed", "CompressionEnabled": True})

    def test_erreur_de_cle(self):
        self.c.traiter(json.dumps({"error": "Api Key Is Not Valid"}))
        self.assertEqual(self.c.erreur, "Api Key Is Not Valid")


class TestFusionEtZones(unittest.TestCase):
    def test_fusion_garde_les_recents(self):
        anciens = [
            {"mmsi": "1", "lat": 1, "lon": 1, "longueur": 250, "vu": iso(INSTANT - timedelta(hours=5))},
            {"mmsi": "2", "lat": 2, "lon": 2, "longueur": 250, "vu": iso(INSTANT - timedelta(hours=50))},
            {"mmsi": "3", "lat": 3, "lon": 3, "longueur": 110, "vu": iso(INSTANT - timedelta(hours=1))},  # barge
        ]
        nouveaux = [{"mmsi": "1", "lat": 9, "lon": 9, "longueur": 250, "vu": iso(INSTANT)}]
        resultat = fusionner(anciens, nouveaux, INSTANT, 36)
        self.assertEqual([p["mmsi"] for p in resultat], ["1"])
        self.assertEqual(resultat[0]["lat"], 9)

    def test_comptage_par_zone(self):
        petroliers = [
            {"mmsi": "1", "lat": 27.0, "lon": 51.0, "vitesse": 0.1, "vu": iso(INSTANT)},
            {"mmsi": "2", "lat": 27.5, "lon": 50.0, "vitesse": 12.0, "vu": iso(INSTANT)},
            {"mmsi": "3", "lat": 27.5, "lon": 50.0, "vitesse": 12.0, "vu": iso(INSTANT - timedelta(hours=10))},
        ]
        point = compter_zones(petroliers, INSTANT)
        self.assertEqual(point["z"]["golfe_persique"], [2, 1])
        self.assertEqual(point["n"], 2)

    def test_mise_a_jour_ecrit_les_fichiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            c = Collecteur({}, horloge=lambda: INSTANT)
            c.traiter(statique(7, 80))
            c.traiter(position(7, 26.6, 56.4))
            resume = collect_ais.mettre_a_jour(data, c, 1, instant=INSTANT)
            self.assertEqual(resume["petroliers_sur_la_carte"], 1)
            for nom in ("tankers.json", "tanker_registry.json", "zones.json"):
                self.assertTrue((data / nom).exists(), nom)


@unittest.skipUnless(WEBSOCKETS, "bibliothèque websockets absente")
class TestFluxWebsocket(unittest.TestCase):
    """Faux serveur aisstream en local : vérifie l'abonnement et la lecture du flux de bout en bout."""

    def demarrer(self, gestionnaire):
        from websockets.asyncio.server import serve

        pret = threading.Event()
        etat = {}

        def tourner():
            boucle = asyncio.new_event_loop()
            etat["boucle"] = boucle

            async def principal():
                async with serve(gestionnaire, "127.0.0.1", 0) as serveur:
                    etat["port"] = serveur.sockets[0].getsockname()[1]
                    etat["arret"] = boucle.create_future()
                    pret.set()
                    await etat["arret"]

            boucle.run_until_complete(principal())
            boucle.close()

        fil = threading.Thread(target=tourner, daemon=True)
        fil.start()
        pret.wait(5)
        self.addCleanup(lambda: etat["boucle"].call_soon_threadsafe(etat["arret"].set_result, None))
        return f"ws://127.0.0.1:{etat['port']}"

    def test_flux_complet(self):
        recu = {}

        async def gestionnaire(ws):
            recu["abonnement"] = json.loads(await ws.recv())
            for m in (statique(123456789, 80), position(123456789, 26.5, 56.3), statique(987654321, 30)):
                await ws.send(json.dumps(m))
            await asyncio.sleep(10)

        url = self.demarrer(gestionnaire)
        collecteur = Collecteur({})
        asyncio.run(collect_ais.ecouter("cle-test", 0.03, collecteur, url=url))
        self.assertEqual(recu["abonnement"]["APIKey"], "cle-test")
        self.assertEqual(recu["abonnement"]["FilterMessageTypes"], ["PositionReport", "ShipStaticData"])
        self.assertEqual(len(collecteur.petroliers()), 1)

    def test_cle_refusee(self):
        async def gestionnaire(ws):
            await ws.recv()
            await ws.send(json.dumps({"error": "Api Key Is Not Valid"}))
            await ws.close()

        url = self.demarrer(gestionnaire)
        with self.assertRaises(ErreurAIS):
            asyncio.run(collect_ais.ecouter("mauvaise", 0.2, Collecteur({}), url=url))


if __name__ == "__main__":
    unittest.main()
