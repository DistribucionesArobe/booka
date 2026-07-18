# Bookaholic Mexicana — Setup Guide

## Estructura del proyecto

```
booka/
├── main.py                  # FastAPI app (todo en un archivo)
├── requirements.txt         # Dependencias Python
├── render.yaml              # Config de Render (blueprint)
└── templates/
    ├── base.html            # Layout base con CSS
    ├── home.html            # Grid de resenas
    ├── post.html            # Resena individual (SEO + Schema.org)
    ├── about.html           # Sobre mi
    └── collaborations.html  # Pagina para editoriales
```

## Deploy en Render

### 1. Configurar el Web Service (ya lo tienes)

- **Service**: `booka` en `booka-13nj.onrender.com`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### 2. Crear la base de datos

En Render Dashboard > New > PostgreSQL:
- Name: `booka-db`
- Plan: Free

Copia el **Internal Database URL** y agrégalo como env var.

### 3. Environment Variables en Render

| Variable | Valor | Notas |
|----------|-------|-------|
| `DATABASE_URL` | `postgresql://...` | Internal URL de tu DB en Render |
| `IG_ACCESS_TOKEN` | Token de Instagram | Ver paso 4 |
| `IG_USER_ID` | ID numerico de la cuenta IG | Ver paso 4 |
| `AMAZON_AFFILIATE_TAG` | `bookaholicmx-20` | Tu tag de Amazon Afiliados |
| `SITE_URL` | `https://bookaholicmexicana.com` | URL del sitio |
| `ADMIN_SECRET` | Cualquier string secreto | Para proteger el endpoint de sync |

### 4. Obtener token de Instagram

Tu esposa necesita cuenta de Creator/Business en Instagram.

1. Ve a https://developers.facebook.com
2. Crea una app tipo "Business"
3. Agrega el producto "Instagram Graph API"
4. En Graph API Explorer:
   - Selecciona tu app
   - Genera User Token con permisos: `instagram_basic`, `pages_read_engagement`
   - Obtiene el token
5. Para obtener el IG_USER_ID:
   ```
   GET https://graph.instagram.com/me?fields=id,username&access_token=TOKEN
   ```
6. IMPORTANTE: El token expira. Para uno de larga duracion:
   ```
   GET https://graph.instagram.com/access_token
       ?grant_type=ig_exchange_token
       &client_secret=APP_SECRET
       &access_token=SHORT_TOKEN
   ```
   Esto da un token de 60 dias. Configura un cron para renovarlo.

### 5. Conectar dominio en Render

En tu servicio de Render > Settings > Custom Domains:
1. Agrega `bookaholicmexicana.com`
2. Agrega `www.bookaholicmexicana.com`
3. Render te dara los DNS records

En Namecheap, cambia los A Records:
- Borra los A Records actuales (162.159.142.117 y 172.66.2.113 son de Cloudflare, no de Render)
- Agrega los que Render te indique
- El CNAME de www apuntalo a donde Render diga

### 6. Sincronizar Instagram

Una vez configurado, sincroniza las resenas:

```bash
curl -X POST https://bookaholicmexicana.com/api/sync-instagram \
  -H "x-admin-secret: TU_ADMIN_SECRET"
```

Esto jala los ultimos 50 posts de Instagram y los convierte en articulos del blog.

### 7. Amazon Afiliados

1. Registrate en https://afiliados.amazon.com.mx
2. Obtiene tu tag (ej: bookaholicmx-20)
3. Agrégalo como env var AMAZON_AFFILIATE_TAG
4. Cada resena automaticamente genera un link de busqueda en Amazon con tu tag

Para poner un link directo a un libro especifico:
```bash
curl -X POST "https://bookaholicmexicana.com/api/posts/1/amazon-url?amazon_url=https://amazon.com.mx/dp/XXXXX" \
  -H "x-admin-secret: TU_ADMIN_SECRET"
```

## Cron automático (opcional)

Para sincronizar automaticamente cada dia, usa un Cron Job en Render:
- Command: `curl -X POST https://bookaholicmexicana.com/api/sync-instagram -H "x-admin-secret: $ADMIN_SECRET"`
- Schedule: `0 12 * * *` (diario a medio dia)

## URLs del sitio

- `/` — Home con grid de resenas
- `/resena/{slug}` — Resena individual (SEO + Schema.org para Google)
- `/genero/{nombre}` — Filtrar por genero
- `/sobre-mi` — About page
- `/colaboraciones` — Media Kit para editoriales
- `/sitemap.xml` — Sitemap para Google Search Console
- `/robots.txt` — Para crawlers
- `/feed.xml` — RSS feed
- `/health` — Health check

## Monetización futura

1. **Amazon Afiliados** — Ya integrado, cada resena tiene link
2. **Google AdSense** — Cuando tengas trafico, agrega el script en base.html
3. **Resenas patrocinadas** — La pagina /colaboraciones es tu media kit
4. **Newsletter** — Agrega Substack o Mailchimp link en el header
