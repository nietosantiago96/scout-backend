# Scout Analytics — Backend API

FastAPI backend que obtiene datos de Transfermarkt: valor de mercado, fin de contrato, pie dominante y % de minutos jugados.

## Endpoints

- `GET /player/{nombre}?squad={equipo}` → datos del jugador
- `GET /health` → health check

## Deploy en Railway

1. Creá una cuenta en [railway.app](https://railway.app)
2. Instalá el CLI: `npm install -g @railway/cli`
3. En la carpeta `backend/`: `railway login` → `railway init` → `railway up`
4. Copiá la URL pública que te da Railway (ej: `https://scout-api.railway.app`)
5. Pegá esa URL en el frontend: en `scout.html` buscá `BACKEND_URL` y reemplazá

## Deploy manual con GitHub

1. Subí la carpeta `backend/` a un repo de GitHub
2. En Railway → New Project → Deploy from GitHub → elegí el repo
3. Railway detecta automáticamente Python y usa el `Procfile`

## Variables de entorno (opcionales)

No se necesitan por ahora. Si Transfermarkt bloquea el scraping en producción, se puede agregar un proxy rotativo configurando:
```
PROXY_URL=http://tu-proxy:puerto
```

## Desarrollo local

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# → http://localhost:8000
# → http://localhost:8000/docs  (Swagger UI)
```
