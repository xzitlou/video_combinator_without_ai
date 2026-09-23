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

Para ver la app: `runserver` + `rqworker default video`, crear una cuenta en `/signup/` (con correo) y un proyecto en `/` (botón "Nuevo proyecto", abre un modal). Las descargas y el ZIP requieren sesión y ser dueño del proyecto.

Los tests usan Postgres (crean `test_video_combinator`). `FullPipelineTests` genera clips reales con FFmpeg y se salta si no está instalado. Los jobs se ejecutan en línea parcheando `services.enqueue` y usando `captureOnCommitCallbacks(execute=True)`.

Configuración por variables de entorno en `config/settings.py` (`POSTGRES_*`, `REDIS_URL`, `MEDIA_ROOT`, `MAX_VARIANTS_PER_RUN`, `OUTPUT_TTL_SECONDS`, `ABANDONED_UPLOAD_TTL_SECONDS`).

## Producto

SaaS web para producir mucho contenido para TikTok **sin IA**. El usuario crea un **proyecto**, sube clips en tres grupos (**Ganchos**, **Contenido**, **Cierres**), activa o desactiva los que quiere usar y el sistema genera con FFmpeg todas las combinaciones como MP4 listos para publicar. Ejemplo: 2 ganchos × 3 contenidos × 2 cierres = 12 videos.

