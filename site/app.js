/* Renders feed.json. No framework, no build step: the page must stay readable
   by anyone auditing what the agent claimed and when. */

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const money = (v) => "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v) => (v < 0 ? "−" : "+") + money(Math.abs(v));
const pct = (v) => (v < 0 ? "−" : "+") + (Math.abs(v) * 100).toFixed(2) + "%";
const cents = (p) => Math.round(p * 100) + "¢";
const nfmt = (n) => n.toLocaleString("en-US");

/** 18.4M / 940k / 812 — token counts get big; the exact digit never matters. */
function tokens(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e8 ? 0 : 1) + "M";
  if (n >= 1e3) return Math.round(n / 1e3) + "k";
  return String(n);
}

const plural = (n, one, many = one + "s") => `${nfmt(n)} ${n === 1 ? one : many}`;

const UTC = { timeZone: "UTC", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false };
const stamp = (iso) => new Date(iso).toLocaleString("en-US", UTC) + "Z";

function ago(iso) {
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return mins + " min ago";
  const hrs = Math.round(mins / 60);
  if (hrs < 36) return hrs + "h ago";
  return Math.round(hrs / 24) + "d ago";
}

/* --- the disagreement scale ------------------------------------------------
   Two ticks on a 0–100¢ rule: what the market charges, what the agent believes.
   The span between them is the edge. Everything else on this page is context. */
function scale(t, { axis = false } = {}) {
  const mkt = t.p_market * 100;
  const mdl = t.p_model * 100;
  const wrap = el("div", "scale pre");
  wrap.dataset.dir = mdl >= mkt ? "up" : "down";
  wrap.style.setProperty("--market", mkt.toFixed(2));
  wrap.style.setProperty("--model", mdl.toFixed(2));
  wrap.style.setProperty("--lo", Math.min(mkt, mdl).toFixed(2));
  wrap.style.setProperty("--hi", Math.max(mkt, mdl).toFixed(2));
  wrap.setAttribute("role", "img");
  wrap.setAttribute("aria-label",
    `Market ${cents(t.p_market)}, model ${cents(t.p_model)}, edge ${cents(Math.abs(t.edge))} ` +
    `on the ${t.side.toUpperCase()} side.`);

  const rule = el("div", "scale-rule");
  for (const [k, v] of [["market", mkt], ["model", mdl]]) {
    const tick = el("div", `tick tick-${k}`);
    const label = el("span", "tick-label", `${k} ${cents(v / 100)}`);
    // A label near the right edge would run off the rule; hang it the other way.
    if (v > 82) label.dataset.anchor = "end";
    tick.append(label);
    rule.append(tick);
  }
  rule.append(el("div", "scale-span"));
  wrap.append(rule);

  if (axis) {
    const ax = el("div", "scale-axis");
    for (const n of [0, 25, 50, 75, 100]) ax.append(el("span", null, n + "¢"));
    wrap.append(ax);
  }

  const read = el("div", "scale-read");
  const edge = el("div", "scale-edge", cents(Math.abs(t.edge)));
  edge.append(el("small", null, "edge"));
  read.append(edge, el("span", "scale-side", `buy ${t.side}`));
  wrap.append(read);

  requestAnimationFrame(() => requestAnimationFrame(() => wrap.classList.remove("pre")));
  return wrap;
}

/* --- hero ------------------------------------------------------------------- */
function renderHero(feed) {
  const slot = $("#hero-slot");
  slot.textContent = "";
  const top = feed.theses.find((t) => t.edge !== null);
  if (!top) {
    slot.append(el("p", "empty",
      "No open claim clears the edge bar right now. The agent is holding cash — " +
      "which is what it is supposed to do when the market is priced correctly."));
    return;
  }

  const meta = el("p", "hero-meta");
  meta.append(el("span", "chip", top.status === "held" ? "position open" : "no position yet"));
  meta.append(el("span", null, top.platform));
  meta.append(el("span", null, stamp(top.ts)));
  slot.append(meta, el("h2", "hero-q", top.question));

  const s = scale(top, { axis: true });
  s.classList.add("hero-scale");
  slot.append(s);

  const claim = el("div", "claim");
  for (const [k, label, text] of [
    ["why", "Why", top.why],
    ["wrong", "Wrong if", top.invalidation],
  ]) {
    if (!text) continue;
    const row = el("div", "claim-row");
    row.dataset.k = k;
    row.append(el("div", "claim-k", label), el("p", "claim-v", text));
    claim.append(row);
  }
  slot.append(claim);
}

