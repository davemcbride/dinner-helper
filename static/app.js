"use strict";

// ---------------------------------------------------------------------------
// tiny DOM + HTTP helpers
// ---------------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function")
      node.addEventListener(k.slice(2), v);
    else if (k === "value") node.value = v;
    else if (k === "style" && v) node.style.cssText = v;
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of Array.isArray(children) ? children : [children]) {
    if (c === null || c === undefined) continue;
    node.append(
      typeof c === "string" || typeof c === "number"
        ? document.createTextNode(String(c))
        : c
    );
  }
  return node;
}

async function api(path, options = {}) {
  const opts = { headers: {}, ...options };
  if (opts.body && !(opts.body instanceof FormData))
    opts.headers["Content-Type"] = "application/json";
  if (opts.body && typeof opts.body !== "string")
    opts.body = JSON.stringify(opts.body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.status >= 500 ? "server error" : "request failed";
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

let toastTimer = null;
function toast(msg, isError = false, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", isError);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------
const state = {
  tab: "pick",
  meals: [],          // enabled, unflagged meals
  review: [],         // flagged meals awaiting a decision
  duplicates: [],     // enabled meals with a suggested merge target
  recent: [],
  plans: [],          // planned meals for the visible week
  weekStart: null,    // ISO date of the Friday starting the visible week
  pickMode: "rare",
  currentPick: null,
  search: "",
  filter: "all",
};

const main = () => $("#app");

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------
function render() {
  const views = { pick: viewPick, meals: viewMeals, stats: viewStats, review: viewReview, week: viewWeek };
  views[state.tab]();
}

function setTab(name) {
  state.tab = name;
  document.querySelectorAll(".tabbar .tab").forEach((t) =>
    t.setAttribute("aria-selected", t.dataset.tab === name ? "true" : "false")
  );
  render();
}

function section(title) {
  return el("div", { class: "section-title" }, title);
}

// ------------------------------------------------ Pick --------------------
function viewPick() {
  const hero = el("div", { class: "pick-hero" }, [
    el("div", { class: "tag" }, "Dinner Helper"),
    el("h1", {}, "What are we having?"),
    el("div", { class: "sub" }, "Rare meals get their turn more often."),
  ]);

  const seg = el("div", { class: "seg", role: "group" });
  [["rare", "Rare first"], ["random", "Random"], ["favourite", "Favourites"]]
    .forEach(([m, label]) =>
      seg.append(el("button", {
        type: "button",
        "aria-pressed": state.pickMode === m ? "true" : "false",
        onclick: () => { state.pickMode = m; setTab("pick"); },
      }, label))
    );

  main().replaceChildren(
    hero,
    el("div", { class: "btn-row", style: "margin-top:14px;justify-content:center" }, [seg]),
    state.currentPick ? pickCard(state.currentPick) : pickPlaceholder(),
    section("How it works"),
    el("p", { style: "color:var(--muted);font-size:14px;line-height:1.5" },
      "The picker favours meals never logged or only had once. Out of ideas? " +
      "Hit “Pick another” until it lands.")
  );
}

function pickPlaceholder() {
  return el("div", { class: "pick-result" }, [
    el("div", { class: "meal-name" }, "Tap to suggest"),
    el("div", { class: "meal-meta" }, "nothing chosen yet"),
    el("div", { class: "btn-row", style: "margin-top:16px" }, [
      el("button", { class: "btn btn-sage", onclick: pickAnother }, "Suggest a meal"),
    ]),
  ]);
}

function pickCard(meal) {
  return el("div", { class: "pick-result" }, [
    el("div", { class: "meal-name" }, meal.name),
    el("div", { class: "meal-meta", html: metaHtml(meal) }),
    el("div", { class: "btn-row", style: "margin-top:18px" }, [
      el("button", { class: "btn btn-sage", onclick: () => logPick(meal) }, "We cooked this!"),
      el("button", { class: "btn btn-ghost", onclick: pickAnother }, "Pick another"),
    ]),
  ]);
}

async function pickAnother() {
  const exclude = state.currentPick ? state.currentPick.id : "";
  const data = await api(`/api/pick?mode=${state.pickMode}&count=1&exclude=${exclude}`);
  state.currentPick = data.picks[0];
  render();
}

async function logPick(meal) {
  try {
    await api(`/api/meals/${meal.id}/log`, { method: "POST", body: {} });
    state.currentPick = null;
    toast(`Cooked ${meal.name} — logged for today`);
    await refresh();
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------ Meals -------------------
function mealBucket(m) {
  if (state.filter === "never" && m.count !== 0) return false;
  if (state.filter === "rare" && m.count !== 1) return false;
  if (state.filter === "favourite" && m.count < 1) return false;
  return true;
}

function renderMealsList() {
  const box = $("#meals-box");
  if (!box) return;
  const q = state.search.trim().toLowerCase();
  const list = state.meals
    .filter(mealBucket)
    .filter((m) => {
      if (!q) return true;
      return (m.name + " " + m.aliases.join(" ")).toLowerCase().includes(q);
    })
    .sort((a, b) => a.name.localeCompare(b.name));

  const wrap = el("div", { class: "list-wrap" });
  list.forEach((m) => wrap.append(mealRow(m)));

  box.replaceChildren(
    el("button", { class: "btn btn-primary btn-block", onclick: openAddSheet }, "+ Add a new meal"),
    el("div", { class: "count-line" }, `${list.length} shown`),
    list.length ? wrap : el("div", { class: "empty" }, "No meals match.")
  );
}

function viewMeals() {
  const search = el("input", {
    class: "searchbar",
    placeholder: "Search meals…",
    value: state.search,
    oninput: (e) => { state.search = e.target.value; renderMealsList(); },
  });

  let never = 0, rare = 0, favourite = 0;
  state.meals.forEach((m) => {
    if (m.count === 0) never++;
    if (m.count <= 1) rare++;
    if (m.count >= 1) favourite++;
  });
  const counts = { all: state.meals.length, never, rare, favourite };

  const chips = el("div", { class: "chips" });
  [["all", "All"], ["never", "Never had"], ["rare", "Had once"], ["favourite", "Favourites"]]
    .forEach(([key, label]) =>
      chips.append(el("button", {
        class: "chip",
        "aria-pressed": state.filter === key ? "true" : "false",
        onclick: (e) => {
          state.filter = key;
          chips.querySelectorAll(".chip").forEach((c) =>
            c.setAttribute("aria-pressed", c === e.currentTarget ? "true" : "false"));
          renderMealsList();
        },
      }, [label, el("span", { class: "count" }, counts[key])]))
    );

  main().replaceChildren(
    el("h1", {}, "Meals"),
    search,
    chips,
    el("div", { id: "meals-box" }),
  );
  renderMealsList();
}

function mealRow(m) {
  const name = el("div", { class: "name" }, m.name);
  name.append(el("span", { class: "sub" }, m.last_served ? `last ${relDate(m.last_served)}` : "never had"));
  const times = el("div", {
    class: "times" + (m.count === 0 ? " zero" : ""),
  }, `${m.count}×`);
  return el("button", { class: "meal-row", onclick: () => openMealSheet(m.id) }, [
    name, times,
  ]);
}

// ------------------------------------------------ Stats -------------------
function viewStats() {
  const { meals, recent } = state;
  const totalLogged = meals.reduce((s, m) => s + m.count, 0);
  const never = meals.filter((m) => m.count === 0);
  const rare = meals.filter((m) => m.count === 1);
  const favs = meals.filter((m) => m.count > 0).sort((a, b) => b.count - a.count).slice(0, 10);

  const cards = el("div", { class: "stat-cards" }, [
    statCard(totalLogged, "meals logged", true),
    statCard(meals.length, "on the list"),
    statCard(never.length, "never had", false, true),
    statCard(rare.length, "had once", false, true),
  ]);

  const recentRows = recent.length
    ? recent.map((r) =>
        el("div", { class: "stat-row" }, [
          el("div", { class: "nm" }, r.meal_name),
          el("div", { style: "display:flex;align-items:center;gap:8px" }, [
            el("span", { class: "ct" }, r.served_on),
            r.note === "seed"
              ? null
              : el("button", {
                  class: "btn btn-ghost",
                  style: "min-height:32px;padding:0 11px;font-size:12px",
                  onclick: async () => {
                    await api(`/api/history/${r.id}`, { method: "DELETE" });
                    toast(`Undid ${r.meal_name}`);
                    await refresh();
                  },
                }, "undo"),
          ]),
        ])
      )
    : [el("div", { class: "empty" }, "Nothing logged yet.")];

  const plain = (items) => items.map((m) =>
    el("div", { class: "stat-row" }, [el("div", { class: "nm" }, m.name)])
  );

  main().replaceChildren(
    el("h1", {}, "Stats"),
    cards,
    section("Favourites"),
    el("div", { class: "stat-list" },
      favs.length
        ? favs.map((m) => el("div", { class: "stat-row" }, [
            el("div", { class: "nm" }, m.name),
            el("div", { class: "ct" }, `${m.count}×`),
          ]))
        : el("div", { class: "empty" }, "No favourites yet.")),
    section("Never had"),
    el("div", { class: "stat-list" },
      never.length ? plain(never.slice(0, 60)) : el("div", { class: "empty" }, "Nothing left untried!")),
    section("Had once (rare)"),
    el("div", { class: "stat-list" },
      rare.length ? plain(rare.slice(0, 60)) : el("div", { class: "empty" }, "None.")),
    section("Recent"),
    el("div", { class: "stat-list" }, recentRows),
    section("Danger zone"),
    el("button", {
      class: "btn btn-danger-ghost btn-block",
      onclick: async () => {
        if (!confirm("Clear the backfilled history? Counts restart from today.")) return;
        const res = await api("/api/history/reset", { method: "POST" });
        toast(`Cleared ${res.deleted} imported entries`);
        await refresh();
      },
    }, "Clear imported history"),
  );
}

function statCard(num, label, hl = false, sg = false) {
  return el("div", { class: "stat-card" + (hl ? " hl" : "") + (sg ? " sg" : "") }, [
    el("div", { class: "num" }, num),
    el("div", { class: "lbl" }, label),
  ]);
}

// ------------------------------------------------ Review ------------------
function viewReview() {
  if (state.review.length === 0 && state.duplicates.length === 0) {
    main().replaceChildren(
      el("h1", {}, "Review"),
      el("div", { class: "empty" }, "Nothing flagged — the list is all clean."),
    );
    return;
  }

  const cards = state.review.map((m) => {
    const meta = el("div", { class: "reason" },
      `flagged: ${m.flag_reasons.join("; ")} · ${m.count}× logged`);
    const hint = m.merge_hint
      ? el("span", { class: "hint" },
          `merge into “${m.merge_hint.into_name}” (${Math.round(m.merge_hint.confidence * 100)}%)`)
      : null;
    const row = el("div", { class: "btn-row" }, []);
    if (hint) {
      row.append(el("button", {
        class: "btn btn-ghost",
        onclick: () => mergeFromReview(m),
      }, "Merge"));
    }
    row.append(
      el("button", { class: "btn btn-sage", onclick: () => resolve(m, "keep") }, "Keep"),
      el("button", {
        class: "btn btn-danger-ghost",
        onclick: () => resolve(m, "delete"),
      }, "Delete"),
    );

    return el("div", { class: "rev-card" }, [
      el("div", { class: "nm", style: "font-weight:700;font-size:16px" }, m.name),
      meta,
      hint,
      row,
    ]);
  });

  const dupCards = state.duplicates.map((m) =>
    el("div", { class: "rev-card" }, [
      el("div", { class: "nm", style: "font-weight:700;font-size:16px" }, m.name),
      el("span", { class: "hint" },
        `merge into “${m.merge_hint.into_name}” (${Math.round(m.merge_hint.confidence * 100)}%)`),
      el("div", { class: "btn-row" }, [
        el("button", {
          class: "btn btn-ghost",
          onclick: () => mergeFromReview(m),
        }, "Merge"),
        el("button", {
          class: "btn btn-danger-ghost",
          onclick: async () => {
            if (!confirm(`Delete “${m.name}” and its ${m.count} logged times?`)) return;
            await api(`/api/meals/${m.id}`, { method: "DELETE" });
            toast(`Deleted ${m.name}`);
            await refresh();
          },
        }, "Delete"),
      ]),
    ])
  );

  main().replaceChildren(
    ...[
      el("h1", {}, "Review"),
      state.review.length
        ? el("p", { style: "color:var(--muted);font-size:14px" },
            `${state.review.length} entries need a decision. “Keep” makes the meal pickable ` +
            "again; merging folds its history into the twin.")
        : null,
      ...(state.review.length
        ? [el("div", { style: "display:flex;flex-direction:column;gap:10px" }, cards)]
        : []),
      ...(state.duplicates.length
        ? [section("Possible duplicates"), el("div", { style: "display:flex;flex-direction:column;gap:10px" }, dupCards)]
        : []),
    ].filter(Boolean)
  );
}

async function resolve(m, action) {
  if (action === "delete" && !confirm(`Delete “${m.name}” and its history?`)) return;
  await api(`/api/meals/${m.id}/resolve`, { method: "POST", body: { action } });
  toast(action === "keep" ? `Kept ${m.name}` : `Deleted ${m.name}`);
  await refresh();
}

async function mergeFromReview(m) {
  const hint = m.merge_hint;
  if (!confirm(`Merge “${m.name}” into “${hint.into_name}”? History and aliases move over.`))
    return;
  await api(`/api/meals/${m.id}/merge`, { method: "POST", body: { into: hint.into } });
  toast(`Merged “${m.name}” into ${hint.into_name}`);
  await refresh();
}

// ------------------------------------------------ Sheet -------------------
function openSheet(html) {
  $("#sheet-content").replaceChildren(html);
  $("#sheet").classList.add("open");
}
function closeSheet() { $("#sheet").classList.remove("open"); }

function openMealSheet(id) {
  const m = state.meals.find((x) => x.id === id) || state.review.find((x) => x.id === id);
  if (!m) return;

  const rows = [el("p", { class: "meta", html: metaHtml(m) })];
  if (m.aliases.length) {
    rows.push(el("p", { class: "meta" }, "also: " + m.aliases.join(", ")));
  }
  if (m.flag_reasons.length) {
    rows.push(el("span", { class: "badge" }, "flagged"));
  }

  openSheet(el("div", {}, [
    el("div", { class: "sheet-title" }, m.name),
    ...rows,
    el("div", { class: "btn-row", style: "margin-top:14px" }, [
      el("button", {
        class: "btn btn-sage",
        onclick: async () => { await logPick(m); closeSheet(); },
      }, "Log for today"),
    ]),
    section("Manage"),
    el("div", { class: "btn-row" }, [
      el("button", { class: "btn btn-ghost", onclick: () => mergePrompt(m) }, "Merge into…"),
      el("button", {
        class: "btn btn-danger-ghost",
        onclick: async () => {
          if (!confirm(`Delete “${m.name}” and its ${m.count} logged times?`)) return;
          await api(`/api/meals/${m.id}`, { method: "DELETE" });
          closeSheet();
          toast(`Deleted ${m.name}`);
          await refresh();
        },
      }, "Delete"),
    ]),
  ]));
}

function openAddSheet() {
  const input = el("input", { class: "input", placeholder: "e.g. Bouillabaisse" });
  openSheet(el("div", {}, [
    el("div", { class: "sheet-title" }, "Add a meal"),
    input,
    el("div", { class: "btn-row" }, [
      el("button", {
        class: "btn btn-primary",
        onclick: async () => {
          const name = input.value.trim();
          if (!name) return;
          const m = await api("/api/meals", { method: "POST", body: { name } });
          closeSheet();
          toast(`Added ${m.name}`);
          await refresh();
        },
      }, "Add meal"),
    ]),
  ]));
}

function mergePrompt(m) {
  const input = el("input", {
    class: "input",
    placeholder: "Search a meal to merge into…",
    list: "merge-targets",
  });
  const targets = el("datalist", { id: "merge-targets" },
    state.meals.filter((x) => x.id !== m.id).map((x) => el("option", { value: x.name })));
  openSheet(el("div", {}, [
    el("div", { class: "sheet-title" }, `Merge “${m.name}” into…`),
    el("p", { class: "meta" }, "The other meal keeps its name; history and aliases move across."),
    input,
    targets,
    el("div", { class: "btn-row" }, [
      el("button", {
        class: "btn btn-primary",
        onclick: async () => {
          const target = state.meals.find((x) => x.name === input.value.trim());
          if (!target) return toast("Pick a meal from the list", true);
          if (!confirm(`Merge “${m.name}” into “${target.name}”?`)) return;
          await api(`/api/meals/${m.id}/merge`, { method: "POST", body: { into: target.id } });
          closeSheet();
          toast(`Merged into ${target.name}`);
          await refresh();
        },
      }, "Merge"),
    ]),
  ]));
}

// ------------------------------------------------ Week --------------------
const WEEKDAY_NAMES = ["Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu"];
const MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const pad2 = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const todayIso = () => iso(new Date());
const shortDate = (s) => { const [, m, d] = s.split("-").map(Number); return `${d} ${MONTH_NAMES[m - 1]}`; };
const weekdayName = (s) => { const [y, m, d] = s.split("-").map(Number); return WEEKDAY_NAMES[(new Date(y, m - 1, d).getDay() + 2) % 7]; };

function isoAdd(isoStr, days) {
  const [y, m, d] = isoStr.split("-").map(Number);
  return iso(new Date(y, m - 1, d + days));
}

function startOfWeek() {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const dow = (today.getDay() + 6) % 7;       // 0=Mon … 6=Sun
  return isoAdd(iso(today), (4 - dow + 7) % 7); // the next Friday on/after today
}

const weekRange = (start) => Array.from({ length: 7 }, (_, i) => isoAdd(start, i));
const canConfirm = (iso) => iso <= todayIso();

async function loadPlans() {
  const end = isoAdd(state.weekStart, 6);
  const data = await api(`/api/plans?start=${state.weekStart}&end=${end}`);
  state.plans = data.plans;
  render();
}

function shiftWeek(days) {
  state.weekStart = isoAdd(state.weekStart, days);
  loadPlans();
}

function backToThisWeek() {
  state.weekStart = startOfWeek();
  loadPlans();
}

function viewWeek() {
  if (!state.weekStart) state.weekStart = startOfWeek();
  const today = todayIso();

  const rows = weekRange(state.weekStart).map((day) => {
    const plan = state.plans.find((p) => p.planned_on === day);
    const confirmButton = plan && canConfirm(day)
      ? el("button", {
          class: "btn btn-sage week-confirm",
          onclick: async () => {
            if (!confirm(`Log ${plan.meal.name} as eaten for ${weekdayName(day)}?`)) return;
            await confirmPlan(day);
          },
        }, "We had this")
      : null;
    return el("div", { class: "week-row" + (day === today ? " today" : "") }, [
      el("button", { class: "week-day", onclick: () => openWeekSheet(day) }, [
        el("div", { class: "week-dow" }, [
          weekdayName(day) + " ",
          el("span", { class: "week-date" }, shortDate(day)),
        ]),
        el("div", { class: "week-meal" + (plan ? "" : " empty") },
          plan ? plan.meal.name : "Tap to plan"),
      ]),
      confirmButton,
    ]);
  });

  const nav = el("div", { class: "week-nav" }, [
    el("button", {
      class: "btn btn-ghost week-nav-btn",
      "aria-label": "Previous week",
      onclick: () => shiftWeek(-7),
    }, "‹"),
    el("div", { class: "week-head" },
      `Week of ${shortDate(state.weekStart)} – ${shortDate(isoAdd(state.weekStart, 6))}`),
    el("button", {
      class: "btn btn-ghost week-nav-btn",
      "aria-label": "Next week",
      onclick: () => shiftWeek(7),
    }, "›"),
  ]);

  main().replaceChildren(
    ...[
      el("h1", {}, "Week plan"),
      nav,
      ...(state.weekStart !== startOfWeek()
        ? [el("button", {
            class: "btn btn-ghost week-back",
            onclick: () => backToThisWeek(),
          }, "Back to this week")]
        : []),
      el("div", { class: "week-list" }, rows),
      section("How it works"),
      el("p", { style: "color:var(--muted);font-size:14px;line-height:1.5" },
        "Plan the week ahead, Friday to Thursday. Tap a day to assign a meal. " +
        "Nothing counts as eaten until you confirm it here."),
    ].filter(Boolean)
  );
}

function fmtDay(iso) { return `${weekdayName(iso)} ${shortDate(iso)}`; }

function openWeekSheet(day) {
  const plan = state.plans.find((p) => p.planned_on === day);
  const meal = plan ? plan.meal : null;
  const input = el("input", {
    class: "input",
    list: "week-meals",
    placeholder: "Search meals…",
    value: meal ? meal.name : "",
  });
  const targets = el("datalist", { id: "week-meals" },
    state.meals.map((x) => el("option", { value: x.name })));

  let mode = state.pickMode;
  const seg = el("div", { class: "seg", role: "group" });
  [["rare", "Rare"], ["random", "Random"], ["favourite", "Favour"]].forEach(([m, label]) =>
    seg.append(el("button", {
      type: "button",
      "aria-pressed": mode === m ? "true" : "false",
      onclick: (e) => {
        mode = m;
        seg.querySelectorAll("button").forEach((b) =>
          b.setAttribute("aria-pressed", b === e.currentTarget ? "true" : "false"));
      },
    }, label))
  );

  const actions = meal && canConfirm(day)
    ? [el("div", { class: "btn-row" }, [
        el("button", {
          class: "btn btn-sage",
          onclick: async () => {
            if (!confirm(`Log ${meal.name} as eaten for ${fmtDay(day)}?`)) return;
            await confirmPlan(day);
          },
        }, "We had this ✓"),
      ])]
    : [];

  openSheet(el("div", {}, [
    el("div", { class: "sheet-title" }, fmtDay(day)),
    el("p", { class: "meta" },
      meal ? `Currently: ${meal.name}${canConfirm(day) ? " — ready to confirm" : ""}` : "Nothing planned yet"),
    section("Suggest one"),
    el("p", { class: "meta", style: "margin:0 0 10px" }, "Already-planned meals are left out."),
    el("div", { class: "btn-row", style: "gap:8px;flex-wrap:wrap" }, [
      seg,
      el("button", {
        class: "btn btn-ghost",
        onclick: async () => {
          const plannedIds = state.plans
            .filter((p) => p.planned_on !== day)
            .map((p) => p.meal.id);
          try {
            const data = await api(`/api/pick?mode=${mode}&count=1&exclude=${plannedIds.join(",")}`);
            input.value = data.picks[0].name;
          } catch (e) {
            toast(e.message === "no meals to choose from" ? "No candidates left" : e.message, true);
          }
        },
      }, "Suggest"),
    ]),
    ...actions,
    section("Pick a meal"),
    input,
    targets,
    el("div", { class: "btn-row" }, [
      el("button", {
        class: "btn btn-primary",
        onclick: async () => {
          const target = state.meals.find((x) => x.name === input.value.trim());
          if (!target) return toast("Pick a meal from the list", true);
          await api(`/api/plans/${day}`, { method: "PUT", body: { meal_id: target.id } });
          closeSheet();
          toast(`${target.name} planned for ${fmtDay(day)}`);
          await refresh();
        },
      }, "Plan this meal"),
      meal
        ? el("button", {
            class: "btn btn-danger-ghost",
            onclick: async () => {
              await api(`/api/plans/${day}`, { method: "DELETE" });
              closeSheet();
              toast(`Cleared ${fmtDay(day)}`);
              await refresh();
            },
          }, "Clear")
        : null,
    ]),
  ]));
}

async function confirmPlan(day) {
  const plan = state.plans.find((p) => p.planned_on === day);
  try {
    await api(`/api/plans/${day}/confirm`, { method: "POST" });
    toast(`Confirmed — ${plan.meal.name} logged`);
    await refresh();
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------ meta --------------------
function metaHtml(m) {
  const bits = [];
  bits.push(`<b>${m.count ? `${m.count}×</b> logged` : "never</b> had"}`);
  if (m.last_served) bits.push(`last ${esc(relDate(m.last_served))}`);
  return bits.join(" · ");
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function relDate(iso) {
  const then = new Date(iso + "T00:00:00");
  const days = Math.round((Date.now() - then.getTime()) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 30) return `${Math.round(days / 7)}w ago`;
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  return `${Math.round(days / 365)}y ago`;
}

// ------------------------------------------------ refresh -----------------
async function refresh(keepTab = true) {
  if (!state.weekStart) state.weekStart = startOfWeek();
  const weekEnd = isoAdd(state.weekStart, 6);
  const [meals, review, dups, recent, plans] = await Promise.all([
    api("/api/meals?filter=all"),
    api("/api/meals?filter=flagged"),
    api("/api/meals?filter=duplicates"),
    api("/api/history/recent?limit=20"),
    api(`/api/plans?start=${state.weekStart}&end=${weekEnd}`),
  ]);
  state.meals = meals.meals.filter((m) => !m.flagged);
  state.review = review.meals;
  state.duplicates = dups.meals;
  state.recent = recent.entries;
  state.plans = plans.plans;

  if (state.currentPick && !state.meals.some((m) => m.id === state.currentPick.id))
    state.currentPick = null;

  if (keepTab) render();
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
document.querySelectorAll(".tabbar .tab").forEach((t) =>
  t.addEventListener("click", () => setTab(t.dataset.tab))
);

(async () => {
  try {
    await refresh();
  } catch (e) {
    toast("Couldn't reach the server", true, 4000);
  }
})();