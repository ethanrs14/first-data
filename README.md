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

1. Lit `data/calendrier-dur.json` et `scripts/produits-cibles.json` (termes, synonymes, exclusions par produit)
2. Récupère les flux RSS par catégorie (`scripts/feeds.json`)
3. Cible chaque produit avec matching précis (termes requis, exclusions, détection tension stock)
4. Attache jusqu'à 3 **articles sources** par produit (titre, URL, source, extrait)
5. Calcule un `niveau` : `confirme` | `surveille` | `faible`
6. Fusionne avec les signaux existants (décroissance 80 %)
7. Écrit `data/signaux.json` et commit si changement

### Couche 3 — Produits émergents (découverte dynamique)

En plus des produits prédéfinis du calendrier, le script détecte les **modèles/marques qui explosent** dans les flux RSS :

1. Lit `scripts/marques.json` (Midea, Daikin, Sony, Apple… par catégorie)
2. Extrait les combinaisons **marque + modèle** dans les articles (`Midea porta split`)
3. Filtre les faux positifs (exclusions, produits déjà listés dans le calendrier)
4. Score boosté si mots de tension + proximité avec une catégorie produit (`climatiseur`)
5. Écrit des signaux `type: "emergent"` — le produit star de l'année, sans mise à jour manuelle

L'app affiche ces signaux en **Produit star** (badge ambre) en priorité sur le dashboard.

### Ajouter / affiner un produit cible

Édite `scripts/produits-cibles.json` :

```json
"climatiseurs": {
  "label": "Climatiseurs portables",
  "termesPrincipaux": ["climatiseur", "clim portable"],
  "synonymes": ["air conditionne"],
  "exclusions": ["climat", "rechauffement climatique"]
}
```

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
