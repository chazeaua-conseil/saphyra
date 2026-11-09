# SAPHYRA : base de données de l'Atelier

L'Atelier SAPHYRA (artefact Claude) stockait ses fiches dans la base Notion « 💡 Idées & projets ».
Le connecteur Notion plafonne les requêtes SQL et `rows` (« Query Data Source ») : passé le quota,
la synchronisation de l'Atelier échoue pendant une heure.

Les fiches sont désormais dans **PostgreSQL 16 sur le VPS Hostinger** (`srv1429117.hstgr.cloud`),
exposées par un petit **serveur MCP** que l'Atelier appelle comme un connecteur claude.ai. Il n'y a plus de quota.

```
Atelier (artefact) ──mcp──► connecteur « Saphyra DB » ──HTTPS──► Traefik (VPS)
                                                                   └─► api (Node, server/server.mjs) ──► db (PostgreSQL 16)
                                                                                                         └─► backup (pg_dump quotidien)
```

## Contenu du dépôt

| Chemin | Rôle |
|---|---|
| `server/server.mjs` | Serveur MCP (Streamable HTTP, sans état) + schéma SQL, créé au démarrage. Seule dépendance : `pg`. |
| `vps/docker-compose.yml` | Projet Docker `saphyra` : `init` (secrets), `db`, `api`, `backup`. |
| `scripts/render-compose.sh` | Prépare le compose et les variables à envoyer à l'API Hostinger (limite de 8 192 caractères par champ). |
| `artifact/atelier-saphyra.html` | Atelier branché sur « Saphyra DB ». |
| `artifact/atelier-saphyra.notion.html` | Version précédente, branchée sur Notion (référence). |

## Modèle de données

- `ideas` : une ligne par fiche. Colonnes : `ref` (IDEE-n), `title`, `stage`, `type`, `priority`, `owner`, `tags[]`, `pitch`, `problem`, `target`,
  `value`, `business`, `next_step`, `due`, `valeur`/`effort`/`confiance` (1-5), `progress` (0-1), `body_md`, `notion_url`, `created_at`, `updated_at`.
- `body_md` : le corps de la fiche en markdown, au même format que dans Notion (`## Notes`, `## Plan d'action`, `## Journal`).
  C'est la source de vérité du corps.
- `tasks` et `journal` : recalculées depuis `body_md` à chaque écriture, pour pouvoir les interroger en SQL (échéances, tâches ouvertes…).
- `meetings` : réunions et rencontres du calendrier (date, heures, type, lieu, participants, notes, fiche liée facultative).
- `reviews` : revues du portefeuille enregistrées depuis l'Atelier.

## Outils MCP

`list_ideas`, `get_idea`, `create_idea`, `update_idea`, `add_journal_entry`, `set_task`, `delete_idea`, `search_ideas`,
`list_tasks`, `list_meetings`, `save_meeting`, `delete_meeting`, `save_review`, `list_reviews`, `import_ideas`, `query_sql` (lecture seule : rôle `saphyra_ro`, 5 s et 500 lignes au plus).

## Brancher l'Atelier

1. claude.ai › Réglages › Connecteurs › **Ajouter un connecteur personnalisé**.
   - Nom : `Saphyra DB` (l'Atelier cherche ce nom exact).
   - URL : l'URL secrète du connecteur, de la forme `https://srv1429117.hstgr.cloud/saphyra/<token>/mcp`.
     Elle est écrite dans les journaux du conteneur `saphyra-api-1` (hPanel › VPS › Docker Manager › saphyra › Logs)
     et le token est dans le volume `saphyra_secrets` (`mcp_token`).
2. Publier `artifact/atelier-saphyra.html` et déclarer le connecteur `Saphyra DB` dans les capacités de l'artefact
   (et garder `Notion` pour `notion-search` / `notion-fetch`, utilisés par l'assistant).

## Sécurité

- Le token du connecteur, le mot de passe PostgreSQL et celui du rôle lecture seule sont générés **sur le VPS** par le service `init`
  (volume `saphyra_secrets`). Ils ne sont ni dans Git ni dans l'API Hostinger.
- Le token est dans l'URL : toute personne qui la possède lit et écrit les fiches. Ne pas la partager.
  Pour la changer : supprimer `/secrets/mcp_token` dans le volume, redémarrer le projet, mettre à jour le connecteur.
- PostgreSQL n'est pas exposé sur Internet (réseau Docker interne uniquement).

## Exploitation

- **Santé** : `https://srv1429117.hstgr.cloud/saphyra/health` renvoie `{"ok":true,"ideas":…,"tasks":…}`.
- **Mettre à jour le code** : modifier `server/server.mjs`, lancer `scripts/render-compose.sh /tmp/deploy`, puis redéployer le projet
  `saphyra` avec `/tmp/deploy/compose.yml` (champ *content*) et `/tmp/deploy/env` (champ *environment*). Les volumes sont conservés.
- **Sauvegardes** : `pg_dump` quotidien dans le volume `saphyra_backups`, 14 jours de rétention.
  Restauration : `docker exec -i saphyra-db-1 pg_restore -U saphyra -d saphyra --clean < saphyra-AAAA-MM-JJ.dump`.
- **SQL direct** (depuis le VPS) : `docker exec -it saphyra-db-1 psql -U saphyra saphyra`.

> Taille du code : l'API Hostinger limite chaque champ à 8 192 caractères. Le code compressé occupe déjà environ 13,1 k
> sur les 13,3 k disponibles (compose + variables). Pour une prochaine évolution, minifier `server.mjs` avant le rendu
> ou passer à un dépôt public pour le compose.
