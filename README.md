Telegram Poll Bot - Advanced (Railway deployment)

Files included:
- telegram_poll_bot_advanced_final.py  (patched bot with admin, delete_previous, dashboard)
- Dockerfile
- requirements.txt
- migrate_sqlite_to_postgres.py  (migration helper)
- README (this file)

Deployment steps (Railway)
1. Create a new GitHub repo and push these files (or upload them directly to Railway).
2. On Railway, create a new project -> Deploy from GitHub, select the repo.
3. Set environment variables in Railway (Project -> Variables):
   - BOT_TOKEN = <your bot token>
   - DATABASE_URL = <postgres connection string>  (optional: if omitted, bot uses SQLite file polls.db)
   - DASHBOARD_HOST = https://<your-railway-app>.up.railway.app  (used to generate dashboard links)
   - TIMEZONE = Europe/Rome  (optional)
4. Deploy. Railway will build the docker container and start the bot.
5. To migrate existing SQLite data to Postgres (if you have polls.db locally):
   - Copy polls.db into the project directory or upload it to the server
   - Run locally or on the server:
       DATABASE_URL=<your_postgres_dsn> python migrate_sqlite_to_postgres.py --sqlite polls.db
   - Check logs for inserted rows.

Notes:
- Test in a staging environment first.
- Keep backups of your SQLite DB before performing migration.
- If you prefer, I can run the migration for you if you provide the sqlite file and DATABASE_URL (handle secrets securely).

