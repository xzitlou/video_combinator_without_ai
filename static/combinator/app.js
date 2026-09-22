(function () {
  const app = document.getElementById("app");
  if (!app) return;

  const csrf = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || "";
  const statusUrl = app.dataset.statusUrl;
  const uploadUrl = app.dataset.uploadUrl;
  const maxVariants = Number(app.dataset.maxVariants);
  const maxClipBytes = Number(app.dataset.maxClipMb) * 1024 * 1024;
  const isDraft = app.dataset.projectStatus === "draft";
  const TYPES = ["hook", "body", "closer"];
  const LABELS = { hook: "GA", body: "CO", closer: "CI" };

  const post = (url, data) =>
    fetch(url, { method: "POST", body: data, headers: { "X-CSRFToken": csrf } }).then((r) =>
      r.json().then((json) => (r.ok ? json : Promise.reject(json.error || "Algo falló.")))
    );

  function notify(message) {
    let box = document.querySelector(".messages");
    if (!box) {
      box = document.createElement("div");
      box.className = "messages";
      app.parentNode.insertBefore(box, app);
    }
    const p = document.createElement("p");
    p.className = "message message-error";
    p.textContent = message;
    box.appendChild(p);
    setTimeout(() => p.remove(), 6000);
  }

  const seconds = (s) => `${Number(s).toFixed(1)} s`;
  const minutesLeft = (s) => (s >= 60 ? `${Math.floor(s / 60)} min` : `${s} s`);

  // --- Formula & generate button -------------------------------------------
  function refreshFormula() {
    if (!isDraft) return;
    const counts = {};
    let allReady = true;
    TYPES.forEach((type) => {
      const rows = [...document.querySelectorAll(`[data-list="${type}"] .clip`)];
      counts[type] = rows.filter((r) => r.querySelector(".clip-toggle")?.checked).length;
      allReady = allReady && rows.every((r) => !r.querySelector(".clip-toggle")?.checked || r.dataset.status === "ready");
      document.querySelector(`[data-n="${type}"]`).textContent = counts[type];
      document.querySelector(`[data-count-for="${type}"]`).textContent = rows.length;
    });
    // Closers are optional: with none enabled each video is hook + body.
    const total = counts.hook * counts.body * Math.max(counts.closer, 1);
    document.querySelectorAll("[data-closer-term]").forEach((el) => el.classList.toggle("term-off", counts.closer === 0));
    document.getElementById("count").textContent = total;
    document.getElementById("count-label").textContent = total === 1 ? "video" : "videos";

    const button = document.getElementById("generate");
    const note = document.getElementById("panel-note");
    note.classList.remove("over");
    if (total === 0) {
      note.textContent = "Sube al menos un gancho y un contenido.";
    } else if (total > maxVariants) {
      note.textContent = `Son demasiados: el máximo es ${maxVariants}. Desmarca algunos clips.`;
      note.classList.add("over");
    } else if (!allReady) {
      note.textContent = "Preparando los clips…";
    } else {
      note.textContent = counts.closer
        ? "Cada video une un gancho, un contenido y un cierre, en ese orden."
        : "Sin cierres, cada video une un gancho y un contenido.";
    }
    button.disabled = total === 0 || total > maxVariants || !allReady;
    button.textContent = total > 0 ? `Generar ${total} video${total === 1 ? "" : "s"}` : "Generar videos";
  }

  // --- Clip rows -----------------------------------------------------------
  function applyClip(row, clip) {
    row.dataset.clip = clip.id;
    row.dataset.status = clip.status;
    row.className = `clip clip-${clip.status}${clip.enabled ? "" : " clip-off"}`;
    row.querySelector(".clip-code").textContent = clip.code;
    row.querySelector(".clip-name").textContent = clip.name;
    row.querySelector(".clip-name").title = clip.name;
    const info = row.querySelector("[data-info]");
    if (clip.status === "ready") info.textContent = clip.duration ? seconds(clip.duration) : "Listo";
    else if (clip.status === "failed") {
      info.textContent = "No se pudo procesar";
      info.title = clip.error || "";
    } else info.textContent = "Preparando…";
    const toggle = row.querySelector(".clip-toggle");
    if (toggle) {
      toggle.checked = clip.enabled;
      toggle.dataset.toggleUrl = `/clips/${clip.id}/toggle/`;
      toggle.setAttribute("aria-label", `Usar ${clip.name}`);
    }
    const remove = row.querySelector(".clip-remove");
    if (remove) remove.dataset.deleteUrl = `/clips/${clip.id}/delete/`;
  }

  function uploadFile(type, file) {
    const list = document.querySelector(`[data-list="${type}"]`);
    const row = document.getElementById("clip-template").content.firstElementChild.cloneNode(true);
    row.classList.add("clip-uploading");
    row.querySelector(".clip-code").textContent = LABELS[type];
    row.querySelector(".clip-name").textContent = file.name;
    row.querySelector(".clip-toggle").disabled = true;
    row.querySelector(".clip-remove").hidden = true;
    list.appendChild(row);
    refreshFormula();

    if (file.size > maxClipBytes) {
      failRow(row, `Supera los ${app.dataset.maxClipMb} MB`);
      return;
    }

    const form = new FormData();
    form.append("type", type);
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", uploadUrl);
    xhr.setRequestHeader("X-CSRFToken", csrf);
    const bar = row.querySelector(".clip-progress i");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) bar.style.width = `${Math.round((e.loaded / e.total) * 100)}%`;
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) {}
      if (xhr.status !== 201) return failRow(row, data.error || "No se pudo subir");
      row.classList.remove("clip-uploading");
      row.querySelector(".clip-toggle").disabled = false;
      row.querySelector(".clip-remove").hidden = false;
      applyClip(row, data);
      refreshFormula();
      startPolling();
    };
    xhr.onerror = () => failRow(row, "Se perdió la conexión");
    xhr.send(form);
  }

  function failRow(row, message) {
    row.classList.remove("clip-uploading");
    row.classList.add("clip-failed");
    row.dataset.status = "failed";
    row.querySelector("[data-info]").textContent = message;
    row.querySelector(".clip-toggle").checked = false;
    row.querySelector(".clip-toggle").disabled = true;
    const remove = row.querySelector(".clip-remove");
    remove.hidden = false;
    remove.onclick = () => { row.remove(); refreshFormula(); };
    refreshFormula();
  }

  document.querySelectorAll(".dropzone").forEach((zone) => {
    const input = zone.querySelector("input");
    input.addEventListener("change", () => {
      [...input.files].forEach((file) => uploadFile(input.dataset.type, file));
      input.value = "";
    });
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("over")));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      [...e.dataTransfer.files].forEach((file) => uploadFile(input.dataset.type, file));
    });
  });

  app.addEventListener("change", (e) => {
    const toggle = e.target.closest(".clip-toggle");
    if (!toggle || !toggle.dataset.toggleUrl) return;
    const row = toggle.closest(".clip");
    row.classList.toggle("clip-off", !toggle.checked);
    refreshFormula();
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
    const button = e.target.closest(".clip-remove");
    if (!button || !button.dataset.deleteUrl) return;
    const row = button.closest(".clip");
    row.style.opacity = ".4";
    post(button.dataset.deleteUrl, new FormData())
      .then(() => { row.remove(); refreshFormula(); })
      .catch((msg) => { row.style.opacity = ""; notify(msg); });
  });

  // --- Polling -------------------------------------------------------------
  let timer = null;
  function startPolling() {
    if (timer) return;
    timer = setTimeout(poll, 2500);
  }

  function poll() {
    timer = null;
    fetch(statusUrl, { headers: { Accept: "application/json" } })
      .then((r) => r.json())
      .then((data) => {
        data.clips.forEach((clip) => {
          const row = document.querySelector(`.clip[data-clip="${clip.id}"]`);
          if (row) applyClip(row, clip);
        });
        refreshFormula();
        applyVariants(data);
        if (data.busy) startPolling();
      })
      .catch(startPolling);
  }

  function applyVariants(data) {
    let ready = 0;
    data.variants.forEach((v) => {
      const row = document.querySelector(`.variant[data-variant="${v.id}"]`);
      if (!row) return;
      const state = row.querySelector("[data-state]");
      if (v.download_url) {
        ready += 1;
        if (row.dataset.status !== "done") {
          state.innerHTML = `<a class="btn btn-small" href="${v.download_url}">Descargar</a><small data-left></small>`;
        }
        state.querySelector("[data-left]").textContent = `quedan ${minutesLeft(v.seconds_left)}`;
      } else if (row.dataset.status !== v.status || v.status === "done") {
        const text = { failed: "Falló", processing: "Uniendo…", pending: "En cola" }[v.status] || "Vencido";
        const cls = v.status === "done" ? "expired" : v.status;
        state.innerHTML = `<span class="status status-${cls}"${v.error ? ` title="${v.error}"` : ""}>${text}</span>`;
      }
      row.dataset.status = v.status;
      row.className = `variant variant-${v.download_url ? "done" : v.status === "done" ? "expired" : v.status}`;
    });
    const doneCount = document.getElementById("done-count");
    if (doneCount) doneCount.textContent = ready;
    const all = document.getElementById("download-all");
    if (all) {
      all.classList.toggle("disabled", ready === 0);
      all.setAttribute("aria-disabled", ready === 0 ? "true" : "false");
    }
    const status = document.getElementById("project-status");
    if (status && data.status === "done") {
      status.textContent = "Terminada";
      status.className = "status status-done";
    }
  }

  refreshFormula();
  poll();
})();