/* --- equity ----------------------------------------------------------------- */
function renderCurve(feed) {
  const slot = $("#curve-slot");
  const curve = feed.performance.equity_curve;
  slot.textContent = "";
  if (curve.length < 2) {
    slot.append(el("p", "empty", "The equity curve needs two cycles. The first is on the clock."));
    return;
  }

  const W = 600, H = 132, PAD = 3;
  const vals = curve.map((c) => c[1]).concat([feed.performance.bankroll]);
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const x = (i) => (i / (curve.length - 1)) * W;
  const y = (v) => PAD + (1 - (v - lo) / span) * (H - 2 * PAD);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "curve");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label",
    `Account value from ${money(curve[0][1])} to ${money(curve[curve.length - 1][1])} over ${curve.length} cycles.`);

  const ns = "http://www.w3.org/2000/svg";
  const base = document.createElementNS(ns, "line");
  base.setAttribute("class", "curve-base");
  base.setAttribute("x1", 0); base.setAttribute("x2", W);
  base.setAttribute("y1", y(feed.performance.bankroll));
  base.setAttribute("y2", y(feed.performance.bankroll));
  svg.append(base);

  const line = document.createElementNS(ns, "polyline");
  const down = curve[curve.length - 1][1] < feed.performance.bankroll;
  line.setAttribute("class", "curve-line" + (down ? " down" : ""));
  line.setAttribute("points", curve.map((c, i) => `${x(i)},${y(c[1])}`).join(" "));
  svg.append(line);
  slot.append(svg);

  const cap = el("div", "curve-caption");
  cap.append(el("span", null, curve[0][0].slice(0, 10)),
             el("span", null, `${curve.length} cycles · dashed line = $${nfmt(feed.performance.bankroll)} start`),
             el("span", null, curve[curve.length - 1][0].slice(0, 10)));
  slot.append(cap);
}

function renderStats(feed) {
  const p = feed.performance;
  const rows = [
    ["Realized P&L", signed(p.realized_pnl), p.realized_pnl < 0],
    ["Settled", `${p.settled_trades}<small> trades</small>`, false],
    ["Win rate", p.settled_trades ? (p.win_rate * 100).toFixed(0) + "%" : "—", false],
    ["Brier score", p.brier === null ? "—" : p.brier.toFixed(3), false],
    // Drawdown is a fact, not a loss — every strategy has one. Red is reserved
    // for money actually given back.
    ["Max drawdown", (p.max_drawdown * 100).toFixed(2) + "%", false],
    ["Cycles run", nfmt(p.cycles), false],
  ];
  const dl = $("#stats-slot");
  dl.textContent = "";
  for (const [k, v, bad] of rows) {
    const box = el("div", "stat");
    const dd = el("dd", bad ? "down" : null);
    dd.innerHTML = v;
    box.append(el("dt", null, k), dd);
    dl.append(box);
  }
}

/* --- open claims ------------------------------------------------------------ */
function renderTheses(feed) {
  const slot = $("#theses-slot");
  slot.textContent = "";
  const list = feed.theses.filter((t) => t.edge !== null);
  $('[data-f="theses.count"]').textContent = list.length || "";

  if (!list.length) {
    slot.classList.remove("cards");
    slot.append(el("p", "empty", "No open claims. Every market the agent scanned this cycle was priced inside its edge bar."));
    return;
  }

  for (const t of list) {
    const card = el("article", "card");
    const head = el("div", "card-head");
    const chip = el("span", "chip", t.status === "held" ? "held" : "pending");
    chip.dataset.s = t.status;
    head.append(chip, el("span", null, t.platform), el("span", "card-ts", stamp(t.ts)));

    const s = scale(t);
    s.classList.add("card-scale");
    card.append(head, el("h3", "card-q", t.question), s);

    if (t.why) card.append(el("p", "card-why", t.why));
    if (t.invalidation) {
      const w = el("p", "card-wrong");
      w.append(el("b", null, "Wrong if"), document.createTextNode(t.invalidation));
      card.append(w);
    }
    slot.append(card);
  }
}

