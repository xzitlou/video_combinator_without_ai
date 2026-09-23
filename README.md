# Variantes

Crea muchos videos para TikTok, Reels o Shorts a partir de pocas grabaciones, **sin IA**.

Subes tus **ganchos** (los primeros segundos), tus **contenidos** (el cuerpo del video) y, si quieres, **cierres** (la llamada a la acción). La app une cada combinación con FFmpeg y te devuelve MP4 verticales listos para publicar.

```
7 ganchos × 3 contenidos = 21 videos
```

**Graba menos. Prueba más. Descubre qué funciona.**

## Qué hace

- **Combina sin duplicar.** Por defecto cada gancho + contenido sale una sola vez y los cierres se reparten, para no generar videos que solo cambian en los últimos segundos. Si subes el mismo clip dos veces, lo detecta.
- **Marca los videos demasiado parecidos**, porque las plataformas bajan el alcance del contenido repetido.
- **Te da un plan de publicación por días**: nunca el mismo contenido dos veces el mismo día y ganchos que no se repiten del día anterior. El ZIP viene con una carpeta por día.
- **No guarda tus videos.** Los archivos no se suben hasta que pulsas *Generar* (antes, solo ves una miniatura en tu navegador). Los clips se borran del servidor al terminar y los videos generados, una hora después.

## Requisitos

- Python 3.12
- PostgreSQL
- Redis
- FFmpeg (`ffmpeg` y `ffprobe` en el PATH)

En macOS:

```bash
brew install python@3.12 postgresql@14 redis ffmpeg
brew services start postgresql@14
brew services start redis
```

## Instalación

```bash
git clone https://github.com/xzitlou/video_combinator_without_ai.git
cd video_combinator_without_ai
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
createdb video_combinator
python manage.py migrate
```

## Uso

Arranca estos tres procesos, cada uno en una terminal:

```bash
python manage.py runserver                 # la web, en http://localhost:8000
python manage.py rqworker default video    # procesa los videos
python manage.py rqcron combinator.cron    # borra los archivos vencidos
```

Abre http://localhost:8000, crea una cuenta con tu correo y crea un proyecto.

## Configuración

Todo se configura con variables de entorno (ver `config/settings.py`):

| Variable | Por defecto | Para qué sirve |
|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` | `video_combinator` | Base de datos |
| `REDIS_URL` | `redis://localhost:6379/0` | Cola de trabajos |
| `MEDIA_ROOT` | `./media` | Dónde se guardan los archivos temporales |
| `MAX_VARIANTS_PER_RUN` | `100` | Máximo de videos por proyecto |
| `PUBLISH_MAX_PER_DAY` | `6` | Máximo de videos por día en el plan |
| `OUTPUT_TTL_SECONDS` | `3600` | Cuánto duran los videos generados |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | modo desarrollo | Para producción |

## Tests

```bash
python manage.py test
```

Algunos tests generan videos reales con FFmpeg.

## Stack

Django, PostgreSQL, Django-RQ + Redis, FFmpeg. CSS y JavaScript sin frameworks.
