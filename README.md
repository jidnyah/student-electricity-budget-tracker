# EcoGrid

Campus electricity billing with per-student bills, usage charts and a leaderboard.

## Run locally
- Windows: double-click `run.bat`
- Mac/Linux: `./run.sh`

It opens http://localhost:5000. Delete `billing.db` to reset the sample data.

## Run in the cloud
- GitHub Codespaces: Code > Codespaces > Create, then run `pip install -r requirements.txt && python app.py`.
- Render: New Web Service, build command `pip install -r requirements.txt`, start command `gunicorn app:app`.
