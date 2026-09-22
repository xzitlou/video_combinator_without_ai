# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos

Desarrollo local con Postgres y Redis de Homebrew (no hay Docker). FFmpeg/ffprobe deben estar en el PATH (o `FFMPEG_BIN`/`FFPROBE_BIN`).

```bash
source venv/bin/activate
pip install -r requirements.txt
createdb video_combinator && python manage.py migrate
python manage.py runserver
python manage.py rqworker default video        # jobs de FFmpeg (cola "video") y limpieza (cola "default")
python manage.py rqcron combinator.cron        # corre purge_expired cada 5 min
python manage.py purge_expired                 # limpieza manual

python manage.py test combinator                                       # todos los tests
python manage.py test combinator.tests.test_pipeline.FullPipelineTests # una clase
```

Para ver la app: `runserver` + `rqworker default video`, crear un usuario en `/signup/` y una campaña en `/`. Las descargas y el ZIP requieren sesión y ser dueño del proyecto.

Los tests usan Postgres (crean `test_video_combinator`). `FullPipelineTests` genera clips reales con FFmpeg y se salta si no está instalado. Los jobs se ejecutan en línea parcheando `services.enqueue` y usando `captureOnCommitCallbacks(execute=True)`.

Configuración por variables de entorno en `config/settings.py` (`POSTGRES_*`, `REDIS_URL`, `USE_S3` + `S3_*`, `MAX_VARIANTS_PER_RUN`, `OUTPUT_TTL_SECONDS`, `ABANDONED_UPLOAD_TTL_SECONDS`). Sin `USE_S3`, los archivos van a `media/`.

## Producto

SaaS web para crear variantes de video para TikTok **sin IA**. El usuario sube clips, marca cada uno como **hook**, **body** o **closer**, activa o desactiva los que quiere usar y el sistema genera con FFmpeg todas las combinaciones `hook × body × closer` como MP4 listos para publicar. Ejemplo: 2 hooks × 3 bodies × 2 closers = 12 videos.

### Alcance del MVP
- Flujo: crear proyecto → subir clips → marcar tipo → elegir combinaciones → generar → ver y descargar (uno a uno o en ZIP).
- Una sola pantalla con tres grupos (Hooks, Bodies, Closers). Cada clip se puede activar o desactivar, lo que permite tener 20 hooks guardados y usar solo 3 en una campaña.
- Contador en vivo (`2 × 3 × 1 = 6 videos`) y botón "Generar N variantes".
- Lista de variantes con su estado (pendiente / procesando / listo / error).
- **Límite de 100 variantes por ejecución.**

### Fuera del MVP (no implementar salvo que se pida)
IA generativa, publicación en TikTok, subtítulos, música, transiciones, editor/timeline, efectos, thumbnails, analytics de redes, eliminación de silencios, voces o avatares.

### Hoja de ruta
- **1.1:** varios bodies con orden variable (H1+B1+B2+C1, H1+B2+B1+C1…). El modelo `hook/body/closer` evolucionará a grupos de segmentos genéricos y ordenados, pero el MVP no debe diseñarse así desde el principio.
- **Después:** registrar métricas (views) por variante y agregarlas por hook/body/closer para encontrar qué piezas funcionan mejor ("Creative Testing Engine"). Por eso cada variante guarda qué clips usó.

## Arquitectura

- **Django** con plantillas del servidor y **Bootstrap**. No se usa React ni otro framework SPA.
- **PostgreSQL** como base de datos.
- **Django-RQ + Redis** para todo el trabajo con FFmpeg. Nunca renderizar dentro del request. Cada variante es un job propio (`queue.enqueue(render_variant, variant.id, job_timeout=...)`). La normalización de clips también se encola.
- **FFmpeg/ffprobe** instalados en los workers.
- **Almacenamiento de objetos** (S3/R2 vía django-storages) para los clips y las salidas. Los workers siempre copian el archivo a un directorio temporal antes de pasarlo a FFmpeg, así el mismo código funciona con disco local o S3. Pendiente: que el navegador suba directo al bucket con URLs prefirmadas, porque los clips pesan cientos de MB.

