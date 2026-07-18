"""
Bookaholic Mexicana — Blog engine + Instagram sync
Deploys on Render (FastAPI + PostgreSQL)
"""
import os
import json
import re
import requests
from datetime import datetime, timezone
from typing import Optional

import psycopg2
from fastapi import FastAPI, Request, Query, HTTPException, Body
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Bookaholic Mexicana", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://bookaholicmexicana.com", "https://www.bookaholicmexicana.com"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
IG_ACCESS_TOKEN = (os.getenv("IG_ACCESS_TOKEN") or "").strip()
IG_USER_ID = (os.getenv("IG_USER_ID") or "").strip()
AMAZON_AFFILIATE_TAG = (os.getenv("AMAZON_AFFILIATE_TAG") or "bookaholicmex-20").strip()
SITE_URL = (os.getenv("SITE_URL") or "https://bookaholicmexicana.com").strip()
SITE_NAME = "Bookaholic Mexicana"
SITE_DESCRIPTION = "Reseñas honestas de libros, recomendaciones literarias y guía de lectura. Descubre qué libro leer: novela contemporánea, ficción, romance, fantasía, thriller y más. Book reviews en español desde México."
IG_HANDLE = "bookaholicmexicana"

# ── Templates ─────────────────────────────────────────────────────────────────
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
jinja_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)

# ── DB ────────────────────────────────────────────────────────────────────────
def get_conn():
    dsn = DATABASE_URL
    if not dsn:
        raise RuntimeError("DATABASE_URL missing")
    if "sslmode=" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    conn = psycopg2.connect(dsn, connect_timeout=5)
    conn.autocommit = True
    return conn


def run_migrations():
    """Create tables if they don't exist."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                ig_id VARCHAR(100) UNIQUE,
                slug VARCHAR(300) UNIQUE NOT NULL,
                title VARCHAR(500) NOT NULL,
                body TEXT NOT NULL,
                excerpt VARCHAR(600),
                image_url TEXT,
                image_local TEXT,
                book_title VARCHAR(500),
                book_author VARCHAR(300),
                amazon_url TEXT,
                genre VARCHAR(100),
                rating SMALLINT,
                ig_permalink TEXT,
                ig_timestamp TIMESTAMP,
                published BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) UNIQUE NOT NULL,
                slug VARCHAR(100) UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS post_categories (
                post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
                category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                PRIMARY KEY (post_id, category_id)
            );

            CREATE INDEX IF NOT EXISTS idx_posts_slug ON posts(slug);
            CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(published);
            CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_posts_genre ON posts(genre);
        """)
        print("MIGRATIONS: OK")
    except Exception as e:
        print(f"MIGRATION ERROR: {repr(e)}")
    finally:
        cur.close()
        conn.close()


# Run migrations at startup
try:
    run_migrations()
except Exception as e:
    print(f"STARTUP MIGRATION ERROR: {repr(e)}")


# ── Helpers ───────────────────────────────────────────────────────────────────
def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    s = (text or "").lower().strip()
    s = re.sub(r"[áà]", "a", s)
    s = re.sub(r"[éè]", "e", s)
    s = re.sub(r"[íì]", "i", s)
    s = re.sub(r"[óò]", "o", s)
    s = re.sub(r"[úù]", "u", s)
    s = re.sub(r"ñ", "n", s)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:200] if s else "sin-titulo"


def make_amazon_link(query: str) -> str:
    """Build Amazon search link with affiliate tag."""
    q = requests.utils.quote(query)
    return f"https://www.amazon.com.mx/s?k={q}&tag={AMAZON_AFFILIATE_TAG}"