**Evitar contenido casi duplicado es parte del producto.** Las plataformas bajan el alcance de videos repetidos, así que el valor es probar ideas distintas, no multiplicar copias. Todo vive en `combinator/variation.py` (funciones puras, tests en `tests/test_variation.py`):
- Modo `distinct` (por defecto): cada par gancho+contenido una sola vez y los cierres rotando. Modo `all`: todas las combinaciones. Se elige antes de generar y se guarda en `Project.mode`.
- `publication_order`: fija `Variant.position` (#001…) para que los videos cercanos (seguidos y a dos posiciones) compartan lo mínimo; lo peor es el mismo gancho+contenido con solo otro cierre. La posición encabeza el nombre de archivo, así que el ZIP ya sale en orden de publicación.
- `publishing_plan`: reparte los videos en días (`Variant.publish_day`). Regla fija: nunca el mismo contenido dos veces el mismo día. Reglas blandas, que se relajan en orden: sin ganchos de ayer, cada gancho una vez al día, sin parejas gancho+contenido de ayer. Hasta `PUBLISH_MAX_PER_DAY` (6, decisión de producto, no límite de las plataformas). Al generar, las posiciones se numeran día a día; la pantalla agrupa por día y el ZIP trae una carpeta por día (`?day=N` baja un solo día).
- `similarity`: por video, "identical" (rojo) si otro tiene el mismo material en todo, "high" (amarillo) si otro solo cambia el cierre, "low" en otro caso. La UI solo etiqueta los problemas, sin porcentajes. Se calcula al vuelo en las vistas; no se guarda. "Mismo material" se decide por `Clip.content_key` (SHA-256 del archivo subido), y `add_clip` rechaza subir el mismo archivo dos veces en un proyecto.

**Los cierres son opcionales.** Sin cierres activos, cada video es gancho + contenido (2 × 3 = 6). Si hay cierres, también se combinan.

Vocabulario: en código se mantienen `Project`/`hook`/`body`/`closer`; en la interfaz se dice **proyecto**, **gancho**, **contenido** y **cierre**, nunca "lote", "campaña", "hook", "body" o "closer". Los códigos de clip son `GA01`, `CO02` y `CI01` (dos letras porque Contenido y Cierre empiezan por C) y aparecen en la UI y en los nombres de archivo.

### Alcance del MVP
- Flujo: crear proyecto → subir clips → marcar tipo → elegir combinaciones → generar → ver y descargar (uno a uno o en ZIP).
- Una sola pantalla con tres grupos (Ganchos, Contenido, Cierres), cada uno con una descripción breve de su función. Cada clip se puede activar o desactivar.
- Contador en vivo (`2 × 3 × 1 = 6 videos`) y botón "Generar N variantes".
- Lista de variantes con su estado (pendiente / procesando / listo / error).
- **Límite de 100 variantes por ejecución.**

### Fuera del MVP (no implementar salvo que se pida)
IA generativa, publicación en TikTok, subtítulos, música, transiciones, editor/timeline, efectos, thumbnails, analytics de redes, eliminación de silencios, voces o avatares.

### Hoja de ruta
- **1.1:** varios contenidos por video con orden variable (H1+B1+B2+C1, H1+B2+B1+C1…). El modelo `hook/body/closer` evolucionará a grupos de segmentos genéricos y ordenados, pero el MVP no debe diseñarse así desde el principio.
- **Después:** registrar métricas (views) por variante y agregarlas por hook/body/closer para encontrar qué piezas funcionan mejor ("Creative Testing Engine"). Por eso cada variante guarda qué clips usó.

## Arquitectura

- **Django** con plantillas del servidor y **Bootstrap**. No se usa React ni otro framework SPA.
- **PostgreSQL** como base de datos.
- **Django-RQ + Redis** para todo el trabajo con FFmpeg. Nunca renderizar dentro del request. Cada variante es un job propio (`queue.enqueue(render_variant, variant.id, job_timeout=...)`). La normalización de clips también se encola.
- **FFmpeg/ffprobe** instalados en los workers.
- **Disco local** para todo (clips originales, normalizados y salidas) en `MEDIA_ROOT`. Decisión del usuario: por ahora nada de S3 ni almacenamiento de objetos. El servidor web y los workers deben compartir ese directorio; FFmpeg lee directamente de `FieldFile.path`.

Código en la app `combinator`: `services.py` tiene las operaciones de dominio (vistas y jobs llaman aquí), `tasks.py` los jobs de RQ, `ffmpeg.py` los comandos de FFmpeg, `cron.py` el job periódico y `views.py` las pantallas y los endpoints JSON que usa `static/combinator/app.js`.

### Pantallas
- Sin frameworks CSS ni JS: `static/combinator/app.css` y `app.js` (vanilla). Plantillas en `templates/`. Textos de la interfaz en español.
- Un proyecto tiene una sola pantalla (`project_detail`): tres columnas definidas en `views.COLUMNS` (etiqueta, descripción, texto del botón), panel con la fórmula `ganchos × contenidos × cierres` y el botón Generar; tras generar, la misma pantalla se bloquea y lista los videos con su tira de tres segmentos (proporcional a la duración de cada clip).
- Cada rol tiene un color fijo (`--hook`, `--body`, `--closer`) que se usa en columnas, fórmula, códigos H01/B02/C01 y tiras. El color es información de rol; no usar esos colores para otra cosa. El botón principal es negro.
- **Nada se sube al elegir archivos.** Los videos elegidos se quedan en el navegador (miniatura y duración sacadas con `<video>` + canvas, vista previa en un `<dialog>`). Al pulsar "Generar" se suben solo los marcados, uno a uno y en el orden de pantalla (así los códigos GA01… siguen ese orden), con barra de progreso; después se llama a `project_generate` (JSON) y se recarga la página. Luego hace polling a `project_status` mientras haya clips normalizando o videos en cola. Django está en `es`, así que en atributos `style` usa `|unlocalize` para los floats.
- Auth en la app `accounts` (login y registro por correo, logout). Toda vista de proyecto filtra por `owner=request.user`.

### Retención de archivos (requisito del producto: no guardar el material del usuario)
- El original subido se borra en cuanto existe la copia normalizada.
- Cuando todas las variantes de un proyecto terminan (listas o con error), `finalize_project_if_done` borra todos los clips normalizados del proyecto. Las filas `Clip` se conservan, sin archivos, para mantener la trazabilidad. Un proyecto se genera **una sola vez**.
- Las salidas se pueden descargar durante `OUTPUT_TTL_SECONDS` (1 h). La descarga pasa siempre por `download_variant`, que rechaza la petición al vencer `expires_at`. `purge_expired` borra los archivos vencidos.
- Los borradores que nunca se generan pierden sus archivos a las 24 h (`ABANDONED_UPLOAD_TTL_SECONDS`).
- `media/` no se sirve como estático. No añadas rutas que expongan archivos sin pasar por estas reglas.

Pipeline: `SUBIDA → NORMALIZAR CLIPS → GENERAR COMBINACIONES → COLA → WORKERS FFMPEG → SALIDAS`

### Modelo de datos mínimo
- `Project`: name, created_at, status
- `Clip`: project, type (`hook|body|closer`), file, duration, order, enabled
- `Variant`: project, hook, body, closer (nullable), output_file, status, error, created_at
- Todos los modelos tienen un `uuid` público: **las URLs y el JSON usan solo UUIDs**, nunca el `pk` entero (que sigue siendo la clave interna y de las FKs).
- `accounts.User`: modelo de usuario propio con `email` como `USERNAME_FIELD` (sin username). El login no distingue mayúsculas en el correo.

Generar no espera a la normalización: los `Variant` se crean enseguida (ya ordenados, ver `variation.py`) y `services.enqueue_ready_variants` encola cada uno cuando sus clips están listos (lo llaman `generate_variants` y `clip_normalized`, ambos con el lock de la fila del proyecto para no perder ninguno). Si un clip falla, sus variantes se marcan como fallidas. `render_variant` reclama la variante con un UPDATE condicional (`claim_variant`), así que encolar dos veces es inofensivo.

Los registros `Variant` se crean al pulsar "Generar" con `services.combinations()` según el modo, usando solo los clips habilitados. Usa siempre `Variant.clips` para recorrer los segmentos, porque omite el cierre vacío. Validar el límite de 100 antes de crear nada.

### Pipeline de video
1. **Normalizar cada clip una sola vez** a 1080×1920, 30 fps, H.264, `yuv420p`, AAC a 48 kHz. Se escala manteniendo la proporción y se rellena con barras (`scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2`). Los clips sin pista de audio necesitan un audio silencioso para que la concatenación no falle. La duración se obtiene con ffprobe.
2. **Concatenar** los clips normalizados con el demuxer concat (`-f concat -safe 0 -i list.txt -c copy`). Así no se recodifica: el costo de cada variante es casi nulo y lo caro es la normalización. No concatenar nunca los originales sin normalizar.
3. Nombres de salida trazables, p. ej. `007_<proyecto-slug>_ga02_co04_ci01.mp4` (posición de publicación primero) o sin `_ci..` si no hay cierre.