/* --- positions & settlements ------------------------------------------------- */
function renderRows(slotSel, countSel, items, empty, row) {
  const slot = $(slotSel);
  slot.textContent = "";
  $(countSel).textContent = items.length || "";
  if (!items.length) { slot.append(el("p", "empty", empty)); return; }
  const rows = el("div", "rows");
  for (const it of items) rows.append(row(it));
  slot.append(rows);
}

function renderPositions(feed) {
  renderRows("#positions-slot", '[data-f="positions.count"]', feed.positions,
    "No open positions. The agent is flat.", (p) => {
      const r = el("div", "row");
      r.append(el("div", "row-q", p.question),
               el("div", "row-n", money(p.cost_basis)),
               el("div", "row-sub", `${p.side} · ${nfmt(p.qty)} @ ${cents(p.avg_cost)}`),
               el("div", "row-side", p.platform));
      return r;
    });
}

function renderTrack(feed) {
  renderRows("#track-slot", '[data-f="track.count"]', feed.track_record,
    "Nothing has settled yet. The track record starts at the first resolution.", (s) => {
      const r = el("div", "row");
      const n = el("div", "row-n" + (s.pnl < 0 ? " down" : ""), signed(s.pnl));
      r.append(el("div", "row-q", s.question), n,
               el("div", "row-sub", `resolved ${s.outcome} · ${s.ts.slice(0, 10)}`),
               el("div", "row-side", s.platform));
      return r;
    });
}

/* --- self-improvement -------------------------------------------------------- */
function renderRsi(feed) {
  const slot = $("#rsi-slot");
  slot.textContent = "";
  const { active_generation: g, meta_cycles: cycles } = feed.rsi;

  if (!g) {
    slot.append(el("p", "empty",
      "No generation has cleared the promotion gate yet, so the operator's config is still in force. " +
      "That is the safe default: a change ships only on evidence."));
  } else {
    const box = el("div", "gen");
    const head = el("div", "gen-head");
    head.append(el("span", "gen-id", "Generation " + g.id),
                el("span", null, g.operator + " operator"),
                el("span", null, "proposed by " + g.proposer),
                el("span", null, "promoted " + g.created.slice(0, 10)));
    box.append(head);
    if (g.rationale) box.append(el("p", "gen-why", g.rationale));
    const params = el("div", "params");
    for (const [k, v] of Object.entries(g.params)) {
      const chip = el("span", "param");
      chip.append(document.createTextNode(k.split(".").pop() + " "), el("b", null, String(v)));
      params.append(chip);
    }
    box.append(params);
    slot.append(box);
  }

  if (!cycles.length) {
    slot.append(el("p", "empty", "The evolution loop has not run a meta-cycle yet."));
    return;
  }
  for (const c of cycles) {
    const row = el("div", "cycle");
    const body = el("div", "cycle-body");
    const passed = c.candidates.filter((x) => x.verdict === "pass").length;

    body.append(el("b", null, c.operator + " operator"), document.createTextNode(
      ` · ${plural(c.replay_rows, "replay row")} · ${plural(c.candidates.length, "candidate")}, `));
    body.append(el("span", passed ? "verdict-pass" : null, `${passed} cleared the gate`));
    if (c.rollback) body.append(document.createTextNode(" · rolled back: " + c.rollback));
    if (c.reason) body.append(el("div", null, c.reason));
    row.append(el("div", "cycle-ts", stamp(c.ts)), body);
    slot.append(row);
  }
}

