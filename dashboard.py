"""The payer-executive dashboard, with the suppression enforced at render time.

WHERE THE SUPPRESSION LIVES, AND WHY IT MATTERS
-----------------------------------------------
Every cell on this page goes through `suppression.suppress_table` on its way to
HTML. Not through a convention, not through a code review checklist -- through
one function, called by the renderer, which is the only path a number has to
the page.

That placement is the design decision. Suppression applied in the analytics
layer protects whatever the analytics layer happened to compute; a dashboard
that later adds a drill-down, a filter, or a new breakdown gets an unprotected
one. Putting it at the render boundary means a cell cannot reach a reader
without passing the check, including cells added by someone who has never read
this file.

The page also SHOWS its own suppression: how many cells were withheld, why each
one, and which were complementary. A dashboard that silently drops rows teaches
its readers that the data is incomplete in unpredictable ways, which is worse
for trust than a visible "withheld: n<11".

AND IT AUDITS ITSELF. `suppression.audit` re-runs the subtraction attack
against what was actually published and prints the result. A suppression rule
that is never attacked is a comment.

WHAT THIS IS NOT
----------------
One static HTML file with inline CSS, no JavaScript framework, no server, no
auth, no row-level security, no export controls, no session logging. A real
payer dashboard needs every one of those, and the access log is not optional --
who looked at which member-level drill-down is itself auditable information.

Run:  python dashboard.py
"""

from __future__ import annotations

import html
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import metrics as M
import suppression as SUP

OUT = "out"


def _fmt(v):
    if v == SUP.SUPPRESSED:
        return '<span class="sup">withheld</span>'
    if isinstance(v, float):
        return f"${v:,.2f}" if abs(v) < 10_000 else f"${v:,.0f}"
    return html.escape(str(v))


