# Vigie pétrole

Une application gratuite qui suit les pétroliers sur une carte du monde et qui, chaque jour, rédige une analyse des points du transport pétrolier susceptibles d'affecter les marchés financiers.

- **Carte** : les navires-citernes repérés par l'AIS, en rouge sur un fond en niveaux de gris, orientés selon leur cap. Un navire plein est en route, un navire en contour est à l'arrêt, un navire pâle n'a pas été revu depuis plus de 3 heures. Un clic affiche sa fiche (vitesse, destination, tirant d'eau…).
- **Détroits** : les passages quotidiens de navires-citernes publiés par IMF PortWatch (Ormuz, Bab el-Mandeb, Suez, Malacca…), comparés aux 4 semaines précédentes et à l'an dernier.
- **Marchés** : Brent, WTI, gaz européen, S&P 500, VIX, taux américain à 10 ans, dollar, euro, or, et stocks de brut américains.
- **Analyse du jour** : un agent (Gemini) consulte ces données avec six outils en lecture seule, cherche dans une bibliothèque de quatorze articles scientifiques, puis rédige un rapport en cinq parties. Un contrôle automatique vérifie la structure et signale toute citation qui n'est pas dans la bibliothèque.

Tout tourne sur GitHub : une tâche planifiée collecte les données chaque heure et publie le site sur GitHub Pages. Il n'y a pas de serveur à louer.

---

## Mise en ligne, pas à pas

Compte environ 15 minutes. Tu as besoin d'un compte GitHub.

### 1. Clé aisstream (positions des navires)