def extract_book_info(caption: str) -> dict:
    """Try to extract book title, author, rating from IG caption."""
    info = {"title": "", "author": "", "rating": None, "genre": ""}

    # Common patterns in bookstagram captions
    # "📖 Título del libro" or "📚 Título"
    title_match = re.search(r"[📖📚📕📗📘📙]\s*(.+?)(?:\n|$)", caption)
    if title_match:
        info["title"] = title_match.group(1).strip().strip("*_")

    # "✍️ Autor" or "Autor: Nombre"
    author_match = re.search(r"(?:✍️|👤|Autor[a]?[:\s]+)(.+?)(?:\n|$)", caption, re.IGNORECASE)
    if author_match:
        info["author"] = author_match.group(1).strip().strip("*_")

    # Rating: "⭐⭐⭐⭐" or "4/5" or "Rating: 4"
    stars = len(re.findall(r"⭐", caption))
    if stars >= 1:
        info["rating"] = min(stars, 5)
    else:
        rating_match = re.search(r"(\d)[/]5|Rating[:\s]*(\d)", caption, re.IGNORECASE)
        if rating_match:
            info["rating"] = int(rating_match.group(1) or rating_match.group(2))

    # Genre hints
    genre_map = {
        "romance": "Romance", "fantasía": "Fantasía", "fantasy": "Fantasía",
        "thriller": "Thriller", "misterio": "Misterio", "mystery": "Misterio",
        "ciencia ficción": "Ciencia Ficción", "sci-fi": "Ciencia Ficción",
        "terror": "Terror", "horror": "Terror",
        "contemporáneo": "Contemporáneo", "contemporary": "Contemporáneo",
        "histórico": "Histórico", "historical": "Histórico",
        "young adult": "Young Adult", "ya": "Young Adult",
        "no ficción": "No Ficción", "non-fiction": "No Ficción",
        "poesía": "Poesía", "poetry": "Poesía",
        "clásico": "Clásico", "classic": "Clásico",
    }
    caption_lower = caption.lower()
    for keyword, genre in genre_map.items():
        if keyword in caption_lower:
            info["genre"] = genre
            break

    return info


def caption_to_html(caption: str) -> str:
    """Convert Instagram caption to clean HTML paragraphs."""
    # Remove hashtags block at the end
    caption = re.sub(r"(?:\n\s*)?(?:#\w+\s*){3,}$", "", caption).strip()
    # Convert line breaks to paragraphs
    paragraphs = [p.strip() for p in caption.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in caption.split("\n") if p.strip()]
    html_parts = []
    for p in paragraphs:
        # Bold text: *text* → <strong>text</strong>
        p = re.sub(r"\*(.+?)\*", r"<strong>\1</strong>", p)
        # Italic: _text_ → <em>text</em>
        p = re.sub(r"_(.+?)_", r"<em>\1</em>", p)
        html_parts.append(f"<p>{p}</p>")
    return "\n".join(html_parts)


def make_excerpt(text: str, max_len: int = 160) -> str:
    """Create SEO-friendly excerpt from caption."""
    clean = re.sub(r"[#@]\w+", "", text)  # Remove hashtags/mentions
    clean = re.sub(r"[📖📚📕📗📘📙⭐✍️👤🌟💫✨🔥❤️💜📌🎭]+", "", clean)  # Remove emojis
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) <= max_len:
        return clean
    return clean[:max_len].rsplit(" ", 1)[0] + "..."