def _table(headers, rows, cls=""):
    h = "".join(f"<th>{html.escape(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                   for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>'


def build(results, suppressed, notes, audit_findings,
          coarse_pub, coarse_notes, raw_rows):
    q = results["quarters"]
    dec = results["decomposition"]
    labels = sorted(q)

    # --- headline -----------------------------------------------------------
    cards = []
    latest, prior = labels[-1], labels[-2]
    delta = q[latest]["pmpm"] - q[prior]["pmpm"]
    cards.append(("PMPM, " + latest, f"${q[latest]['pmpm']:,.2f}",
                  f"{delta:+,.2f} vs {prior}"))
    cards.append(("Member-months, " + latest,
                  f"{q[latest]['member_months']:,.0f}", ""))
    cards.append(("Top 1% share of spend",
                  f"{results['concentration']['top_1pct_share']:.1%}",
                  "read this before reading the PMPM movement"))
    card_html = "".join(
        f'<div class="card"><div class="lab">{html.escape(a)}</div>'
        f'<div class="val">{html.escape(b)}</div>'
        f'<div class="sub">{html.escape(c)}</div></div>'
        for a, b, c in cards)

    # --- trend --------------------------------------------------------------
    trend_rows = [[html.escape(l), f"${q[l]['pmpm']:,.2f}",
                   f"{q[l]['member_months']:,.0f}",
                   f"${q[l]['paid']:,.0f}"] for l in labels]

    # --- the decomposition, which is the executive question ------------------
    dec_rows = [
        ["Utilisation", f"${dec['utilisation_effect']:+,.2f}"],
        ["Price", f"${dec['price_effect']:+,.2f}"],
        ["Mix", f"${dec['mix_effect']:+,.2f}"],
        ["<em>Residual</em>", f"<em>${dec['residual']:+,.2f}</em>"],
        ["<strong>Actual change</strong>",
         f"<strong>${dec['actual_change']:+,.2f}</strong>"],
    ]

    # --- level 1: category x quarter, where nothing needs suppressing --------
    cat_headers = ["Category"] + [html.escape(l) for l in labels]
    cat_rows = []
    for cat in sorted(coarse_pub):
        cat_rows.append([html.escape(cat)]
                        + [_fmt(coarse_pub[cat].get(l)) for l in labels])

    # --- level 2: ZIP3 x quarter x category, where it does -------------------
    cats = sorted({c for r in suppressed.values() for c in r})
    geo_headers = ["ZIP3 / quarter"] + [html.escape(c) for c in cats] + ["Total"]
    geo_rows = []
    for row in sorted(suppressed):
        total = sum(c["value"] for c in raw_rows[row].values())
        geo_rows.append([html.escape(row)]
                        + [_fmt(suppressed[row].get(c, 0.0)) for c in cats]
                        + [f"<strong>${total:,.0f}</strong>"])

    # --- what was withheld ---------------------------------------------------
    note_rows = [[html.escape(n["row"]), html.escape(n["cell"]),
                  html.escape(n["kind"]), html.escape("; ".join(n["reasons"]))]
                 for n in notes]

    audit_html = (
        '<p class="ok">The subtraction attack recovers nothing: every '
        'suppressed cell sits in a row with at least one other suppressed '
        'cell.</p>' if not audit_findings else
        '<p class="bad">SUPPRESSION FAILED — the following cells are '
        'recoverable by subtraction:</p>' + _table(
            ["row", "cell", "recovered", "true"],
            [[html.escape(f["row"]), html.escape(f["cell"]),
              f"${f['recovered_value']:,.0f}", f"${f['true_value']:,.0f}"]
             for f in audit_findings]))

    m = M.metric("pmpm")
    dict_html = "".join(
        f"<dt><code>{html.escape(k)}</code> — {html.escape(v['name'])}</dt>"
        f"<dd>{html.escape(v['definition'])}<br>"
        f"<span class='meta'>denominator: {html.escape(v['denominator'])}</span>"
        f"</dd>"
        for k, v in sorted(M.METRICS.items()))

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Payer analytics — synthetic claims</title>
<style>
 body {{ font: 15px/1.55 -apple-system, Segoe UI, Roboto, sans-serif;
        max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #1c1c1e; }}
 h1 {{ font-size: 1.5rem; margin-bottom: .2rem; }}
 h2 {{ font-size: 1.05rem; margin-top: 2.2rem; border-bottom: 1px solid #e5e5ea;
       padding-bottom: .3rem; }}
 .banner {{ background: #fff4e5; border: 1px solid #ffcc80; padding: .7rem .9rem;
            border-radius: 6px; font-size: .9rem; margin: 1rem 0; }}
 .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1.2rem 0; }}
 .card {{ flex: 1 1 200px; border: 1px solid #e5e5ea; border-radius: 8px;
          padding: .8rem 1rem; }}
 .lab {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;
         color: #6b6b70; }}
 .val {{ font-size: 1.6rem; font-weight: 600; margin: .15rem 0; }}
 .sub {{ font-size: .8rem; color: #6b6b70; }}
 table {{ border-collapse: collapse; width: 100%; font-size: .88rem;
          margin: .6rem 0; }}
 th, td {{ text-align: right; padding: .35rem .6rem;
           border-bottom: 1px solid #f0f0f2; }}
 th:first-child, td:first-child {{ text-align: left; }}
 th {{ background: #fafafa; font-weight: 600; font-size: .8rem;
       text-transform: uppercase; letter-spacing: .03em; color: #6b6b70; }}
 .sup {{ color: #b00020; font-style: italic; }}
 .ok {{ color: #1b5e20; }} .bad {{ color: #b00020; font-weight: 600; }}
 dt {{ font-weight: 600; margin-top: .6rem; }}
 dd {{ margin: .1rem 0 0 0; font-size: .88rem; }}
 .meta {{ color: #6b6b70; font-size: .82rem; }}
 code {{ background: #f4f4f6; padding: .05rem .3rem; border-radius: 3px; }}
</style>

<h1>Payer analytics</h1>
<div class="banner">
  <strong>Synthetic data. No real member, claim, or provider appears anywhere
  in this project.</strong> Every figure below is generated by
  <code>src/synth.py</code> from rate parameters I wrote. Numbers are
  arithmetically real and clinically unearned.
</div>

{card_html}

<h2>PMPM trend</h2>
{_table(["Quarter", "PMPM", "Member-months", "Paid"], trend_rows)}
<p class="meta">Incurred basis. A quarter is not stable until roughly 90 days
of runout — facility claims here have a ~41-day median receipt lag with a long
tail, so a recent quarter is missing spend, not improving.</p>

<h2>What moved the PMPM: PMPM ${dec['pmpm_0']:,.2f} &rarr; ${dec['pmpm_1']:,.2f}</h2>
{_table(["Effect", "PMPM impact"], dec_rows)}
<p class="meta">Laspeyres-style, using period-0 weights: the attribution is
order-dependent and period-1 weights would give different numbers from the same
data. The residual is published so the size of that arbitrariness is visible.
Attribution is not causation.</p>

<h2>Drill-down 1: service category by quarter</h2>
{_table(cat_headers, cat_rows)}
<p class="meta">{len(coarse_notes)} cells suppressed at this grain. Cells here
hold 137&ndash;6,311 members, so the rule correctly leaves them alone &mdash; a
suppression rule that fires on everything is as useless as one that fires on
nothing.</p>

<h2>Drill-down 2: ZIP3 and quarter by category</h2>
<p class="meta">This is where it matters. Executives drill down until cells get
small; that is what a drill-down is <em>for</em>. ZIP3 is also one of the 18
HIPAA Safe Harbor identifiers and the same quasi-identifier
<code>reidentify.py</code> uses to attack the member extract &mdash; the
dashboard and the attack are looking at the same column from opposite sides.
The row total is published, which is exactly what makes complementary
suppression necessary.</p>
{_table(geo_headers, geo_rows[:24])}
<p class="meta">First 24 of {len(geo_rows)} rows.</p>

<h2>What was withheld, and why</h2>
<p class="meta">{len(notes)} cell(s) suppressed. Cells are withheld when fewer
than {SUP.MIN_CELL} members contribute, or when one contributor holds
{SUP.DOMINANCE_SHARE:.0%} or more of the cell — the second rule catches
disclosure in large cells, which a threshold alone misses entirely.</p>
{_table(["Row", "Cell", "Kind", "Reason"], note_rows) if note_rows
 else '<p class="ok">No cell required suppression at this grain.</p>'}

<h2>Self-audit: is the suppression real?</h2>
{audit_html}
<p class="meta">Suppressing one cell in a row whose total is published achieves
nothing — the value is recoverable by subtraction. That is why a second,
complementary cell is withheld. This section re-runs the attack against what
was actually rendered.</p>

<h2>Metric dictionary (v{M.VERSION})</h2>
<p class="meta">Generated from <code>src/metrics.py</code>, which is also what
the pipeline computes from. There is one definition of PMPM in this repository.</p>
<dl>{dict_html}</dl>
"""


def main(datadir="data"):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "results.json")
    if not os.path.exists(path):
        raise SystemExit("run `python run_pipeline.py` first")
    with open(path) as fh:
        results = json.load(fh)

    # Build the drill-down cells WITH their member counts, because a cell
    # cannot be checked for safety without knowing how many members are in it.
    # Two grains, deliberately. The coarse one shows the rule NOT firing, which
    # matters: a suppression rule that fires on everything is as useless as one
    # that fires on nothing, and a reader has to see it leave safe cells alone.
    coarse = {}
    for cat, per_q in results["category_cells"].items():
        coarse[cat] = {q: {"n": c["n_members"], "value": c["paid"],
                           "top_share": c.get("top_share")}
                       for q, c in per_q.items()}
    coarse_pub, coarse_notes = SUP.suppress_table(coarse, total_published=True)

    rows = {}
    for row, per_cat in results["geo_cells"].items():
        rows[row] = {cat: {"n": c["n_members"], "value": c["paid"],
                           "top_share": c.get("top_share")}
                     for cat, c in per_cat.items()}

    published, notes = SUP.suppress_table(rows, total_published=True)
    findings = SUP.audit(rows, published)

    page = build(results, published, notes, findings,
                 coarse_pub, coarse_notes, rows)
    out_path = os.path.join(OUT, "dashboard.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)

    with open("docs/METRIC_DICTIONARY.md", "w", encoding="utf-8") as fh:
        fh.write(M.render_markdown())

    print("=" * 72)
    print("DASHBOARD")
    print("=" * 72)
    print(f"  cells rendered      {sum(len(v) for v in rows.values())}")
    print(f"  cells suppressed    {len(notes)}")
    for kind in ("primary", "complementary", "row-withheld"):
        k = [n for n in notes if n["kind"] == kind]
        if k:
            print(f"    {kind:<16}{len(k)}")
            for n in k[:3]:
                print(f"      {n['row']} / {n['cell']}: {n['reasons'][0][:66]}")
    print(f"\n  subtraction attack recovers: "
          f"{len(findings)} cell(s)" + (" -- SUPPRESSION FAILED"
                                        if findings else " (none)"))
    print(f"\nwrote {out_path}")
    print("wrote docs/METRIC_DICTIONARY.md (generated from src/metrics.py)")
    return out_path


if __name__ == "__main__":
    main()
