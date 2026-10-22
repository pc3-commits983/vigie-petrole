import json
import re
import tempfile
import unittest
from pathlib import Path

from pipeline import config, run, state


class TestConstructionDuSite(unittest.TestCase):
    def test_demo_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run.main(["--demo", "--out", tmp]), 0)
            sortie = Path(tmp)
            self.assertTrue((sortie / "index.html").exists())
            self.assertTrue((sortie / "geo" / "land.json").exists())
            for nom in config.FICHIERS.values():
                self.assertTrue((sortie / "data" / nom).exists(), nom)
            etat = json.loads((sortie / "data" / "status.json").read_text())
            self.assertTrue(etat["demo"])
            self.assertTrue(all(e["ok"] for e in etat["etapes"].values()))

    def test_la_page_lit_les_fichiers_produits(self):
        page = (config.SITE_SRC / "index.html").read_text(encoding="utf-8")
        for cle in ("petroliers", "detroits", "zones", "marches", "analyse", "etat"):
            self.assertIn(f"data/{config.FICHIERS[cle]}", page)
        # bibliothèques chargées depuis cdnjs, versions fixées
        for src in re.findall(r'<script src="([^"]+)"', page):
            self.assertTrue(src.startswith("https://cdnjs.cloudflare.com/ajax/libs/"), src)
            self.assertRegex(src, r"/\d+\.\d+\.\d+/")


class TestQuandRefaireLAnalyse(unittest.TestCase):
    def test_retente_gemini_apres_un_rapport_par_regles(self):
        from pipeline.util import ecrire_json, iso, maintenant

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            self.assertTrue(run.analyse_a_refaire(data, False, ""))  # aucune analyse encore
            ecrire_json(data / "analysis.json", {"genere_le": iso(maintenant()), "mode": "regles"})
            self.assertTrue(run.analyse_a_refaire(data, False, "cle"))
            self.assertFalse(run.analyse_a_refaire(data, False, ""))
            ecrire_json(data / "analysis.json", {"genere_le": iso(maintenant()), "mode": "ia"})
            self.assertFalse(run.analyse_a_refaire(data, False, "cle"))
            self.assertTrue(run.analyse_a_refaire(data, True, "cle"))


class TestSecrets(unittest.TestCase):
    def test_les_cles_ne_finissent_pas_dans_les_fichiers_publies(self):
        import os

        from pipeline.util import masquer

        ancien = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "cle-gemini-secrete"
        try:
            erreur = "403 Client Error for url: https://api.eia.gov/v2/x?api_key=ABC123&frequency=weekly (cle-gemini-secrete)"
            propre = masquer(erreur)
            self.assertNotIn("ABC123", propre)
            self.assertNotIn("cle-gemini-secrete", propre)
            self.assertIn("api_key=***", propre)
        finally:
            if ancien is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = ancien

    def test_la_page_echappe_les_textes_venus_de_l_ais(self):
        page = (config.SITE_SRC / "index.html").read_text(encoding="utf-8")
        self.assertIn("echapper(n.nom", page)
        self.assertIn("DOMPurify.sanitize", page)


class TestUrlDuSite(unittest.TestCase):
    def test_depuis_le_depot(self):
        import os

        anciens = {k: os.environ.get(k) for k in ("SITE_URL", "GITHUB_REPOSITORY")}
        try:
            os.environ.pop("SITE_URL", None)
            os.environ["GITHUB_REPOSITORY"] = "Exemple/vigie-petrole"
            self.assertEqual(state.url_du_site(), "https://exemple.github.io/vigie-petrole/")
            os.environ["GITHUB_REPOSITORY"] = "exemple/exemple.github.io"
            self.assertEqual(state.url_du_site(), "https://exemple.github.io/")
        finally:
            for k, v in anciens.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
