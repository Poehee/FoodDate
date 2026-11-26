// Small enhancements. Every page works without this script.
(() => {
  "use strict";

  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const MAX_CELLS = 24; // keep in sync with selectors.MAX_CELLS
  const UNDO_SECONDS = 5;

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

  const after = (ms, fn) => setTimeout(fn, reduceMotion ? 0 : ms);

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

  const huesOf = (compartment) =>
    [...compartment.querySelectorAll(".cell.is-used")].map((c) => Number(c.style.getPropertyValue("--h")));

  // Redraw a compartment's cells. hues has one entry per used portion, soonest first.
  function renderPortions(compartment, capacity, hues, { animate = true, pop = [] } = {}) {
    const old = compartment.querySelector('[data-role="portions"]');
    compartment.dataset.capacity = capacity;
    compartment.dataset.used = hues.length;
    compartment.querySelector('[data-role="capacity"]').textContent = capacity;
    const used = compartment.querySelector('[data-role="used"]');
    if (used) countTo(used, hues.length);

    if (capacity > MAX_CELLS) {
      let bar = old;
      if (!old.classList.contains("portions--bar")) {
        bar = document.createElement("span");
        bar.className = "portions portions--bar";
        bar.dataset.role = "portions";
        bar.setAttribute("aria-hidden", "true");
        bar.append(document.createElement("span"));
        bar.firstChild.className = "bar-fill";
        old.replaceWith(bar);
      }
      bar.firstChild.style.width = `${Math.round((100 * hues.length) / capacity)}%`;
      if (hues.length) bar.firstChild.style.setProperty("--h", hues[0]);
      return;
    }

    let grid = old;
    if (!old.classList.contains("portions--cells")) {
      grid = document.createElement("span");
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
    cells.forEach((cell, i) => {
      const isUsed = i < hues.length;
      cell.classList.toggle("is-used", isUsed);
      if (isUsed) cell.style.setProperty("--h", hues[i]);
      else cell.style.removeProperty("--h");
      if (animate && !reduceMotion && pop.includes(i)) {
        cell.classList.remove("is-pop");
        void cell.offsetWidth; // restart the animation
        cell.classList.add("is-pop");
      }
    });
    grid.style.setProperty("--n", Math.max(capacity, cells.length + leaving.length));
    if (leaving.length) after(260, () => grid.style.setProperty("--n", capacity));
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

  function snapshot(figure) {
    const compartments = {};
    figure.querySelectorAll("[data-compartment]").forEach((c) => {
      compartments[c.dataset.compartment] = { capacity: Number(c.dataset.capacity), hues: huesOf(c) };
    });
    return { totals: totalsOf(figure), compartments };
  }

  // Freezer page: animate from what was shown last time, for example before adding food
  const viewTotals = document.querySelector("[data-totals]:not([data-live])");
  const viewFigure = viewTotals && document.querySelector(`[data-freezer="${viewTotals.dataset.totals}"]`);
  const viewKey = viewTotals && `fooddate:freezer:${viewTotals.dataset.totals}`;
  const remember = () => viewKey && store.set(viewKey, snapshot(viewFigure));

  if (viewFigure) {
    const before = store.get(viewKey);
    const now = snapshot(viewFigure);
    store.set(viewKey, now);
    if (before?.compartments && !reduceMotion) {
      viewTotals.querySelectorAll("[data-total]").forEach((dd) => {
        dd.dataset.shown = before.totals[dd.dataset.total] ?? 0;
        dd.textContent = dd.dataset.shown;
      });
      viewFigure.querySelectorAll("[data-compartment]").forEach((c) => {
        const was = before.compartments[c.dataset.compartment];
        const is = now.compartments[c.dataset.compartment];
        if (!was || (was.capacity === is.capacity && was.hues.join() === is.hues.join())) return;
        renderPortions(c, was.capacity, was.hues, { animate: false });
        const pop = is.hues.map((_, i) => i).slice(was.hues.length);
        requestAnimationFrame(() => renderPortions(c, is.capacity, is.hues, { pop }));
      });
      requestAnimationFrame(() => showTotals(viewTotals, now.totals));
    }
  }

  // Undo toast
  const toasts = document.querySelector("[data-toasts]");

  function dismiss(toast) {
    if (!toast.isConnected) return;
    toast.classList.add("is-leaving");
    after(260, () => toast.remove());
  }

  function armToast(toast) {
    const timer = setTimeout(() => dismiss(toast), UNDO_SECONDS * 1000);
    toast.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearTimeout(timer);
      const button = toast.querySelector("button");
      button.disabled = true;
      try {
        const response = await post(toast);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        remember(); // the reload animates the portion coming back
        location.reload();
      } catch (error) {
        toast.querySelector("p").textContent = error.message || "Couldn't undo. Try again.";
        button.remove();
        after(3000, () => dismiss(toast));
      }
    });
  }

  function showUndo(undo, csrf) {
    toasts.querySelectorAll("form.toast").forEach(dismiss);
    const toast = document.createElement("form");
    toast.className = "toast";
    toast.method = "post";
    toast.action = toasts.dataset.undoUrl;
    toast.innerHTML =
      '<input type="hidden" name="csrfmiddlewaretoken"><input type="hidden" name="token">' +
      '<p></p><button class="button button--quiet" type="submit">Undo</button>' +
      '<span class="toast-timer" aria-hidden="true"></span>';
    toast.elements.csrfmiddlewaretoken.value = csrf;
    toast.elements.token.value = undo.token;
    toast.querySelector("p").textContent = undo.text;
    toasts.append(toast);
    armToast(toast);
  }

  toasts?.querySelectorAll("form.toast").forEach(armToast);
  toasts?.querySelectorAll("[data-message]").forEach((message) => {
    const seconds = message.classList.contains("toast--error") ? 10 : 6;
    setTimeout(() => dismiss(message), seconds * 1000);
    message.addEventListener("click", () => dismiss(message));
  });

  function post(form) {
    return fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
  }

  // Take 1: update the drawer in place instead of reloading
  document.querySelectorAll("form[data-take]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button");
      if (button.disabled) return;
      button.disabled = true;
      const row = form.closest("[data-item]");
      let data;
      try {
        const response = await post(form);
        data = await response.json();
        if (response.status === 409 && data.reload) {
          remember();
          location.reload();
          return;
        }
        if (!response.ok) throw new Error(data.error);
      } catch {
        button.disabled = false;
        form.submit(); // let the server handle it the plain way
        return;
      }

      const compartment = viewFigure?.querySelector(`[data-compartment="${data.compartment.id}"]`);
      if (compartment) {
        renderPortions(compartment, data.compartment.capacity, data.compartment.hues);
        const warning = compartment.querySelector('[data-role="expired"]');
        if (warning && !data.compartment.expired) warning.textContent = "";
      }
      if (viewTotals) showTotals(viewTotals, data.totals);
      const free = document.querySelector(`[data-panel="${data.compartment.id}"] [data-role="free"]`);
      if (free) free.textContent = `${data.compartment.free} free`;

      if (data.item.portions === 0) {
        row.classList.add("is-leaving");
        after(300, () => row.remove());
      } else {
        row.querySelector('[data-role="portions-text"]').textContent = data.portions_text;
        form.elements.portions.value = data.item.portions;
        button.disabled = false;
      }
      remember();
      showUndo(data.undo, form.elements.csrfmiddlewaretoken.value);
    });
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
        // An empty field starts from its placeholder, such as the default shelf life
        const value = Number(input.value || input.placeholder) || 0;
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

  // Stock filters are folded away on phones unless one is active; wider screens show them
  const panel = document.querySelector("[data-filters-panel]");
  if (panel && matchMedia("(min-width: 48rem)").matches) panel.open = true;

  // Stock filters: apply as soon as something changes
  document.querySelectorAll("form[data-filters]").forEach((form) => {
    const range = form.querySelector("[data-range]");
    const apply = form.querySelector("[data-apply]");
    const isRange = () => form.elements.expires?.value === "range";
    if (range) range.hidden = !isRange();
    // Clear filters stays visible, only the button that applies them goes
    if (apply) apply.hidden = true;
    if (apply && !form.querySelector(".filter-actions a")) apply.parentElement.hidden = true;

    let timer;
    form.addEventListener("change", (event) => {
      if (event.target.name === "expires" && range) {
        range.hidden = !isRange();
        if (isRange()) {
          form.elements.expires_from.focus();
          return; // wait for a date
        }
      }
      clearTimeout(timer);
      // Dates are often typed digit by digit: wait for a pause
      const delay = event.target.type === "date" ? 600 : 0;
      timer = setTimeout(() => {
        const params = new URLSearchParams(new FormData(form));
        for (const [key, value] of [...params]) if (!value) params.delete(key);
        if (!isRange()) {
          params.delete("expires_from");
          params.delete("expires_to");
        }
        const query = params.toString();
        location.assign(query ? `?${query}` : location.pathname);
      }, delay);
    });
  });

  // Food form: preview the expiration date
  document.querySelectorAll("form[data-food-form]").forEach((form) => {
    const preview = form.querySelector("[data-expiry-preview]");
    const type = form.elements.food_type;
    const date = form.elements.frozen_on;
    if (!preview || !type || !date) return;
    const update = () => {
      const weeks = Number(type.selectedOptions[0]?.dataset.weeks || type.dataset.defaultWeeks);
      const frozen = date.valueAsDate;
      preview.hidden = !frozen || !weeks;
      if (preview.hidden) return;
      const expires = new Date(frozen.getTime() + weeks * 7 * 864e5);
      const dd = String(expires.getUTCDate()).padStart(2, "0");
      const mm = String(expires.getUTCMonth() + 1).padStart(2, "0");
      preview.querySelector('[data-role="date"]').textContent =
        `${dd}-${mm}-${expires.getUTCFullYear()} (${weeks} weeks)`;
    };
    type.addEventListener("change", update);
    date.addEventListener("input", update);
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
      const hues = huesOf(compartment);
      capacity?.addEventListener("input", () => {
        const value = Number(capacity.value);
        if (!Number.isInteger(value) || value < Math.max(1, hues.length) || value > Number(capacity.max)) return;
        renderPortions(compartment, value, hues);
        showTotals(dl, totalsOf(figure));
      });
      name?.addEventListener("input", () => {
        compartment.querySelector('[data-role="name"]').textContent = name.value.trim() || "Unnamed";
      });
    });
  });
})();
