// Small enhancements. Every page works without this script.
(() => {
  "use strict";

  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const MAX_CELLS = 24; // keep in sync with selectors.MAX_CELLS

  const store = {
    get(key) {
      try {
        return JSON.parse(sessionStorage.getItem(key));
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        sessionStorage.setItem(key, JSON.stringify(value));
      } catch {
        // Private browsing or storage disabled: animations start from the current values
      }
    },
  };

  function countTo(el, to) {
    const from = Number(el.dataset.shown ?? el.textContent);
    el.dataset.shown = to;
    cancelAnimationFrame(el.raf);
    if (reduceMotion || !Number.isFinite(from) || from === to) {
      el.textContent = to;
      return;
    }
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / 450);
      el.textContent = Math.round(from + (to - from) * (1 - (1 - k) ** 3));
      if (k < 1) el.raf = requestAnimationFrame(step);
    };
    el.raf = requestAnimationFrame(step);
  }

  function renderPortions(compartment, capacity, used, animate = true) {
    const old = compartment.querySelector('[data-role="portions"]');
    compartment.dataset.capacity = capacity;
    compartment.querySelector('[data-role="capacity"]').textContent = capacity;
    const wantCells = capacity <= MAX_CELLS;

    if (!wantCells) {
      let bar = old;
      if (!old.classList.contains("portions--bar")) {
        bar = document.createElement("div");
        bar.className = "portions portions--bar";
        bar.dataset.role = "portions";
        bar.setAttribute("aria-hidden", "true");
        bar.append(document.createElement("span"));
        bar.firstChild.className = "bar-fill";
        old.replaceWith(bar);
      }
      bar.firstChild.style.width = `${Math.round((100 * used) / capacity)}%`;
      return;
    }

    let grid = old;
    if (!old.classList.contains("portions--cells")) {
      grid = document.createElement("div");
      grid.className = "portions portions--cells";
      grid.dataset.role = "portions";
      grid.setAttribute("aria-hidden", "true");
      old.replaceWith(grid);
    }
    const cells = [...grid.children].filter((c) => !c.classList.contains("is-leaving"));
    for (let i = cells.length; i < capacity; i++) {
      const cell = document.createElement("span");
      cell.className = "cell";
      if (animate && !reduceMotion) {
        cell.classList.add("is-new");
        cell.style.animationDelay = `${Math.min(i - cells.length, 12) * 25}ms`;
      }
      grid.append(cell);
      cells.push(cell);
    }
    const leaving = cells.splice(capacity);
    leaving.forEach((cell) => {
      if (animate && !reduceMotion) {
        cell.classList.add("is-leaving");
        cell.addEventListener("animationend", () => cell.remove(), { once: true });
      } else {
        cell.remove();
      }
    });
    cells.forEach((cell, i) => cell.classList.toggle("is-used", i < used));
    grid.style.setProperty("--n", Math.max(capacity, cells.length + leaving.length));
    if (leaving.length) {
      setTimeout(() => grid.style.setProperty("--n", capacity), reduceMotion ? 0 : 260);
    }
  }

  function totalsOf(figure) {
    const totals = { capacity: 0, used: 0 };
    figure.querySelectorAll("[data-compartment]").forEach((c) => {
      totals.capacity += Number(c.dataset.capacity);
      totals.used += Number(c.dataset.used);
    });
    totals.free = totals.capacity - totals.used;
    return totals;
  }

  function showTotals(dl, totals) {
    dl.querySelectorAll("[data-total]").forEach((dd) => countTo(dd, totals[dd.dataset.total]));
  }

  // Freezer page: animate from what was shown last time, for example before an edit
  document.querySelectorAll("[data-totals]:not([data-live])").forEach((dl) => {
    const key = `fooddate:freezer:${dl.dataset.totals}`;
    const figure = document.querySelector(`[data-freezer="${dl.dataset.totals}"]`);
    if (!figure) return;
    const now = { totals: totalsOf(figure), capacities: {} };
    figure.querySelectorAll("[data-compartment]").forEach((c) => {
      now.capacities[c.dataset.compartment] = Number(c.dataset.capacity);
    });
    const before = store.get(key);
    store.set(key, now);
    if (!before || reduceMotion) return;

    dl.querySelectorAll("[data-total]").forEach((dd) => {
      dd.dataset.shown = before.totals[dd.dataset.total] ?? 0;
      dd.textContent = dd.dataset.shown;
    });
    figure.querySelectorAll("[data-compartment]").forEach((c) => {
      const previous = before.capacities[c.dataset.compartment];
      if (previous && previous !== now.capacities[c.dataset.compartment]) {
        renderPortions(c, previous, Number(c.dataset.used), false);
        requestAnimationFrame(() =>
          renderPortions(c, now.capacities[c.dataset.compartment], Number(c.dataset.used)),
        );
      }
    });
    requestAnimationFrame(() => showTotals(dl, now.totals));
  });

  // Number fields get - and + buttons
  document.querySelectorAll("input[data-stepper]").forEach((input) => {
    const name = input.labels[0]?.textContent.replace(/\s+/g, " ").trim() ?? "";
    const wrap = document.createElement("div");
    wrap.className = "stepper";
    const button = (label, path, delta) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "icon-button";
      b.setAttribute("aria-label", `${label} ${name.toLowerCase()}`.trim());
      b.innerHTML = `<svg class="icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="${path}"/></svg>`;
      b.addEventListener("click", () => {
        const min = Number(input.min || -Infinity);
        const max = Number(input.max || Infinity);
        const value = Number(input.value) || 0;
        input.value = Math.min(max, Math.max(min, value + delta));
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
      return b;
    };
    input.replaceWith(wrap);
    wrap.append(button("Decrease", "M5 12h14", -1), input, button("Increase", "M12 5v14M5 12h14", 1));
  });

  // Add freezer: show only the fields for the chosen type
  document.querySelectorAll('input[name="kind"]').forEach((radio, _, radios) => {
    const update = () => {
      const kind = [...radios].find((r) => r.checked)?.value;
      document.querySelectorAll("[data-kind-fields]").forEach((group) => {
        group.hidden = Boolean(kind) && group.dataset.kindFields !== kind;
      });
    };
    radio.addEventListener("change", update);
    update();
  });

  // Edit freezer: the preview follows the form as you type
  document.querySelectorAll("form[data-live-form]").forEach((form) => {
    const figure = document.querySelector("[data-freezer]");
    const dl = document.querySelector("[data-totals][data-live]");
    if (!figure || !dl) return;
    const compartments = [...figure.querySelectorAll("[data-compartment]")];
    const idInputs = [...form.querySelectorAll('input[name^="compartments-"][name$="-id"]')];

    idInputs.forEach((idInput) => {
      const prefix = idInput.name.slice(0, -"id".length);
      const compartment = compartments.find((c) => c.dataset.compartment === idInput.value);
      if (!compartment) return;
      const capacity = form.elements[`${prefix}capacity`];
      const name = form.elements[`${prefix}name`];
      capacity?.addEventListener("input", () => {
        const value = Number(capacity.value);
        if (!Number.isInteger(value) || value < 1 || value > Number(capacity.max)) return;
        renderPortions(compartment, value, Number(compartment.dataset.used));
        showTotals(dl, totalsOf(figure));
      });
      name?.addEventListener("input", () => {
        compartment.querySelector('[data-role="name"]').textContent = name.value.trim() || "Unnamed";
      });
    });
  });
})();
