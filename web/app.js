(function () {
  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty");
  const clock = document.getElementById("clock");
  const title = document.getElementById("page-title");
  const hideOffline = document.getElementById("hide-offline");
  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  const lightboxCaption = document.getElementById("lightbox-caption");

  function dash(value) {
    return value === null || value === undefined || value === "" ? "—" : value;
  }

  function statusOf(machine) {
    if (!machine.online) return { cls: "bad", label: "офлайн" };
    if (machine.window_found && machine.has_data) return { cls: "ok", label: "в работе" };
    if (machine.window_found) return { cls: "warn", label: "окно есть, нет данных" };
    return { cls: "warn", label: "нет окна AutoCut" };
  }

  function render(payload) {
    if (payload && payload.title) {
      title.textContent = payload.title;
      document.title = payload.title;
    }

    const machines = (payload && payload.machines) || [];
    const visible = hideOffline.checked
      ? machines.filter(function (m) { return m.online; })
      : machines;

    grid.innerHTML = "";
    if (!visible.length) {
      empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden");

    visible.forEach(function (machine) {
      const st = statusOf(machine);
      const tile = document.createElement("article");
      tile.className = "tile";

      const previewSrc = machine.preview_url
        ? machine.preview_url + (machine.preview_url.indexOf("?") >= 0 ? "&" : "?") + "t=" + Date.now()
        : "";

      tile.innerHTML =
        '<div class="tile-head">' +
          '<div class="tile-name"></div>' +
          '<div class="status"><span class="dot ' + st.cls + '"></span>' + st.label + "</div>" +
        "</div>" +
        '<div class="preview-wrap">' +
          (previewSrc ? '<img alt="Экран станка">' : "") +
          '<div class="preview-placeholder">Нет снимка экрана</div>' +
        "</div>" +
        '<div class="stats">' +
          '<div class="stat"><span>X</span><strong></strong></div>' +
          '<div class="stat"><span>Y</span><strong></strong></div>' +
          '<div class="stat"><span>Скорость</span><strong></strong></div>' +
          '<div class="stat"><span>В работе</span><strong></strong></div>' +
          '<div class="stat"><span>Осталось</span><strong></strong></div>' +
          '<div class="stat"><span>Деталь</span><strong></strong></div>' +
        "</div>";

      tile.querySelector(".tile-name").textContent = machine.name || machine.id || "Станок";
      const values = [
        dash(machine.X),
        dash(machine.Y),
        machine.Speed ? machine.Speed + " Hz" : "—",
        dash(machine.WorkingTime),
        dash(machine.SurplusTime),
        dash(machine.DetailNo),
      ];
      tile.querySelectorAll(".stat strong").forEach(function (el, idx) {
        el.textContent = values[idx];
      });

      const img = tile.querySelector("img");
      const placeholder = tile.querySelector(".preview-placeholder");
      if (img) {
        img.onload = function () { placeholder.classList.add("hidden"); };
        img.onerror = function () { img.classList.add("hidden"); };
        img.src = previewSrc;
        tile.querySelector(".preview-wrap").addEventListener("click", function () {
          lightboxImg.src = img.src;
          lightboxCaption.textContent = machine.name || "";
          lightbox.classList.remove("hidden");
        });
      }

      grid.appendChild(tile);
    });
  }

  async function tick() {
    clock.textContent = new Date().toLocaleString("ru-RU");
    try {
      const response = await fetch("/api/fleet", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      render(await response.json());
    } catch (err) {
      empty.textContent = "Нет связи с монитором: " + err.message;
      empty.classList.remove("hidden");
      grid.innerHTML = "";
    }
  }

  document.getElementById("lightbox-close").addEventListener("click", function () {
    lightbox.classList.add("hidden");
  });
  lightbox.addEventListener("click", function (ev) {
    if (ev.target === lightbox) lightbox.classList.add("hidden");
  });
  hideOffline.addEventListener("change", tick);

  tick();
  setInterval(tick, 3000);
})();
