import json
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from pipeline import agent, demo
from pipeline.tools import Outils, executer

TEXTE_FINAL = """## Flux dans les détroits
Les passages à Bab el-Mandeb baissent (données PortWatch au 17 octobre).
## Pétrole et prix
Le Brent progresse.
## Canaux vers les marchés
Une crainte sur l'offre ferait baisser les actions (Kilian et Park, 2009).
## Points à surveiller
- Prochaine publication PortWatch.
## Limites des données
- Couverture AIS côtière (Adland, Jia et Strandenes, 2017).
"""


class FauxGemini(BaseHTTPRequestHandler):
    """Imite l'API generateContent : deux demandes d'outils, puis le texte final."""

    scenario: list = []
    requetes: list = []

    def do_POST(self):
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FauxGemini.requetes.append({"chemin": self.path, "cle": self.headers.get("x-goog-api-key"), "corps": corps})
        statut, reponse = FauxGemini.scenario.pop(0)
        donnees = json.dumps(reponse).encode()
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def log_message(self, *args):
        pass


def appel_outil(nom, args=None, signature=None):
    partie = {"functionCall": {"name": nom, "args": args or {}}}
    if signature:
        partie["thoughtSignature"] = signature
    return 200, {"candidates": [{"content": {"role": "model", "parts": [partie]}}]}


def texte(t):
    return 200, {"candidates": [{"content": {"role": "model", "parts": [{"text": t}]}, "finishReason": "STOP"}]}


class TestAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.tmp.name)
        demo.generer(cls.data)
        cls.serveur = HTTPServer(("127.0.0.1", 0), FauxGemini)
        threading.Thread(target=cls.serveur.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.serveur.server_port}/v1beta"

    @classmethod
    def tearDownClass(cls):
        cls.serveur.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        FauxGemini.requetes = []

    def test_boucle_complete_avec_outils(self):
        FauxGemini.scenario = [
            appel_outil("etat_des_detroits", signature="sig-123"),
            appel_outil("chercher_litterature", {"theme": "actions précaution"}),
            texte(TEXTE_FINAL),
        ]
        analyse = agent.produire_analyse(self.data, cle="cle-test", modeles=["modele-test"], base=self.base)
        self.assertEqual(analyse["mode"], "ia")
        self.assertEqual(analyse["modele"], "modele-test")
        self.assertEqual(analyse["outils_appeles"], ["etat_des_detroits", "chercher_litterature"])
        self.assertTrue(analyse["controle"]["sections_ok"])
        self.assertEqual(analyse["controle"]["citations_inconnues"], [])
        self.assertIn("## Sources", analyse["markdown"])
        self.assertIn("International Economic Review", analyse["markdown"])

        premiere, deuxieme = FauxGemini.requetes[0], FauxGemini.requetes[1]
        self.assertEqual(premiere["chemin"], "/v1beta/models/modele-test:generateContent")
        self.assertEqual(premiere["cle"], "cle-test")
        noms = [d["name"] for d in premiere["corps"]["tools"][0]["functionDeclarations"]]
        self.assertIn("chercher_litterature", noms)
        # Le message du modèle est renvoyé tel quel, signature comprise, suivi du résultat de l'outil
        contenus = deuxieme["corps"]["contents"]
        self.assertEqual(contenus[1]["parts"][0]["thoughtSignature"], "sig-123")
        reponse_outil = contenus[2]["parts"][0]["functionResponse"]
        self.assertEqual(reponse_outil["name"], "etat_des_detroits")
        self.assertIn("detroits", reponse_outil["response"]["resultat"])

    def test_repli_sur_les_regles_si_la_cle_est_refusee(self):
        FauxGemini.scenario = [(403, {"error": {"message": "cle invalide"}})]
        analyse = agent.produire_analyse(self.data, cle="cle-test", modeles=["modele-a", "modele-b"], base=self.base)
        self.assertEqual(analyse["mode"], "regles")
        self.assertIn("403", analyse["erreur_ia"])
        self.assertEqual(len(FauxGemini.requetes), 1)  # inutile d'essayer un autre modèle
        self.assertTrue(analyse["controle"]["sections_ok"])

    def test_surcharge_passagere_on_reessaie(self):
        FauxGemini.scenario = [(503, {"error": {"message": "high demand"}}), texte(TEXTE_FINAL)]
        with mock.patch.object(agent.time, "sleep") as pause:
            analyse = agent.produire_analyse(self.data, cle="cle-test", modeles=["modele-test"], base=self.base)
        self.assertEqual(analyse["mode"], "ia")
        pause.assert_called_once_with(15)

    def test_surcharge_persistante_on_change_de_modele(self):
        surcharge = (503, {"error": {"message": "high demand"}})
        FauxGemini.scenario = [surcharge, surcharge, surcharge, texte(TEXTE_FINAL)]
        with mock.patch.object(agent.time, "sleep"):
            analyse = agent.produire_analyse(self.data, cle="cle-test", modeles=["sature", "secours"], base=self.base)
        self.assertEqual(analyse["mode"], "ia")
        self.assertEqual(analyse["modele"], "secours")

    def test_modele_introuvable_on_passe_au_suivant(self):
        FauxGemini.scenario = [(404, {"error": {"message": "not found"}}), texte(TEXTE_FINAL)]
        analyse = agent.produire_analyse(self.data, cle="cle-test", modeles=["ancien", "nouveau"], base=self.base)
        self.assertEqual(analyse["modele"], "nouveau")

    def test_sans_cle_rapport_par_regles(self):
        analyse = agent.produire_analyse(self.data, cle="")
        self.assertEqual(analyse["mode"], "regles")
        self.assertTrue(analyse["controle"]["sections_ok"])
        self.assertEqual(analyse["controle"]["citations_inconnues"], [])


