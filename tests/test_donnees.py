import unittest
from datetime import date, timedelta

from pipeline import markets, portwatch


class FausseReponse:
    def __init__(self, donnees):
        self.donnees = donnees
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.donnees


class FausseSession:
    def __init__(self, pages):
        self.pages = list(pages)
        self.params = []

    def get(self, url, params=None, **_):
        self.params.append(params)
        return FausseReponse(self.pages.pop(0))


class TestPortWatch(unittest.TestCase):
    def test_pagination(self):
        page1 = {"features": [{"attributes": {"date": "2026-10-01", "portid": "chokepoint6", "n_tanker": 30}}], "exceededTransferLimit": True}
        page2 = {"features": [{"attributes": {"date": "2026-10-02", "portid": "chokepoint6", "n_tanker": 31}}]}
        session = FausseSession([page1, page2])
        lignes = portwatch.telecharger(2025, session=session, par_page=1)
        self.assertEqual(len(lignes), 2)
        self.assertEqual(session.params[1]["resultOffset"], 1)
        self.assertEqual(session.params[0]["where"], "year >= 2025")

    def test_erreur_du_service(self):
        with self.assertRaises(RuntimeError):
            portwatch.telecharger(2025, session=FausseSession([{"error": {"code": 400}}]))

    def test_dates_texte_ou_millisecondes(self):
        lignes = [
            {"date": "2026-10-01", "portid": "a", "n_tanker": 1, "n_total": 2, "capacity_tanker": 3},
            {"date": 1759363200000, "portid": "a", "n_tanker": 4, "n_total": 5, "capacity_tanker": 6},  # 2025-10-02
        ]
        series = portwatch.series_par_detroit(lignes)
        self.assertEqual([j.isoformat() for j, *_ in series["a"]], ["2025-10-02", "2026-10-01"])

    def test_statistiques_forte_baisse(self):
        fin = date(2026, 10, 18)
        serie = [(fin - timedelta(days=k), 5 if k < 7 else 10, 20, 1000.0) for k in range(400, -1, -1)]
        stats = portwatch.statistiques(serie)
        self.assertEqual(stats["petroliers_7j"], 5)
        self.assertEqual(stats["petroliers_4sem_avant"], 10)
        self.assertEqual(stats["ecart_vs_4sem_pct"], -50.0)
        self.assertEqual(stats["niveau"], "forte baisse")
        self.assertEqual(stats["derniere_date"], "2026-10-18")

    def test_construire(self):
        lignes = [{"date": "2026-10-01", "portid": "chokepoint6", "n_tanker": 30, "n_total": 90, "capacity_tanker": 2e6}]
        contenu = portwatch.construire(lignes)
        ormuz = next(d for d in contenu["detroits"] if d["id"] == "chokepoint6")
        self.assertEqual(ormuz["nom"], "Détroit d'Ormuz")
        self.assertEqual(ormuz["serie"]["petroliers"], [30])
        self.assertEqual(contenu["derniere_date"], "2026-10-01")


class TestMarches(unittest.TestCase):
    def test_lire_yahoo(self):
        donnees = {"chart": {"result": [{
            "meta": {"gmtoffset": -14400},
            "timestamp": [1760947200, 1761033600, 1761120000],
            "indicators": {"quote": [{"close": [70.1, None, 71.5]}]},
        }]}}
        points = markets.lire_yahoo(donnees)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[-1][1], 71.5)

    def test_lire_fred(self):
        texte = "observation_date,DCOILBRENTEU\n2026-10-01,70.5\n2026-10-02,.\n2026-10-03,71.0\n"
        self.assertEqual(markets.lire_fred(texte), [("2026-10-01", 70.5), ("2026-10-03", 71.0)])

    def test_resume_en_pourcent_et_en_points_de_base(self):
        points = [(f"2026-09-{i:02d}", 100.0 + i) for i in range(1, 30)]
        resume = markets.resumer({"id": "brent", "nom": "Brent", "unite": "$", "variation": "pct"}, points, "test")
        self.assertAlmostEqual(resume["var_1j"], round((129 / 128 - 1) * 100, 2))
        self.assertIsNotNone(resume["volatilite_30j_pct"])
        taux = [(f"2026-09-{i:02d}", 4.0 + i / 100) for i in range(1, 30)]
        resume_taux = markets.resumer({"id": "taux10", "nom": "10 ans", "unite": "%", "variation": "pb"}, taux, "test")
        self.assertEqual(resume_taux["var_5j"], 5)
        self.assertEqual(resume_taux["variation_en"], "points de base")

    def test_eia(self):
        donnees = {"response": {"data": [
            {"period": "2026-10-16", "value": "420000"},
            {"period": "2026-10-09", "value": 421500},
        ]}}
        resume = markets.resumer_eia(markets.lire_eia(donnees))
        self.assertEqual(resume["niveau"], 420000)
        self.assertEqual(resume["variation_semaine"], -1500)


if __name__ == "__main__":
    unittest.main()