# ── Instagram Sync ────────────────────────────────────────────────────────────
def fetch_instagram_posts(limit: int = 25) -> list:
    """Fetch recent posts from Instagram Graph API."""
    if not IG_ACCESS_TOKEN or not IG_USER_ID:
        print("IG SYNC: Missing IG_ACCESS_TOKEN or IG_USER_ID")
        return []

    url = f"https://graph.instagram.com/{IG_USER_ID}/media"
    params = {
        "fields": "id,caption,media_type,media_url,permalink,timestamp,thumbnail_url",
        "access_token": IG_ACCESS_TOKEN,
        "limit": limit,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except Exception as e:
        print(f"IG FETCH ERROR: {repr(e)}")
        return []


def sync_instagram_to_db():
    """Pull new IG posts and insert into DB."""
    posts = fetch_instagram_posts(limit=50)
    if not posts:
        return {"synced": 0, "skipped": 0}

    conn = get_conn()
    cur = conn.cursor()
    synced = 0
    skipped = 0

    try:
        for post in posts:
            ig_id = post.get("id", "")
            caption = (post.get("caption") or "").strip()
            media_type = post.get("media_type", "")
            media_url = post.get("media_url") or post.get("thumbnail_url") or ""
            permalink = post.get("permalink", "")
            timestamp = post.get("timestamp", "")

            # Skip videos without caption, reels without text
            if not caption or len(caption) < 50:
                skipped += 1
                continue

            # Check if already exists
            cur.execute("SELECT id FROM posts WHERE ig_id = %s", (ig_id,))
            if cur.fetchone():
                skipped += 1
                continue

            # Extract book info from caption
            book = extract_book_info(caption)

            # Generate title: use book title if found, else first line of caption
            if book["title"]:
                title = f"Reseña: {book['title']}"
                if book["author"]:
                    title += f" — {book['author']}"
            else:
                first_line = caption.split("\n")[0].strip()
                first_line = re.sub(r"[📖📚📕📗📘📙⭐✍️]+", "", first_line).strip()
                title = first_line[:120] if first_line else f"Reseña #{ig_id[-6:]}"

            slug = slugify(title)
            # Ensure unique slug
            cur.execute("SELECT COUNT(*) FROM posts WHERE slug LIKE %s", (f"{slug}%",))
            count = cur.fetchone()[0]
            if count > 0:
                slug = f"{slug}-{count + 1}"

            body_html = caption_to_html(caption)
            excerpt = make_excerpt(caption)

            # Amazon affiliate link
            amazon_search = book["title"] or title
            if book["author"]:
                amazon_search += f" {book['author']}"
            amazon_url = make_amazon_link(amazon_search)

            # Parse timestamp
            ig_ts = None
            if timestamp:
                try:
                    ig_ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except Exception:
                    ig_ts = None

            cur.execute("""
                INSERT INTO posts
                    (ig_id, slug, title, body, excerpt, image_url, book_title,
                     book_author, amazon_url, genre, rating, ig_permalink, ig_timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ig_id) DO NOTHING
            """, (
                ig_id, slug, title, body_html, excerpt, media_url,
                book["title"] or None, book["author"] or None,
                amazon_url, book["genre"] or None, book["rating"],
                permalink, ig_ts,
            ))
            synced += 1

        return {"synced": synced, "skipped": skipped, "total_fetched": len(posts)}
    except Exception as e:
        print(f"IG SYNC ERROR: {repr(e)}")
        return {"synced": synced, "skipped": skipped, "error": str(e)}
    finally:
        cur.close()
        conn.close()


# ── Page data helpers ─────────────────────────────────────────────────────────
def get_recent_posts(limit: int = 12, offset: int = 0, genre: str = None):
    conn = get_conn()
    cur = conn.cursor()
    try:
        if genre:
            cur.execute("""
                SELECT id, slug, title, excerpt, image_url, book_title, book_author,
                       genre, rating, created_at
                FROM posts
                WHERE published = TRUE AND genre = %s
                ORDER BY COALESCE(ig_timestamp, created_at) DESC
                LIMIT %s OFFSET %s
            """, (genre, limit, offset))
        else:
            cur.execute("""
                SELECT id, slug, title, excerpt, image_url, book_title, book_author,
                       genre, rating, created_at
                FROM posts
                WHERE published = TRUE
                ORDER BY COALESCE(ig_timestamp, created_at) DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
        rows = cur.fetchall()
        return [
            {
                "id": r[0], "slug": r[1], "title": r[2], "excerpt": r[3],
                "image_url": r[4], "book_title": r[5], "book_author": r[6],
                "genre": r[7], "rating": r[8],
                "created_at": r[9].strftime("%d %b %Y") if r[9] else "",
            }
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()


def get_post_by_slug(slug: str):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, slug, title, body, excerpt, image_url, book_title, book_author,
                   amazon_url, genre, rating, ig_permalink, ig_timestamp, created_at
            FROM posts
            WHERE slug = %s AND published = TRUE
            LIMIT 1
        """, (slug,))
        r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "slug": r[1], "title": r[2], "body": r[3],
            "excerpt": r[4], "image_url": r[5], "book_title": r[6],
            "book_author": r[7], "amazon_url": r[8], "genre": r[9],
            "rating": r[10], "ig_permalink": r[11],
            "ig_timestamp": r[12].strftime("%d %b %Y") if r[12] else "",
            "created_at": r[13].strftime("%d %b %Y") if r[13] else "",
        }
    finally:
        cur.close()
        conn.close()


