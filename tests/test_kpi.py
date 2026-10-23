import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from pipeline import demo, kpi


def detroit(valeurs, ident="chokepoint6", nom="Détroit d'Ormuz"):
    fin = date(2026, 10, 18)
    jours = [(fin - timedelta(days=len(valeurs) - 1 - k)).isoformat() for k in range(len(valeurs))]
    return {"detroits": [{"id": ident, "nom": nom, "lat": 26.5, "lon": 56.3, "petrole": True,
                          "serie": {"dates": jours, "petroliers": valeurs, "total": valeurs, "capacite": valeurs}}]}


class TestDetroits(unittest.TestCase):
    def test_reouverture_detectee(self):
        # un an à 40 par jour, une semaine quasi fermée, puis la reprise
        valeurs = [40] * 380 + [2] * 7 + [12] * 7
        ind = kpi.indicateurs_detroits(detroit(valeurs))[0]
        self.assertEqual(ind["etat"], "reprise")
        une_semaine = ind["variations"][0]
        self.assertEqual(une_semaine["libelle"], "1 sem.")
        self.assertEqual(une_semaine["valeur"], 500.0)
        self.assertEqual(ind["principale"], 0)

    def test_quasi_a_l_arret(self):
        ind = kpi.indicateurs_detroits(detroit([40] * 380 + [1] * 14))[0]
        self.assertEqual(ind["etat"], "quasi à l'arrêt")
        self.assertGreater(ind["score"], 3)

    def test_trafic_normal(self):
        ind = kpi.indicateurs_detroits(detroit([40] * 400))[0]
        self.assertIsNone(ind["etat"])
        self.assertEqual(ind["score"], 0)


class TestMarches(unittest.TestCase):
    def test_variation_principale_et_score(self):
        donnees = {"series": [
            {"id": "brent", "nom": "Brent", "unite": "$/baril", "dernier": 80, "var_1j": 0.2, "var_5j": 1.0, "var_1m": 12.0,
             "variation_en": "%", "derniere_date": "2026-10-21", "source": "test"},
            {"id": "taux10", "nom": "Taux 10 ans", "unite": "%", "dernier": 4.2, "var_1j": 15, "var_5j": 5, "var_1m": 10,
             "variation_en": "points de base", "derniere_date": "2026-10-21", "source": "test"},
        ]}
        brent, taux = kpi.indicateurs_marches(donnees)
        self.assertEqual(brent["variations"][brent["principale"]]["libelle"], "1 mois")
        self.assertEqual(taux["variations"][taux["principale"]]["libelle"], "1 j")
        self.assertEqual(taux["variations"][0]["unite"], "pb")
        self.assertEqual(brent["score"], round(12 / 8 * 1.2, 2))


class TestConstruction(unittest.TestCase):
    def test_sur_les_donnees_de_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            dossier = Path(tmp)
            demo.generer(dossier)
            kpi.construire(dossier)
            import json

            indicateurs = json.loads((dossier / "kpis.json").read_text())["indicateurs"]
            ids = {i["id"] for i in indicateurs}
            self.assertIn("marche:brent", ids)
            self.assertIn("marche:ecart_brent_wti", ids)
            self.assertIn("stocks:us", ids)
            self.assertTrue(any(i.startswith("zone:") for i in ids))
            scores = [i["score"] for i in indicateurs]
            self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
