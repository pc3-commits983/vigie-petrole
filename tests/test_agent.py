import json
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from pipeline import agent, demo, kpi
from pipeline.tools import Outils, executer

SELECTION = {
    "titre": "Bab el-Mandeb ralentit, le pétrole se tend",
    "indicateurs": [
        {"id": "detroit:chokepoint4", "pourquoi": "Moins de pétrole vers l'Europe, risque sur l'offre.", "reference": "Kilian, 2009"},
        {"id": "marche:brent", "pourquoi": "Pèse sur l'inflation et les marges.", "reference": None},
        {"id": "marche:vix", "pourquoi": "La nervosité gagne les actions.", "reference": "Kilian et Park, 2009"},
        {"id": "marche:inexistant", "pourquoi": "Identifiant inventé.", "reference": None},
        {"id": "detroit:chokepoint7", "pourquoi": "Détours par le Cap, fret plus cher.", "reference": "Dupont, 2021"},
    ],
    "a_surveiller": ["Prochaine publication PortWatch pour Bab el-Mandeb", "Brent au-dessus de son plus haut annuel"],
}
TEXTE_FINAL = "Voici ma sélection :\n```json\n" + json.dumps(SELECTION, ensure_ascii=False) + "\n```"


def preparer(dossier: Path) -> None:
    demo.generer(dossier)
    kpi.construire(dossier)


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
        preparer(cls.data)
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
            appel_outil("indicateurs_cles", signature="sig-123"),
            appel_outil("chercher_litterature", {"theme": "actions précaution"}),
            texte(TEXTE_FINAL),
        ]
        analyse = agent.produire_analyse(self.data, cle="cle-test", modeles=["modele-test"], base=self.base)
        self.assertEqual(analyse["mode"], "ia")
        self.assertEqual(analyse["modele"], "modele-test")
        self.assertEqual(analyse["outils_appeles"], ["indicateurs_cles", "chercher_litterature"])
        self.assertEqual(analyse["titre"], SELECTION["titre"])
        # identifiant inventé écarté, citation inventée retirée mais indicateur gardé
        self.assertEqual([s["id"] for s in analyse["selection"]], ["detroit:chokepoint4", "marche:brent", "marche:vix", "detroit:chokepoint7"])
        self.assertEqual(analyse["controle"]["ids_inconnus"], ["marche:inexistant"])
        self.assertEqual(analyse["controle"]["citations_inconnues"], ["Dupont, 2021"])
        self.assertIsNone(analyse["selection"][3]["reference"])
        self.assertTrue(any("International Economic Review" in r for r in analyse["references"]))

        premiere, deuxieme = FauxGemini.requetes[0], FauxGemini.requetes[1]
        self.assertEqual(premiere["chemin"], "/v1beta/models/modele-test:generateContent")
        self.assertEqual(premiere["cle"], "cle-test")
        noms = [d["name"] for d in premiere["corps"]["tools"][0]["functionDeclarations"]]
        self.assertIn("chercher_litterature", noms)
        # Le message du modèle est renvoyé tel quel, signature comprise, suivi du résultat de l'outil
        contenus = deuxieme["corps"]["contents"]
        self.assertEqual(contenus[1]["parts"][0]["thoughtSignature"], "sig-123")
        reponse_outil = contenus[2]["parts"][0]["functionResponse"]
        self.assertEqual(reponse_outil["name"], "indicateurs_cles")
        self.assertIn("indicateurs", reponse_outil["response"]["resultat"])

    def test_repli_sur_les_regles_si_la_cle_est_refusee(self):
        FauxGemini.scenario = [(403, {"error": {"message": "cle invalide"}})]
        analyse = agent.produire_analyse(self.data, cle="cle-test", modeles=["modele-a", "modele-b"], base=self.base)
        self.assertEqual(analyse["mode"], "regles")
        self.assertIn("403", analyse["erreur_ia"])
        self.assertEqual(len(FauxGemini.requetes), 1)  # inutile d'essayer un autre modèle
        self.assertGreaterEqual(len(analyse["selection"]), 3)

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

    def test_reponse_inutilisable_on_se_replie(self):
        FauxGemini.scenario = [texte("Désolé, je ne peux pas répondre.")]
        analyse = agent.produire_analyse(self.data, cle="cle-test", modeles=["modele-test"], base=self.base)
        self.assertEqual(analyse["mode"], "regles")
        self.assertIn("JSON", analyse["erreur_ia"])

    def test_sans_cle_selection_par_regles(self):
        analyse = agent.produire_analyse(self.data, cle="")
        self.assertEqual(analyse["mode"], "regles")
        self.assertEqual(analyse["format"], agent.FORMAT)
        connus = {i["id"] for i in json.loads((self.data / "kpis.json").read_text())["indicateurs"]}
        self.assertTrue(3 <= len(analyse["selection"]) <= 6)
        self.assertTrue(all(s["id"] in connus for s in analyse["selection"]))
        self.assertIn("marche:brent", [s["id"] for s in analyse["selection"]])


class TestValidation(unittest.TestCase):
    INDICATEURS = [{"id": f"marche:{n}"} for n in ("brent", "wti", "vix", "or")]

    def test_extraire_json_entoure_de_texte(self):
        self.assertEqual(agent.extraire_json('blabla ```json {"titre": "x"} ``` fin'), {"titre": "x"})
        with self.assertRaises(agent.ErreurAgent):
            agent.extraire_json("pas de json")

    def test_textes_raccourcis_et_doublons_retires(self):
        long = " ".join(["mot"] * 40)
        reponse = {"titre": long, "indicateurs": [{"id": "marche:brent", "pourquoi": long}, {"id": "marche:brent"},
                   {"id": "marche:wti"}, {"id": "marche:vix", "pourquoi": "Hausse de 30 %"}], "a_surveiller": [long] * 5}
        resultat, controle = agent.valider(reponse, self.INDICATEURS)
        self.assertEqual(len(resultat["selection"]), 3)
        self.assertLessEqual(len(resultat["titre"].split()), 13)
        self.assertLessEqual(len(resultat["selection"][0]["pourquoi"].split()), 21)
        self.assertEqual(len(resultat["a_surveiller"]), 3)
        self.assertEqual(controle["explications_avec_chiffres"], 1)

    def test_trop_peu_d_indicateurs_valides(self):
        with self.assertRaises(agent.ErreurAgent):
            agent.valider({"indicateurs": [{"id": "marche:brent"}, {"id": "faux"}]}, self.INDICATEURS)


class TestOutils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        preparer(Path(cls.tmp.name))
        cls.outils = Outils(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_indicateurs_cles(self):
        r = self.outils.indicateurs_cles()
        scores = [i["score"] for i in r["indicateurs"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertIn("variations", r["indicateurs"][0])

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