def get_all_genres():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT genre, COUNT(*) as cnt
            FROM posts
            WHERE published = TRUE AND genre IS NOT NULL AND genre != ''
            GROUP BY genre
            ORDER BY cnt DESC
        """)
        return [{"name": r[0], "count": r[1]} for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def get_total_posts(genre: str = None) -> int:
    conn = get_conn()
    cur = conn.cursor()
    try:
        if genre:
            cur.execute("SELECT COUNT(*) FROM posts WHERE published = TRUE AND genre = %s", (genre,))
        else:
            cur.execute("SELECT COUNT(*) FROM posts WHERE published = TRUE")
        return cur.fetchone()[0]
    finally:
        cur.close()
        conn.close()


# ── Template rendering ────────────────────────────────────────────────────────
def render_template(template_name: str, **kwargs) -> HTMLResponse:
    kwargs.setdefault("site_name", SITE_NAME)
    kwargs.setdefault("site_url", SITE_URL)
    kwargs.setdefault("site_description", SITE_DESCRIPTION)
    kwargs.setdefault("ig_handle", IG_HANDLE)
    kwargs.setdefault("amazon_tag", AMAZON_AFFILIATE_TAG)
    kwargs.setdefault("genres", get_all_genres())
    kwargs.setdefault("current_year", datetime.now().year)
    tmpl = jinja_env.get_template(template_name)
    html = tmpl.render(**kwargs)
    return HTMLResponse(content=html)


# ── Routes: Pages ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home(page: int = Query(default=1, ge=1)):
    per_page = 12
    offset = (page - 1) * per_page
    posts = get_recent_posts(limit=per_page, offset=offset)
    total = get_total_posts()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "home.html",
        posts=posts,
        page=page,
        total_pages=total_pages,
        meta_title=f"Reseñas de Libros y Recomendaciones Literarias — {SITE_NAME}",
        meta_description=SITE_DESCRIPTION,
    )


@app.get("/resena/{slug}", response_class=HTMLResponse)
def post_detail(slug: str):
    post = get_post_by_slug(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")

    # Structured data for Google (Book review schema)
    schema = {
        "@context": "https://schema.org",
        "@type": "Review",
        "itemReviewed": {
            "@type": "Book",
            "name": post["book_title"] or post["title"],
        },
        "author": {"@type": "Person", "name": SITE_NAME},
        "reviewBody": post["excerpt"],
        "url": f"{SITE_URL}/resena/{slug}",
    }
    if post["book_author"]:
        schema["itemReviewed"]["author"] = {"@type": "Person", "name": post["book_author"]}
    if post["rating"]:
        schema["reviewRating"] = {
            "@type": "Rating",
            "ratingValue": post["rating"],
            "bestRating": 5,
        }
    if post["image_url"]:
        schema["itemReviewed"]["image"] = post["image_url"]

    return render_template(
        "post.html",
        post=post,
        schema_json=json.dumps(schema, ensure_ascii=False),
        meta_title=f"{post['title']} — {SITE_NAME}",
        meta_description=post["excerpt"] or "",
        meta_image=post["image_url"] or "",
    )


@app.get("/genero/{genre_name}", response_class=HTMLResponse)
def genre_page(genre_name: str, page: int = Query(default=1, ge=1)):
    per_page = 12
    offset = (page - 1) * per_page
    posts = get_recent_posts(limit=per_page, offset=offset, genre=genre_name)
    total = get_total_posts(genre=genre_name)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "home.html",
        posts=posts,
        page=page,
        total_pages=total_pages,
        current_genre=genre_name,
        meta_title=f"Libros de {genre_name}: Reseñas y Recomendaciones — {SITE_NAME}",
        meta_description=f"Descubre los mejores libros de {genre_name}. Reseñas honestas, recomendaciones y calificaciones de novelas de {genre_name} por Bookaholic Mexicana.",
    )


@app.get("/sobre-mi", response_class=HTMLResponse)
def about():
    return render_template(
        "about.html",
        meta_title=f"Sobre mí — {SITE_NAME} | Bookstagrammer y Reseñadora de Libros",
        meta_description=f"Conoce a Bookaholic Mexicana: esposa, mamá, abogada y lectora apasionada. Reseñas honestas de libros en español con casi 10,000 lectores.",
    )


@app.get("/colaboraciones", response_class=HTMLResponse)
def collaborations():
    return render_template(
        "collaborations.html",
        meta_title=f"Colaboraciones con Editoriales y Autores — {SITE_NAME}",
        meta_description=f"¿Eres editorial, autor o marca literaria? Colabora con Bookaholic Mexicana. Reseñas de libros, partnerships y promoción con +10K lectores.",
    )


# ── Routes: SEO ───────────────────────────────────────────────────────────────
@app.get("/sitemap.xml")
def sitemap():
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT slug, COALESCE(ig_timestamp, created_at) as date
            FROM posts WHERE published = TRUE
            ORDER BY date DESC
        """)
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    urls = [
        f'<url><loc>{SITE_URL}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>',
        f'<url><loc>{SITE_URL}/sobre-mi</loc><changefreq>monthly</changefreq><priority>0.5</priority></url>',
        f'<url><loc>{SITE_URL}/colaboraciones</loc><changefreq>monthly</changefreq><priority>0.6</priority></url>',
    ]
    for slug, date in rows:
        date_str = date.strftime("%Y-%m-%d") if date else ""
        urls.append(
            f'<url><loc>{SITE_URL}/resena/{slug}</loc>'
            f'<lastmod>{date_str}</lastmod>'
            f'<changefreq>monthly</changefreq><priority>0.8</priority></url>'
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{"".join(urls)}
</urlset>"""
    return Response(content=xml, media_type="application/xml")


@app.get("/robots.txt")
def robots():
    content = f"""User-agent: *
Allow: /
Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")


@app.get("/feed.xml")
def rss_feed():
    posts = get_recent_posts(limit=20)
    items = []
    for p in posts:
        items.append(f"""
        <item>
            <title>{p['title']}</title>
            <link>{SITE_URL}/resena/{p['slug']}</link>
            <description><![CDATA[{p['excerpt']}]]></description>
            <pubDate>{p['created_at']}</pubDate>
            <guid>{SITE_URL}/resena/{p['slug']}</guid>
        </item>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
    <title>{SITE_NAME}</title>
    <link>{SITE_URL}</link>
    <description>{SITE_DESCRIPTION}</description>
    <language>es-mx</language>
    <atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
    {"".join(items)}
</channel>
</rss>"""
    return Response(content=xml, media_type="application/xml")


# ── Routes: API (sync + admin) ────────────────────────────────────────────────
ADMIN_SECRET = (os.getenv("ADMIN_SECRET") or "").strip()


def _check_admin(request: Request):
    token = request.headers.get("x-admin-secret", "")
    if not ADMIN_SECRET or token != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="No autorizado")


@app.post("/api/sync-instagram")
def api_sync_instagram(request: Request):
    """Trigger manual Instagram sync. Protected by admin secret."""
    _check_admin(request)
    result = sync_instagram_to_db()
    return {"ok": True, **result}


@app.get("/api/posts")
def api_list_posts(limit: int = 20, offset: int = 0):
    """Public JSON API for posts (for future frontend or integrations)."""
    posts = get_recent_posts(limit=limit, offset=offset)
    return {"ok": True, "posts": posts}


@app.post("/api/posts/{post_id}/amazon-url")
def api_update_amazon_url(request: Request, post_id: int, amazon_url: str = ""):
    """Manually set a specific Amazon affiliate link for a post."""
    _check_admin(request)
    url = (amazon_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="amazon_url requerido")
    conn = get_conn()
    cur = conn.cursor()
    try:
        # Add affiliate tag if missing
        if AMAZON_AFFILIATE_TAG not in url:
            sep = "&" if "?" in url else "?"
            url += f"{sep}tag={AMAZON_AFFILIATE_TAG}"
        cur.execute("UPDATE posts SET amazon_url = %s, updated_at = now() WHERE id = %s RETURNING id", (url, post_id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Post no encontrado")
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@app.post("/api/posts/{post_id}/toggle")
def api_toggle_post(request: Request, post_id: int):
    """Publish/unpublish a post."""
    _check_admin(request)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE posts SET published = NOT published, updated_at = now() WHERE id = %s RETURNING published",
            (post_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Post no encontrado")
        return {"ok": True, "published": row[0]}
    finally:
        cur.close()
        conn.close()


# ── Routes: Admin Panel ──────────────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, secret: str = Query(default="")):
    """Admin panel — access via /admin?secret=YOUR_SECRET"""
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Acceso denegado. Usa /admin?secret=TU_CLAVE")
    return render_template(
        "admin.html",
        admin_secret=ADMIN_SECRET,
        meta_title=f"Admin — {SITE_NAME}",
    )


@app.post("/api/admin/posts")
def api_admin_create_post(request: Request, data: dict = Body(...)):
    """Create a new review manually from admin panel."""
    _check_admin(request)

    book_title = (data.get("book_title") or "").strip()
    book_author = (data.get("book_author") or "").strip()
    genre = (data.get("genre") or "").strip()
    rating = data.get("rating")
    caption = (data.get("caption") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    amazon_url_input = (data.get("amazon_url") or "").strip()
    ig_permalink = (data.get("ig_permalink") or "").strip()

    if not book_title or not caption:
        raise HTTPException(status_code=400, detail="Título y reseña son requeridos")

    # Build title
    title = f"Reseña: {book_title}"
    if book_author:
        title += f" — {book_author}"

    slug = slugify(title)

    # Ensure unique slug
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM posts WHERE slug LIKE %s", (f"{slug}%",))
        count = cur.fetchone()[0]
        if count > 0:
            slug = f"{slug}-{count + 1}"

        body_html = caption_to_html(caption)
        excerpt = make_excerpt(caption)

        # Amazon link
        if amazon_url_input:
            amazon_url = amazon_url_input
            if AMAZON_AFFILIATE_TAG and AMAZON_AFFILIATE_TAG not in amazon_url:
                sep = "&" if "?" in amazon_url else "?"
                amazon_url += f"{sep}tag={AMAZON_AFFILIATE_TAG}"
        else:
            search_q = book_title
            if book_author:
                search_q += f" {book_author}"
            amazon_url = make_amazon_link(search_q)

        cur.execute("""
            INSERT INTO posts
                (slug, title, body, excerpt, image_url, book_title, book_author,
                 amazon_url, genre, rating, ig_permalink, published)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING id
        """, (
            slug, title, body_html, excerpt, image_url or None,
            book_title, book_author or None, amazon_url,
            genre or None, rating, ig_permalink or None,
        ))
        new_id = cur.fetchone()[0]
        return {"ok": True, "id": new_id, "slug": slug}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@app.get("/api/admin/posts-list")
def api_admin_list_posts(request: Request):
    """List all posts for admin panel."""
    _check_admin(request)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, title, image_url, genre, rating, published, created_at
            FROM posts
            ORDER BY created_at DESC
            LIMIT 100
        """)
        rows = cur.fetchall()
        posts = [
            {
                "id": r[0], "title": r[1], "image_url": r[2], "genre": r[3],
                "rating": r[4], "published": r[5],
                "created_at": r[6].strftime("%d %b %Y") if r[6] else "",
            }
            for r in rows
        ]
        return {"ok": True, "posts": posts}
    finally:
        cur.close()
        conn.close()


@app.delete("/api/admin/posts/{post_id}")
def api_admin_delete_post(request: Request, post_id: int):
    """Delete a post."""
    _check_admin(request)
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM posts WHERE id = %s RETURNING id", (post_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Post no encontrado")
        return {"ok": True}
    finally:
        cur.close()
        conn.close()


@app.post("/api/admin/import-instagram")
def api_admin_import_ig(request: Request, data: dict = Body(...)):
    """Import posts from Instagram JSON export."""
    _check_admin(request)

    # Instagram export format can vary — handle both formats
    posts_data = []

    # Format 1: Direct list of posts
    if isinstance(data, list):
        posts_data = data
    # Format 2: Object with key like "ig_posts" or similar
    elif isinstance(data, dict):
        for key in ["ig_posts", "posts", "media", "photos_and_videos", "content"]:
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    posts_data = val
                    break
        # Try nested: content -> posts_1 (newer IG export format)
        if not posts_data and "content" in data and isinstance(data["content"], list):
            posts_data = data["content"]
        if not posts_data:
            # Maybe it's the top level with media items
            if "media" in data and isinstance(data["media"], list):
                posts_data = data["media"]
            elif isinstance(data, dict) and len(data) == 1:
                val = list(data.values())[0]
                if isinstance(val, list):
                    posts_data = val

    if not posts_data:
        raise HTTPException(status_code=400, detail="No se encontraron posts en el archivo. Formato no reconocido.")

    conn = get_conn()
    cur = conn.cursor()
    imported = 0
    skipped = 0

    try:
        for item in posts_data:
            # Extract caption — handle multiple IG export formats
            caption = ""
            if isinstance(item, dict):
                caption = (
                    item.get("caption", "") or
                    item.get("title", "") or
                    item.get("text", "") or ""
                )
                # Nested media format
                if not caption and "media" in item and isinstance(item["media"], list):
                    for m in item["media"]:
                        if isinstance(m, dict) and m.get("title"):
                            caption = m["title"]
                            break
                # Newer format: string_map_data
                if not caption and "string_map_data" in item:
                    smd = item["string_map_data"]
                    if isinstance(smd, dict):
                        for k, v in smd.items():
                            if isinstance(v, dict) and v.get("value"):
                                caption = v["value"]
                                break

            if not caption or len(caption.strip()) < 30:
                skipped += 1
                continue

            caption = caption.strip()

            # Extract image URL
            image_url = ""
            if isinstance(item, dict):
                image_url = item.get("media_url", "") or item.get("image_url", "") or item.get("uri", "") or ""
                if not image_url and "media" in item and isinstance(item["media"], list):
                    for m in item["media"]:
                        if isinstance(m, dict):
                            image_url = m.get("uri", "") or m.get("media_url", "")
                            if image_url:
                                break

            # Extract timestamp
            ts = item.get("timestamp", "") or item.get("creation_timestamp", "") or item.get("taken_at", "")
            ig_ts = None
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        ig_ts = datetime.fromtimestamp(ts, tz=timezone.utc)
                    else:
                        ig_ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except Exception:
                    ig_ts = None

            # Extract book info
            book = extract_book_info(caption)

            if book["title"]:
                title = f"Reseña: {book['title']}"
                if book["author"]:
                    title += f" — {book['author']}"
            else:
                first_line = caption.split("\n")[0].strip()
                first_line = re.sub(r"[📖📚📕📗📘📙⭐✍️]+", "", first_line).strip()
                title = first_line[:120] if first_line else f"Reseña importada"

            slug = slugify(title)
            cur.execute("SELECT COUNT(*) FROM posts WHERE slug LIKE %s", (f"{slug}%",))
            count = cur.fetchone()[0]
            if count > 0:
                slug = f"{slug}-{count + 1}"

            body_html = caption_to_html(caption)
            excerpt = make_excerpt(caption)

            amazon_search = book["title"] or title
            if book["author"]:
                amazon_search += f" {book['author']}"
            amazon_url = make_amazon_link(amazon_search)

            permalink = item.get("permalink", "") or ""

            cur.execute("""
                INSERT INTO posts
                    (slug, title, body, excerpt, image_url, book_title, book_author,
                     amazon_url, genre, rating, ig_permalink, ig_timestamp, published)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            """, (
                slug, title, body_html, excerpt, image_url or None,
                book["title"] or None, book["author"] or None,
                amazon_url, book["genre"] or None, book["rating"],
                permalink or None, ig_ts,
            ))
            imported += 1

        return {"ok": True, "imported": imported, "skipped": skipped, "total": len(posts_data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"ok": True, "service": "bookaholic-mexicana"}