class TestControles(unittest.TestCase):
    def test_citation_inventee_reperee(self):
        md = TEXTE_FINAL + "\nUn auteur imaginaire l'affirme (Dupont, 2021)."
        controle = agent.controler(md)
        self.assertEqual(controle["citations_inconnues"], ["Dupont, 2021"])
        self.assertIn("kilian_park2009", controle["articles_cites"])

    def test_sections_manquantes(self):
        self.assertFalse(agent.controler("## Introduction\nTexte")["sections_ok"])


class TestOutils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        demo.generer(Path(cls.tmp.name))
        cls.outils = Outils(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_detroits(self):
        r = self.outils.etat_des_detroits()
        self.assertTrue(r["detroits"][0]["detroit_petrolier_majeur"])
        self.assertIn("ecart_vs_4sem_pct", r["detroits"][0])

    def test_serie_detroit(self):
        r = self.outils.serie_detroit("ormuz", 8)
        self.assertEqual(r["nom"], "Détroit d'Ormuz")
        self.assertEqual(len(r["semaines"]), 8)
        self.assertIn("erreur", self.outils.serie_detroit("Atlantide"))

    def test_zones_marches_stocks(self):
        self.assertTrue(self.outils.petroliers_par_zone()["zones"])
        marches = self.outils.marches()
        self.assertNotIn("points", marches["series"][0])
        self.assertIn("niveau", self.outils.stocks_petrole_us())

    def test_litterature(self):
        r = self.outils.chercher_litterature("inflation anticipations")
        self.assertIn("Coibion et Gorodnichenko, 2015", [a["citation"] for a in r["articles"]])
        self.assertIn("themes_disponibles", self.outils.chercher_litterature("zzz"))

    def test_executer_ne_plante_jamais(self):
        self.assertIn("erreur", executer(self.outils, "supprimer_tout", {}))
        self.assertIn("erreur", executer(self.outils, "serie_detroit", {"inconnu": 1}))


if __name__ == "__main__":
    unittest.main()