/* --- compute ----------------------------------------------------------------- */
function renderCompute(feed) {
  const slot = $("#compute-slot");
  const c = feed.compute;
  slot.textContent = "";

  const grid = el("div", "tokens");
  const boxes = [
    ["Tokens in", tokens(c.total.prompt_tokens)],
    ["Tokens out", tokens(c.total.completion_tokens)],
    ["Model calls", nfmt(c.total.calls)],
    ["Forecasts on record", nfmt(c.forecasts_recorded)],
  ];
  for (const [k, v] of boxes) {
    const b = el("div", "stat");
    b.append(el("dt", null, k), el("dd", null, v));
    grid.append(b);
  }
  slot.append(grid);

  if (!c.ledger_started) {
    slot.append(el("p", "empty",
      "Token accounting starts with the next cycle. The forecasts on record predate the ledger, " +
      "so their cost is not reconstructable — and this page does not guess."));
    return;
  }

  const max = Math.max(...c.by_node.map((n) => n.total_tokens), 1);
  const bars = el("div", "bars");
  for (const n of c.by_node) {
    const row = el("div", "bar-row");
    const track = el("div", "bar-track");
    const fill = el("div", "bar-fill");
    fill.style.width = (n.total_tokens / max) * 100 + "%";
    track.append(fill);
    row.append(el("div", "bar-k", n.node), track,
               el("div", "bar-v", `${tokens(n.total_tokens)} · ${plural(n.calls, "call")}`));
    bars.append(row);
  }
  slot.append(bars);

  const flat = c.total.calls_without_usage;
  const note = `Counted since ${c.ledger_started.slice(0, 10)}.` + (flat
    ? ` ${plural(flat, "call")} went to a flat-rate subscription endpoint that reports no token counts; they are not in the totals above.`
    : "");
  slot.append(el("p", "empty", note));
}

/* --- chrome ------------------------------------------------------------------- */
function renderChrome(feed) {
  const p = feed.performance;
  const set = (k, v) => { const n = $(`[data-f="${k}"]`); if (n) n.textContent = v; };

  set("agent.mode_chip", `${feed.agent.mode} · ${feed.agent.focus}`);
  set("performance.as_of_rel", p.as_of ? "cycle " + ago(p.as_of) : "no cycles yet");
  set("performance.account_value", money(p.account_value));
  set("generated_at", stamp(feed.generated_at));

  // The model gets a link when its weights are public — which, for an agent
  // arguing that the crowd is wrong, is the difference between a claim and a
  // checkable one.
  const model = $('[data-f="agent.forecaster"]');
  model.textContent = "";
  const { label, url } = feed.agent.forecaster;
  if (url) {
    const a = el("a", "strip-model", label);
    a.href = url;
    a.rel = "noopener";
    model.append(a);
  } else {
    model.textContent = label;
  }

  const delta = $('[data-f="performance.return_line"]');
  delta.textContent = "";
  const v = el("span", p.return_pct < 0 ? "down" : null, pct(p.return_pct));
  delta.append(v, document.createTextNode(` since ${money(p.bankroll)} start`));

  const nav = $("#links-slot");
  nav.textContent = "";
  const links = [
    ["Source on GitHub", feed.links.github],
    ["Models & data on Hugging Face", feed.links.huggingface],
    ["Claims, timestamped on X", feed.links.x],
  ];
  for (const [label, href] of links) {
    if (!href) continue;
    const a = el("a", null, label);
    a.href = href;
    a.rel = "noopener";
    nav.append(a);
  }
}

async function main() {
  let feed;
  try {
    const resp = await fetch("feed.json", { cache: "no-store" });
    if (!resp.ok) throw new Error(resp.status);
    feed = await resp.json();
  } catch (err) {
    $("#hero-slot").textContent = "";
    $("#hero-slot").append(el("p", "empty",
      "The feed did not load. The agent keeps trading either way — reload, or read feed.json directly."));
    return;
  }
  renderChrome(feed);
  renderHero(feed);
  renderCurve(feed);
  renderStats(feed);
  renderTheses(feed);
  renderPositions(feed);
  renderTrack(feed);
  renderRsi(feed);
  renderCompute(feed);
}

main();