1. Va sur [aisstream.io](https://aisstream.io) et connecte-toi (avec ton compte GitHub).
2. Ouvre la page **API Keys** et crée une clé.
3. Copie-la dans une note, tu en auras besoin à l'étape 3.

### 2. Clé Gemini (l'agent qui rédige l'analyse)

1. Va sur [Google AI Studio](https://aistudio.google.com) et connecte-toi avec un compte Google.
2. Clique sur **Get API key**, puis **Create API key**.
3. Copie la clé.

Le palier gratuit suffit largement : l'agent fait une quinzaine d'appels par jour. Sur ce palier, Google peut utiliser les requêtes pour améliorer ses modèles et l'usage commercial est exclu ; ici les données envoyées sont publiques.

*Facultatif* : une clé gratuite de l'EIA ([inscription](https://www.eia.gov/opendata/register.php)) ajoute les stocks hebdomadaires de brut américains.

### 3. Le dépôt GitHub et ses secrets

1. Crée un dépôt **public** nommé `vigie-petrole` et mets-y les fichiers du projet. Le dépôt doit être public pour que GitHub Pages et les minutes de calcul soient gratuits.
2. Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**. Crée :
   - `AISSTREAM_API_KEY` avec la clé de l'étape 1 ;
   - `GEMINI_API_KEY` avec la clé de l'étape 2 ;
   - `EIA_API_KEY` si tu en as une.

Les secrets ne sont jamais visibles dans le site ni dans le code : seule la tâche planifiée y a accès.

### 4. Activer le site et le premier lancement

1. **Settings → Pages → Build and deployment → Source** : choisis **GitHub Actions**.
2. Onglet **Actions** → **Vigie pétrole** → **Run workflow** → **Run workflow**.
3. Attends une douzaine de minutes (8 minutes d'écoute du flux AIS, puis PortWatch, les marchés et l'analyse).
4. Ton application est en ligne à l'adresse `https://<ton-pseudo>.github.io/vigie-petrole/`.

Ensuite, tout est automatique : les positions sont mises à jour chaque heure et l'analyse une fois par jour. Pour régénérer l'analyse tout de suite, relance **Run workflow**.

---

## Voir la démo sur ton ordinateur

Sans aucune clé, avec des données fictives :

```bash
python -m pipeline.run --demo --out _site
python -m http.server --directory _site 8000
```

Puis ouvre http://localhost:8000 dans ton navigateur.

---

## Comment ça marche

```
Chaque heure (GitHub Actions)
  1. Récupère les fichiers du passage précédent depuis le site publié  ->  pipeline/state.py
  2. Écoute le flux AIS mondial pendant 8 minutes                      ->  pipeline/collect_ais.py
     garde les navires-citernes (types AIS 80 à 89)
Une fois par jour
  3. Télécharge les passages dans les détroits (PortWatch)            ->  pipeline/portwatch.py
  4. Télécharge les cours (Yahoo Finance, FRED en secours, EIA)        ->  pipeline/markets.py
  5. L'agent rédige l'analyse                                          ->  pipeline/agent.py
Puis
  6. Publie le site (site/index.html + data/*.json) sur GitHub Pages
```

**Pourquoi relire le site publié ?** GitHub Actions repart d'une machine vierge à chaque fois. Enregistrer les données dans le dépôt ferait grossir l'historique Git de plusieurs Mo par jour ; le site publié sert donc de mémoire d'un passage à l'autre.

**Pourquoi un registre des citernes ?** Les messages de position AIS ne disent pas quel type de navire les envoie. Ce sont les messages statiques, émis environ toutes les 6 minutes, qui donnent le type. Le registre (`tanker_registry.json`) se remplit au fil des passages : la carte est donc plus complète après quelques heures qu'au premier lancement.

### L'agent

La boucle est écrite à la main avec l'API REST de Gemini, sans bibliothèque, pour que chaque étape soit lisible dans `pipeline/agent.py` :

1. le modèle reçoit les consignes et la liste des outils ;
2. il demande un outil, par exemple `etat_des_detroits` ;
3. le programme exécute l'outil (`pipeline/tools.py`) et lui renvoie le résultat ;
4. on recommence jusqu'à ce qu'il rende son texte.

Les six outils ne font que lire les fichiers déjà collectés : l'agent ne peut ni écrire, ni naviguer sur Internet, ni exécuter de code. Les consignes lui imposent de n'utiliser que les chiffres fournis, de dater les données, de séparer faits et interprétations, et de ne citer que les articles de la bibliothèque.

Après la rédaction, `controler()` vérifie les cinq titres et repère les citations inconnues ; la section Sources est ajoutée par le programme, pas par le modèle. Sans clé Gemini, ou si l'appel échoue, un rapport construit par des règles fixes prend le relais.

### La bibliothèque scientifique

`pipeline/litterature.json` contient quatorze articles (Kilian 2009, Kilian et Park 2009, Hamilton 1983, Känzig 2021, Caldara et Iacoviello 2022, travaux du FMI sur l'AIS…), chacun avec un résumé et la façon de s'en servir. **Les résumés ont été rédigés par Claude : vérifie-les en lisant les articles.** C'est la partie que tu peux enrichir avec tes propres lectures de master.

---

## Limites à connaître

- Le flux AIS gratuit repose surtout sur des antennes côtières : la haute mer est mal couverte, et un navire qui coupe son AIS disparaît.
- Les types AIS 80 à 89 regroupent tous les navires-citernes : pétrole brut, produits raffinés, chimie et gaz.
- PortWatch publie chaque semaine avec quelques jours de décalage.
- aisstream est encore en bêta et peut être instable ; le collecteur se reconnecte automatiquement.
- Yahoo Finance n'a pas d'API officielle ; si une série échoue, FRED prend le relais quand c'est possible.
- GitHub met en pause les tâches planifiées d'un dépôt public après 60 jours sans activité sur le dépôt. Un commit ou un clic sur **Enable workflow** suffit à les relancer.

---

## Sécurité

- **Clés** : uniquement dans les secrets GitHub, jamais dans le code ni dans le site. GitHub les masque dans les journaux, et le programme retire toute clé des messages d'erreur avant de les écrire dans les fichiers publiés (`masquer()` dans `pipeline/util.py`).
- **Droits de la tâche planifiée** : aucun droit par défaut. La tâche qui manipule les clés ne peut que lire le dépôt ; seule la tâche de publication peut écrire sur GitHub Pages.
- **Actions GitHub** : figées sur un commit précis, pour qu'une mise à jour malveillante d'une action ne puisse pas s'exécuter chez toi.
- **Contributions extérieures** : une demande de fusion venue d'un autre compte lance seulement les tests, sans accès aux clés.
- **Textes venus de l'AIS** : n'importe qui peut émettre un nom de navire. Ces textes sont échappés avant affichage, l'analyse est nettoyée par DOMPurify, et l'agent ne reçoit que des comptages, jamais les noms ou destinations des navires.
- **Ton compte** : active la double authentification sur GitHub, Google et aisstream. Si une clé a circulé (dans une conversation par exemple), régénère-la et remplace le secret.

## Personnaliser

| Réglage | Où | Effet |
|---|---|---|
| Durée d'écoute AIS | `AIS_MINUTES` dans `pipeline/config.py` | 8 minutes par défaut |
| Zones du tableau | `ZONES` dans `pipeline/config.py` | rectangles latitude/longitude |
| Modèle Gemini | variable de dépôt `GEMINI_MODEL` (**Settings → Variables**) | par défaut `gemini-flash-latest` |
| Fréquence de l'analyse | `ANALYSE_TOUTES_LES_HEURES` | 24 heures par défaut |
| Articles | `pipeline/litterature.json` | ajoute un article avec ses thèmes |

---

## Tests

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -t . -v
```

38 tests : traitement des messages AIS, faux serveur aisstream en local, pagination PortWatch, calculs de marché, outils de l'agent, boucle de l'agent contre un faux serveur Gemini (appels d'outils, repli, modèle introuvable), contrôle des citations, masquage des clés et construction du site. Ils tournent aussi automatiquement sur GitHub à chaque modification.

## Structure

```
pipeline/
  config.py         zones, détroits, séries de marché, réglages
  collect_ais.py    écoute du flux AIS et registre des citernes
  portwatch.py      passages dans les détroits
  markets.py        cours et stocks
  tools.py          les six outils de l'agent (lecture seule)
  agent.py          boucle Gemini, contrôle, rapport de secours
  litterature.json  bibliothèque d'articles
  state.py          relecture du passage précédent
  demo.py           données fictives
  run.py            orchestrateur
site/
  index.html        l'application (Leaflet, carte Natural Earth)
  geo/              côtes et frontières (Natural Earth, domaine public)
tests/
.github/workflows/  tâche horaire et tests
```

## Sources des données

- Positions : [aisstream.io](https://aisstream.io)
- Détroits : [IMF PortWatch](https://portwatch.imf.org), données AIS du UN Global Platform
- Cours : Yahoo Finance, [FRED](https://fred.stlouisfed.org), [EIA](https://www.eia.gov)
- Fond de carte : [Natural Earth](https://www.naturalearthdata.com), domaine public
- Carte : [Leaflet](https://leafletjs.com)
