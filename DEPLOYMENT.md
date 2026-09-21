# Public deployment

This project uses SQLite locally and PostgreSQL when DATABASE_URL is set.

## Important for permanent records
Do NOT use the local SQLite file as the online production database on Render: Render web-service filesystems are ephemeral. Use a hosted PostgreSQL database.

### Render + PostgreSQL
1. Push this folder to a private GitHub repository.
2. Create a PostgreSQL database with a provider such as Supabase.
3. Copy its PostgreSQL connection string.
4. Create a Render Web Service from the GitHub repository.
5. Build command: `pip install -r requirements.txt`
6. Start command: `gunicorn --bind 0.0.0.0:$PORT app:app`
7. Add environment variables: `DATABASE_URL`, `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`.
8. Deploy and open the generated `onrender.com` URL.

## Record deletion rule
All management pages require admin login. Delete routes are protected by `login_required`. There is no automatic deletion or cleanup job in this application. Records are deleted only when the authenticated admin uses a Delete action.

## Database note
On first connection to an empty PostgreSQL database, the app creates its tables and sample records. For a real deployment, remove the sample seed section after the first deployment if you do not want demo records.

## Free-tier warning
Render Free web services can be used for testing, but their local filesystem is ephemeral. Render Free Postgres currently expires after 30 days. Supabase Free Postgres is 500 MB and can pause after 1 week of inactivity; data is preserved while paused. For a continuously available production system, use a paid database/service or maintain regular backups.
