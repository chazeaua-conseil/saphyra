# Studio SAPHYRA — l'équipe d'agents qui produit des maquettes Fiori + IA

Le Studio tourne en continu sur le VPS. Chaque nuit, il cherche des idées d'applications SAP Fiori avec IA, les fait critiquer, construit les maquettes UI5, les teste, les documente et évalue si elles se vendent. Chaque matin, les 2 ou 3 meilleures arrivent dans l'Atelier SAPHYRA et par e-mail. Les idées qui ne passent pas une porte sont rangées au cimetière, sans notification.

```
Éclaireur ──► Panel métier ──► Architecte ──► Développeur ⇄ Testeur ──► Revue du POC ──► Évaluateur ──► Rédacteur ──► Chef de projet
 (idées)       PORTE 1         clean core      UI5        Chromium      PORTE 2          PORTE 3      (doc, en       Atelier + e-mail
                 │               │ (pas d'API)    │ (3 échecs)            │ ⟲ 1 correction  │          dernier)
                 └───────────────┴────────────────┴───────────────────────┴─────────────────┴──► cimetière
```

Le Rédacteur passe en dernier : il n'écrit que pour les idées qui ont franchi les trois portes. Aucun document n'est rédigé pour une idée qui finit au cimetière.

## Les agents

| Agent | Ce qu'il fait | Consignes | Porte |
|---|---|---|---|
| Éclaireur | Propose 5 idées par vague, sans doublon avec l'Atelier ni le cimetière | `prompts/eclaireur.md` | — |
| Panel métier | 4 personas notent l'idée séparément : terrain, processus, DSI clean core, acheteur | `prompts/panel.md`, `prompts/personas.json` | **1** : moyenne ≥ 3,0, pas 2 « stop », aucune note de 1 |
| Architecte | Vérifie les API publiées (recherche web sur api.sap.com), choisit l'archétype, conçoit les écrans et le moment IA | `prompts/architecte.md` | « stop » si aucune API publiée |
| Développeur | Écrit l'appli OpenUI5 (Horizon, données fictives, IA simulée) avec des outils de fichiers | `prompts/developpeur.md` | — |
| Testeur | Sans modèle : rejoue le scénario dans Chromium, relève erreurs JS, 404, écran vide, clic impossible | `studio/mockup.py` | 3 échecs = cimetière |
| Revue du POC | Les 4 personas regardent les captures et vérifient leurs conditions de la porte 1 | `prompts/revue.md` | **2** : moyenne ≥ 3,3 ; une seule boucle de correction |
| Évaluateur commercial | Juge le dossier (idée, avis du panel, architecture, revue de la maquette) ; recherche web sur la concurrence, acheteur, modèle économique, 6 notes | `prompts/marche.md` | **3** : score ≥ 3,2 et vendabilité ≥ 3 |
| Rédacteur | En dernier, pour les idées validées seulement : pitch, problème, parcours, règles de gestion, architecture, avis du panel, pourquoi ça se vend, risques | `prompts/redacteur.md` | — |
| Chef de projet | Fait avancer le projet le plus avancé, tient budget et quota, livre à l'heure du récap | `studio/main.py`, `studio/digest.py` | — |

Les seuils sont en tête de `studio/agents.py`. Pour durcir ou assouplir le tri, c'est là.

## Garde-fous

- **Budget** : plafond par jour (8 $ par défaut) et par projet (4 $). Chaque appel est compté dans `studio_usage` avant le suivant ; un projet qui dépasse son plafond part au cimetière.
- **Quota** : une fois 3 maquettes prêtes dans la journée, le Studio s'arrête jusqu'au lendemain.
- **Erreurs** : une étape qui échoue est retentée 2 fois, puis le projet part au cimetière avec l'erreur.
- **Éclaireur** : après 3 vagues sans idée nouvelle, il s'arrête jusqu'au lendemain.
- **Sécurité** : toutes les pages sont derrière un jeton secret dans l'URL. La clé API et le SMTP sont stockés sur le VPS (volume `studio_data`), jamais dans Git. Aucune donnée client n'est utilisée : tout est fictif.

## Où regarder

- **Tableau de bord** : `https://srv1429117.hstgr.cloud/saphyra/studio/<jeton>/`. On y trouve les maquettes à revoir, les projets en cours, le fil des agents, le cimetière avec ses raisons et le budget. Le jeton est écrit dans les journaux du conteneur au démarrage.
- **Atelier** : chaque maquette livrée devient une fiche au stade POC, avec le propriétaire « Studio IA », les liens vers la maquette et la doc, et deux tâches.
- **SQL** : `studio_projects` (une ligne par idée, avec tous les avis en JSON), `studio_events` (le fil), `studio_usage` (le coût).

## Faire évoluer

- **Changer le comportement d'un agent** : modifier son fichier dans `prompts/`, pousser, puis redémarrer le projet Docker. Le code est retéléchargé à chaque démarrage.
- **Ajouter un expert au panel** (ex. responsable HSE, magasinier) : ajouter un objet dans `prompts/personas.json`. Il participe aux portes 1 et 2.
- **Ajouter une étape** : écrire une fonction `etape(p, cfg)` dans `agents.py`, l'insérer dans `db.STAGES` et dans `STEPS`.
- **Essai à sec** (sans modèle, avec un vrai Chromium) : `DATABASE_URL=… STUDIO_DATA=/tmp/t python3 -m tests.test_pipeline`.

## Coût indicatif

Avec Sonnet 5.5 (2 $ / 10 $ par million de tokens en entrée et en sortie), compter de l'ordre de 1 à 3 $ par maquette livrée, plus quelques centimes par idée tuée tôt. Le plafond de 8 $ par jour borne la facture à environ 240 $ par mois. Les chiffres réels sont dans le tableau de bord après quelques jours.