Código en la app `combinator`: `services.py` tiene las operaciones de dominio (vistas y jobs llaman aquí), `tasks.py` los jobs de RQ, `ffmpeg.py` los comandos de FFmpeg, `cron.py` el job periódico y `views.py` las pantallas y los endpoints JSON que usa `static/combinator/app.js`.

### Pantallas
- Sin frameworks CSS ni JS: `static/combinator/app.css` y `app.js` (vanilla). Plantillas en `templates/`. Textos de la interfaz en español; en la UI el `Project` se llama "campaña".
- Una campaña tiene una sola pantalla (`project_detail`): tres columnas Hooks/Bodies/Closers, panel con la fórmula `hooks × bodies × closers` y el botón Generar; tras generar, la misma pantalla se bloquea y lista los videos con su tira de tres segmentos (proporcional a la duración de cada clip).
- Cada rol tiene un color fijo (`--hook`, `--body`, `--closer`) que se usa en columnas, fórmula, códigos H01/B02/C01 y tiras. El color es información de rol; no usar esos colores para otra cosa. El botón principal es negro.
- Subidas por XHR con barra de progreso; la página hace polling a `project_status` mientras haya clips normalizando o videos en cola. Django está en `es`, así que en atributos `style` usa `|unlocalize` para los floats.
- Auth con `django.contrib.auth` (login, signup, logout). Toda vista de campaña filtra por `owner=request.user`.

### Retención de archivos (requisito del producto: no guardar el material del usuario)
- El original subido se borra en cuanto existe la copia normalizada.
- Cuando todas las variantes de un proyecto terminan (listas o con error), `finalize_project_if_done` borra todos los clips normalizados del proyecto. Las filas `Clip` se conservan, sin archivos, para mantener la trazabilidad. Un proyecto se genera **una sola vez**.
- Las salidas se pueden descargar durante `OUTPUT_TTL_SECONDS` (1 h). La descarga pasa siempre por `download_variant`, que rechaza la petición al vencer `expires_at` y, en S3, redirige a una URL firmada de 5 min. `purge_expired` borra los archivos vencidos.
- Los borradores que nunca se generan pierden sus archivos a las 24 h (`ABANDONED_UPLOAD_TTL_SECONDS`).
- `media/` no se sirve como estático. No añadas rutas que expongan archivos sin pasar por estas reglas.

Pipeline: `SUBIDA → NORMALIZAR CLIPS → GENERAR COMBINACIONES → COLA → WORKERS FFMPEG → SALIDAS`

### Modelo de datos mínimo
- `Project`: name, created_at, status
- `Clip`: project, type (`hook|body|closer`), file, duration, order, enabled
- `Variant`: project, hook, body, closer, output_file, status, error, created_at

Los registros `Variant` se crean al pulsar "Generar", a partir de `itertools.product(hooks, bodies, closers)` usando solo los clips habilitados. Validar el límite de 100 antes de crear nada.

### Pipeline de video
1. **Normalizar cada clip una sola vez** a 1080×1920, 30 fps, H.264, `yuv420p`, AAC a 48 kHz. Se escala manteniendo la proporción y se rellena con barras (`scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2`). Los clips sin pista de audio necesitan un audio silencioso para que la concatenación no falle. La duración se obtiene con ffprobe.
2. **Concatenar** los clips normalizados con el demuxer concat (`-f concat -safe 0 -i list.txt -c copy`). Así no se recodifica: el costo de cada variante es casi nulo y lo caro es la normalización. No concatenar nunca los originales sin normalizar.
3. Nombres de salida trazables y guardados en la base de datos, p. ej. `<proyecto-slug>_h02_b04_c01.mp4`.
