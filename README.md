# first-data

Données statiques pour l'app mobile **First** (calendrier d'événements + signaux RSS).

## Fichiers

| Fichier | Rôle | Mise à jour |
|---------|------|-------------|
| `data/calendrier-dur.json` | Événements à date connue | Manuelle |
| `data/signaux.json` | Scores de tendance produit | Automatique (GitHub Actions) |

## URLs consommées par l'app

```
https://raw.githubusercontent.com/ethanrs14/first-data/main/data/calendrier-dur.json
https://raw.githubusercontent.com/ethanrs14/first-data/main/data/signaux.json
```

## Automatisation des signaux RSS

Un workflow GitHub Actions (`.github/workflows/update-data.yml`) s'exécute :

- **chaque lundi à 6h UTC**
- **manuellement** via l'onglet Actions → *Update signaux RSS* → *Run workflow*

Le script `scripts/update-signaux.py` :

1. Lit `data/calendrier-dur.json`
2. Récupère les flux RSS par catégorie (`scripts/feeds.json`)
3. Compte les mentions des `produitsAsurveiller` avec pondération récence
4. Booste le score si des mots de tension apparaissent (rupture, stock, pénurie…)
5. Écrit `data/signaux.json` et commit si changement

### Tester en local

```bash
pip install -r scripts/requirements.txt
python scripts/update-signaux.py
```

### Ajouter un flux RSS

Édite `scripts/feeds.json` et ajoute l'URL dans la catégorie correspondante (`sport`, `tech`, `gaming`, `retail`, `famille`, `maison`).

## Calendrier dur (manuel)

Quand une date ou un événement change :

1. Modifie `data/calendrier-dur.json`
2. Mets à jour `derniereVerification` (ISO 8601) — l'app reprogramme les notifications locales
