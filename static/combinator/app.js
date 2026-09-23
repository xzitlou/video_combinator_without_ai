(function () {
  const app = document.getElementById("app");
  if (!app) return;

  const csrf = app.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
  const { statusUrl, uploadUrl, generateUrl } = app.dataset;
  const maxVariants = Number(app.dataset.maxVariants);
  const maxClipMb = Number(app.dataset.maxClipMb);
  const accepted = app.dataset.accepted.split(",");
  const isDraft = app.dataset.projectStatus === "draft";
  const TYPES = ["hook", "body", "closer"];
  const PREFIX = { hook: "GA", body: "CO", closer: "CI" };

  // Files chosen in the browser but not uploaded yet, keyed by row id.
  const local = new Map();
  let localSeq = 0;
  let uploading = false;

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const pad = (n) => String(n).padStart(2, "0");
  const seconds = (s) => `${Number(s).toFixed(1)} s`;
  const mb = (bytes) => `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  const post = (url, data) =>
    fetch(url, {
      method: "POST",
      body: data || new FormData(),
      headers: { "X-CSRFToken": csrf, Accept: "application/json" },
    }).then((r) => r.json().then((json) => (r.ok ? json : Promise.reject(json.error || "Algo falló."))));

  function notify(message) {
    let box = $(".messages");
    if (!box) {
      box = document.createElement("div");
      box.className = "messages";
      app.parentNode.insertBefore(box, app);
    }
    const p = document.createElement("p");
    p.className = "message message-error";
    p.textContent = message;
    box.appendChild(p);
    setTimeout(() => p.remove(), 8000);
  }

  // --- Progress box -----------------------------------------------------------
  const progress = $("#progress");
  function setProgress(label, done, total, detail) {
    if (!progress) return;
    progress.hidden = false;
    $("[data-progress-label]", progress).textContent = label;
    $("[data-progress-count]", progress).textContent = total ? `${done} de ${total}` : "";
    $("[data-progress-bar]", progress).style.width = `${total ? Math.min(100, (done / total) * 100) : 0}%`;
    const extra = $("[data-progress-detail]", progress);
    if (extra) extra.textContent = detail || "";
  }

  // --- Counting ---------------------------------------------------------------
  const isUsable = (row) => {
    const toggle = $(".clip-toggle", row);
    return toggle ? toggle.checked && !toggle.disabled && row.dataset.status !== "failed" : false;
  };

  // Local files get their real code (GA03…) only when uploaded; show what it will be.
  function renumberLocal() {
    TYPES.forEach((type) => {
      const rows = $$(`[data-list="${type}"] .clip`);
      let next = Math.max(0, ...rows.filter((r) => r.dataset.order).map((r) => Number(r.dataset.order))) + 1;
      rows.filter((r) => r.classList.contains("clip-local")).forEach((row) => {
        $(".clip-code", row).textContent = isUsable(row) ? PREFIX[type] + pad(next++) : "—";
      });
    });
  }

  function refreshFormula() {
    if (!isDraft) return;
    renumberLocal();
    const counts = {};
    TYPES.forEach((type) => {
      const rows = $$(`[data-list="${type}"] .clip`);
      counts[type] = rows.filter(isUsable).length;
      $(`[data-n="${type}"]`).textContent = counts[type];
      $(`[data-count-for="${type}"]`).textContent = rows.length;
    });
    // Closers are optional: with none selected each video is hook + body. In "distinct" mode
    // each hook+body pair appears once and closers are spread across them (see variation.py).
    const perMode = {
      distinct: counts.hook * counts.body,
      all: counts.hook * counts.body * Math.max(counts.closer, 1),
    };
    $$("[data-mode-count]").forEach((el) => { el.textContent = perMode[el.dataset.modeCount]; });
    $("#modes").hidden = counts.closer < 2; // with 0 or 1 closers both modes are the same
    const mode = selectedMode();
    const total = perMode[mode];
    $$("[data-closer-term]").forEach((el) => {
      el.classList.toggle("term-off", counts.closer === 0);
      el.classList.toggle("term-rotate", mode === "distinct" && counts.closer > 1);
    });
    $("#count").textContent = total;
    $("#count-label").textContent = total === 1 ? "video" : "videos";

    const failed = $$(".clip[data-status=failed] .clip-toggle:checked:not(:disabled)").length;
    const note = $("#panel-note");
    note.classList.remove("over");
    if (total === 0) {
      note.textContent = "Añade al menos un gancho y un contenido.";
    } else if (total > maxVariants) {
      note.textContent = `Son demasiados: el máximo es ${maxVariants}. Desmarca algunos clips.`;
      note.classList.add("over");
    } else if (failed) {
      note.textContent = "Quita los clips que no se pudieron procesar.";
      note.classList.add("over");
    } else if (!counts.closer) {
      note.textContent = "Sin cierres, cada video une un gancho y un contenido.";
    } else if (mode === "distinct" && counts.closer > 1) {
      note.textContent = `Cada gancho + contenido sale una vez; los ${counts.closer} cierres se reparten entre ellos.`;
    } else {
      note.textContent = "Cada video une un gancho, un contenido y un cierre, en ese orden.";
    }
    const button = $("#generate");
    if (!uploading) {
      button.disabled = total === 0 || total > maxVariants || failed > 0;
      button.textContent = total > 0 ? `Generar ${total} video${total === 1 ? "" : "s"}` : "Generar videos";
    }
  }

  const selectedMode = () => $("input[name=mode]:checked")?.value || "distinct";

  // --- Local files: pick, preview, remove ------------------------------------
  // Frames are extracted one file at a time (decoding many videos at once is heavy), and
  // only while the tab is visible: Chrome doesn't decode media in background tabs.
  let thumbChain = Promise.resolve();
  const whenVisible = () =>
    document.hidden
      ? new Promise((r) => document.addEventListener("visibilitychange", function on() {
          if (!document.hidden) { document.removeEventListener("visibilitychange", on); r(); }
        }))
      : Promise.resolve();
  const queueThumb = (url) => (thumbChain = thumbChain.then(whenVisible).then(() => readThumb(url)));

  function readThumb(url) {
    // Grab a frame and the duration in the browser; nothing leaves the machine.
    return new Promise((resolve) => {
      const video = document.createElement("video");
      const done = (result) => { clearTimeout(timer); video.removeAttribute("src"); video.load(); resolve(result); };
      const timer = setTimeout(() => done({}), 10000);
      video.muted = true;
      video.preload = "metadata";
      video.playsInline = true;
      video.onloadedmetadata = () => { video.currentTime = Math.min(0.5, (video.duration || 1) / 3); };
      video.onseeked = () => {
        const canvas = document.createElement("canvas");
        canvas.width = 72;
        canvas.height = 128;
        const ctx = canvas.getContext("2d");
        const scale = Math.max(72 / video.videoWidth, 128 / video.videoHeight);
        const w = video.videoWidth * scale;
        const h = video.videoHeight * scale;
        ctx.drawImage(video, (72 - w) / 2, (128 - h) / 2, w, h);
        done({ thumb: canvas.toDataURL("image/jpeg", 0.7), duration: video.duration });
      };
      video.onerror = () => done({});
      video.src = url;
    });
  }

  function addLocal(type, file) {
    const row = $("#clip-template").content.firstElementChild.cloneNode(true);
    const id = `local-${++localSeq}`;
    row.dataset.local = id;
    $(".clip-name", row).textContent = file.name;
    $(".clip-name", row).title = file.name;
    $(".clip-toggle", row).setAttribute("aria-label", `Usar ${file.name}`);
    $(".clip-remove", row).setAttribute("aria-label", `Quitar ${file.name}`);
    $(`[data-list="${type}"]`).appendChild(row);

    const ext = "." + file.name.split(".").pop().toLowerCase();
    let problem = null;
    if (!accepted.includes(ext)) problem = "Formato no admitido";
    else if (file.size > maxClipMb * 1024 * 1024) problem = `Supera los ${maxClipMb} MB`;
    if (problem) {
      markInvalid(row, problem);
      refreshFormula();
      return;
    }

    const url = URL.createObjectURL(file);
    const entry = { file, url, type, name: file.name };
    local.set(id, entry);
    $("[data-info]", row).textContent = mb(file.size);
    findTwin(entry).then((twin) => {
      if (!twin || !row.isConnected) return;
      // The same footage twice would produce byte-identical videos.
      URL.revokeObjectURL(url);
      local.delete(id);
      markInvalid(row, `Repetido: es el mismo archivo que ${twin.name}`);
      refreshFormula();
    });
    queueThumb(url).then(({ thumb, duration }) => {
      if (!row.isConnected || !local.has(id)) return;
      if (thumb) $(".clip-thumb img", row).src = thumb;
      if (!duration || !isFinite(duration)) return;
      entry.duration = duration;
      const info = $("[data-info]", row);
      info.textContent = `${seconds(duration)} · ${mb(file.size)}`;
      // Re-exports of the same take have different bytes but the same length: flag, don't block.
      const lookalike = sameLength(entry);
      if (lookalike) {
        info.textContent = `${seconds(duration)} · dura igual que ${lookalike}. ¿Es el mismo video?`;
        row.classList.add("clip-suspect");
      }
    });
    refreshFormula();
  }

  // Byte-compare against other chosen files of the same size (any column), 4 MB at a time.
  async function findTwin(entry) {
    for (const other of local.values()) {
      if (other === entry || other.file.size !== entry.file.size) continue;
      if (await sameBytes(entry.file, other.file)) return other;
    }
    return null;
  }

  async function sameBytes(a, b) {
    const step = 4 * 1024 * 1024;
    for (let start = 0; start < a.size; start += step) {
      const [x, y] = await Promise.all([
        a.slice(start, start + step).arrayBuffer(),
        b.slice(start, start + step).arrayBuffer(),
      ]);
      const u = new Uint8Array(x);
      const v = new Uint8Array(y);
      for (let k = 0; k < u.length; k++) if (u[k] !== v[k]) return false;
    }
    return true;
  }

  function sameLength(entry) {
    const close = (d) => d && Math.abs(d - entry.duration) < 0.02;
    for (const other of local.values()) {
      if (other !== entry && close(other.duration)) return other.name;
    }
    const server = $$(".clip[data-duration]").find((r) => close(Number(r.dataset.duration)));
    return server ? $(".clip-name", server).textContent : null;
  }

  function markInvalid(row, message) {
    row.dataset.status = "failed";
    row.classList.add("clip-failed");
    $("[data-info]", row).textContent = message;
    const toggle = $(".clip-toggle", row);
    toggle.checked = false;
    toggle.disabled = true;
  }

  function removeLocal(row) {
    const entry = local.get(row.dataset.local);
    if (entry) URL.revokeObjectURL(entry.url);
    local.delete(row.dataset.local);
    row.remove();
    refreshFormula();
  }

  const preview = $("#preview");
  function openPreview(row) {
    const entry = local.get(row.dataset.local);
    if (!entry || !preview) return;
    const video = $("video", preview);
    video.src = entry.url;
    $("[data-preview-name]", preview).textContent = entry.file.name;
    preview.showModal();
    video.play().catch(() => {});
  }
  preview?.addEventListener("close", () => {
    const video = $("video", preview);
    video.pause();
    video.removeAttribute("src");
    video.load();
  });

  $$(".dropzone").forEach((zone) => {
    const input = $("input", zone);
    const add = (files) => [...files].forEach((file) => addLocal(input.dataset.type, file));
    input.addEventListener("change", () => { add(input.files); input.value = ""; });
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("over")));
    zone.addEventListener("drop", (e) => { e.preventDefault(); add(e.dataTransfer.files); });
  });

  // --- Row interactions (local and already-uploaded clips) --------------------
  app.addEventListener("change", (e) => {
    if (e.target.name === "mode") return refreshFormula();
    const toggle = e.target.closest(".clip-toggle");
    if (!toggle) return;
    const row = toggle.closest(".clip");
    row.classList.toggle("clip-off", !toggle.checked);
    refreshFormula();
    if (!toggle.dataset.toggleUrl) return; // local file: nothing to tell the server
    const form = new FormData();
    form.append("enabled", toggle.checked ? "1" : "0");
    post(toggle.dataset.toggleUrl, form).catch((msg) => {
      toggle.checked = !toggle.checked;
      row.classList.toggle("clip-off", !toggle.checked);
      refreshFormula();
      notify(msg);
    });
  });

  app.addEventListener("click", (e) => {
    if (uploading) return;
    const thumb = e.target.closest("button.clip-thumb");
    if (thumb) return openPreview(thumb.closest(".clip"));

    const remove = e.target.closest(".clip-remove");
    if (!remove) return;
    const row = remove.closest(".clip");
    if (row.classList.contains("clip-local")) return removeLocal(row);
    row.style.opacity = ".4";
    post(remove.dataset.deleteUrl)
      .then(() => { row.remove(); refreshFormula(); })
      .catch((msg) => { row.style.opacity = ""; notify(msg); });
  });

  // --- Generate: upload the selected files, then start the render ------------
  function applyClip(row, clip) {
    row.classList.remove("clip-local", "clip-uploading");
    delete row.dataset.local;
    row.dataset.clip = clip.uuid;
    row.dataset.status = clip.status;
    row.dataset.order = clip.code.slice(2);
    row.classList.remove("clip-pending", "clip-normalizing", "clip-ready", "clip-failed");
    row.classList.add(`clip-${clip.status}`);
    row.classList.toggle("clip-off", !clip.enabled);
    $(".clip-code", row).textContent = clip.code;
    const info = $("[data-info]", row);
    if (clip.status === "ready") info.textContent = clip.duration ? seconds(clip.duration) : "Listo";
    else if (clip.status === "failed") {
      info.textContent = "No se pudo procesar";
      info.title = clip.error || "";
    } else info.textContent = "Preparando…";
    const toggle = $(".clip-toggle", row);
    if (toggle) {
      toggle.checked = clip.enabled;
      toggle.dataset.toggleUrl = clip.toggle_url;
    }
    const remove = $(".clip-remove", row);
    if (remove) remove.dataset.deleteUrl = clip.delete_url;
  }

  function uploadOne(row, entry, onProgress) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("type", entry.type);
      form.append("file", entry.file);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", uploadUrl);
      xhr.setRequestHeader("X-CSRFToken", csrf);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded);
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) {}
        if (xhr.status === 201) resolve(data);
        else reject(data.error || "No se pudo subir");
      };
      xhr.onerror = () => reject("Se perdió la conexión");
      xhr.send(form);
    });
  }

  async function generate() {
    // Upload in on-screen order, one at a time, so GA01 is the first row you see.
    const queue = TYPES.flatMap((type) =>
      $$(`[data-list="${type}"] .clip-local`).filter(isUsable).map((row) => ({ row, entry: local.get(row.dataset.local) }))
    );
    const totalBytes = queue.reduce((sum, q) => sum + q.entry.file.size, 0);
    let sentBytes = 0;

    uploading = true;
    app.classList.add("is-uploading");
    const button = $("#generate");
    button.disabled = true;
    button.textContent = "Subiendo…";

    try {
      for (const [i, { row, entry }] of queue.entries()) {
        const localId = row.dataset.local; // applyClip() clears it once the row becomes a server clip
        row.classList.add("clip-uploading");
        const bar = $(".clip-progress i", row);
        const report = (loaded) => {
          bar.style.width = `${(loaded / entry.file.size) * 100}%`;
          setProgress("Subiendo videos", i, queue.length, `${mb(sentBytes + loaded)} de ${mb(totalBytes)}`);
        };
        report(0);
        let clip;
        try {
          clip = await uploadOne(row, entry, report);
        } catch (msg) {
          row.classList.remove("clip-uploading");
          $("[data-info]", row).textContent = msg;
          row.classList.add("clip-upload-error");
          throw `No se pudo subir ${entry.file.name}: ${msg}`;
        }
        sentBytes += entry.file.size;
        applyClip(row, clip);
        // Uploaded: the local file is no longer previewable, keep only the still frame.
        const thumb = $("button.clip-thumb", row);
        const still = document.createElement("span");
        still.className = "clip-thumb";
        still.append(...thumb.childNodes);
        thumb.replaceWith(still);
        URL.revokeObjectURL(entry.url);
        local.delete(localId);
      }
      setProgress("Iniciando generación", queue.length, queue.length, `${mb(totalBytes)} subidos`);
      const form = new FormData();
      form.append("mode", selectedMode());
      const result = await post(generateUrl, form);
      uploading = false;
      window.location.href = result.redirect;
    } catch (msg) {
      uploading = false;
      app.classList.remove("is-uploading");
      progress.hidden = true;
      notify(typeof msg === "string" ? msg : "Algo falló. Inténtalo de nuevo.");
      refreshFormula();
      if (!button.disabled) button.textContent = "Reintentar";
    }
  }

  $("#generate")?.addEventListener("click", generate);

  window.addEventListener("beforeunload", (e) => {
    if (uploading || local.size) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // --- Polling while the server works -----------------------------------------
  let timer = null;
  const schedule = () => { if (!timer) timer = setTimeout(poll, 2000); };

  function poll() {
    timer = null;
    fetch(statusUrl, { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((data) => {
        data.clips.forEach((clip) => {
          const row = $(`.clip[data-clip="${clip.uuid}"]`);
          if (row) applyClip(row, clip);
        });
        refreshFormula();
        if (!isDraft) applyVariants(data);
        if (data.busy) schedule();
      })
      .catch(schedule);
  }

  function drawStrips(variants) {
    const totals = variants.map((v) => v.segments.reduce((a, b) => a + b, 0));
    const longest = Math.max(0, ...totals);
    if (!longest) return;
    variants.forEach((v, i) => {
      const row = $(`.variant[data-variant="${v.uuid}"]`);
      if (!row || !totals[i]) return;
      $(".strip", row).style.setProperty("--w", `${(totals[i] / longest) * 100}%`);
      $$(".strip i", row).forEach((seg, j) => { seg.style.flex = v.segments[j] || 1; });
    });
  }

  function applyVariants(data) {
    drawStrips(data.variants);
    let ready = 0;
    let finished = 0;
    data.variants.forEach((v) => {
      if (["done", "failed", "expired"].includes(v.status)) finished += 1;
      const row = $(`.variant[data-variant="${v.uuid}"]`);
      if (!row) return;
      const state = $("[data-state]", row);
      if (v.download_url) {
        ready += 1;
        if (!$("a", state)) {
          state.innerHTML = `<a class="btn btn-small" href="${v.download_url}">Descargar</a>`;
        }
      } else {
        const text = { failed: "Falló", processing: "Uniendo…", pending: "En cola" }[v.status] || "Vencido";
        const cls = v.status === "done" ? "expired" : v.status;
        const badge = document.createElement("span");
        badge.className = `status status-${cls}`;
        badge.textContent = text;
        if (v.error) badge.title = v.error;
        state.replaceChildren(badge);
      }
      const similarity = $("[data-similarity]", row);
      if (similarity) {
        similarity.className = `similarity similarity-${v.similar_level}`;
        similarity.title = v.similar_note;
        similarity.hidden = !v.similar_label;
        $("[data-similarity-text]", similarity).textContent = v.similar_label;
      }
      row.dataset.status = v.status;
      row.className = `variant variant-${v.download_url ? "done" : v.status === "done" ? "expired" : v.status}`;
    });

    const used = data.clips.filter((c) => c.enabled);
    const clipsReady = used.filter((c) => c.status === "ready" || c.status === "failed").length;
    if (data.status === "done") {
      if (progress) progress.hidden = true;
    } else if (clipsReady < used.length) {
      setProgress("Preparando clips", clipsReady, used.length);
    } else {
      setProgress("Uniendo videos", finished, data.variants.length);
    }

    $("#done-count").textContent = ready;
    const expiries = data.variants.map((v) => v.expires_at).filter(Boolean).sort();
    showExpiry(expiries[0]);
    const all = $("#download-all");
    all.classList.toggle("disabled", ready === 0);
    all.setAttribute("aria-disabled", ready === 0 ? "true" : "false");
    const status = $("#project-status");
    if (status && data.status === "done") {
      status.textContent = "Terminado";
      status.className = "status status-done";
    }
  }

  // One deadline for the whole project instead of a countdown on every row.
  function showExpiry(iso) {
    const note = $("#expiry");
    if (note && iso) note.textContent = `Descárgalos antes de las ${clock(iso)}. Después se borran, igual que los clips que subiste.`;
  }

  showExpiry($("#expiry")?.dataset.expires);
  refreshFormula();
  poll();
})();
