"""Modern-dashboard HTML/ECharts charts for the Echo evaluation report.

Design language: Linear / Vercel-style product dashboard — white cards, soft
shadows, rounded bars, smooth lines with gradient confidence band, hover
tooltips. Numbers are embedded inline (file:// blocks fetch) and come from the
committed stats.json + satisfaction_curve_ci.json, so the page is reproducible.

Run:  python generate_html.py   ->  charts.html  (open in a browser)
"""
from __future__ import annotations
import json, pathlib

FIG = pathlib.Path(__file__).resolve().parent          # DevPlan/experiment-figures
S = json.loads((FIG / "stats.json").read_text())
CI = json.loads((FIG / "satisfaction_curve_ci.json").read_text())

pm = S["personamem"]; pe = S["prefeval"]; m1 = S["metric1_satisfaction"]
m2 = S["metric2_error_propagation"]; det = m2["deterministic"]
m3 = S["metric3_overhead"]; mm = S["micrometrics"]

DATA = {
    "personamem": {
        "cats": ["No memory", "Full history", "Echo M5"],
        "subs": ["cold", "naive RAG", "retrieval"],
        "mean": [pm["accuracy_mean"][c] * 100 for c in ("no_mem", "full_hist", "echo_m5")],
        "sd": [pm["accuracy_sd"][c] * 100 for c in ("no_mem", "full_hist", "echo_m5")],
        "inj": [pm["avg_inject_chars"][c] for c in ("no_mem", "full_hist", "echo_m5")],
    },
    "prefeval": {
        "cats": ["No memory", "Echo M5", "Oracle"],
        "subs": ["", "retrieval", "pref. given"],
        "mean": [pe["adherence_mean"][c] * 100 for c in ("no_pref", "echo_m5", "oracle")],
        "sd": [pe["adherence_sd"][c] * 100 for c in ("no_pref", "echo_m5", "oracle")],
    },
    "sat": {
        "turns": CI["echo"]["turns"],
        "A": CI["A"]["mean"], "B": CI["B"]["mean"], "echo": CI["echo"]["mean"],
        "lo": CI["echo"]["lo"], "hi": CI["echo"]["hi"],
        "dA": m1["echo_vs_A"]["cliffs_delta"], "dB": m1["echo_vs_B"]["cliffs_delta"],
        "n": m1["echo_vs_A"]["n_pairs"],
    },
    "errk": {
        "n3": {"echo": det["n_bad_3"]["echo_caught_mean"], "b": det["n_bad_3"]["baseline_b_caught_mean"], "n": 3},
        "n10": {"echo": det["n_bad_10"]["echo_caught_mean"], "b": det["n_bad_10"]["baseline_b_caught_mean"], "n": 10},
    },
    "conf": {
        "good": [r["echo_mean_conf_good"] for r in det["n_bad_3"]["per_seed"] + det["n_bad_10"]["per_seed"]],
        "bad": [r["echo_mean_conf_bad"] for r in det["n_bad_3"]["per_seed"] + det["n_bad_10"]["per_seed"]],
    },
    "over": {
        "cats": ["Baseline A", "Baseline B", "Echo"],
        "agent": [m3["mean_agent_tokens"][c] for c in ("A", "B", "echo")],
        "lb": [m3["mean_layerB_tokens"][c] for c in ("A", "B", "echo")],
        "lc": [m3["mean_layerC_tokens"][c] for c in ("A", "B", "echo")],
        "fairpct": m3["fair_agent_tokens_A_vs_echo"]["echo_vs_A_pct"],
        "agent_pt": m3["steady_state"]["fair_agent_tokens_per_turn"],
        "lb_pt": m3["steady_state"]["layerB_tokens_per_turn"],
        "ss_pct": m3["steady_state"]["steady_overhead_pct_per_turn"],
        "fires": m3["layerC_incident"]["total_firings"],
        "turns": m3["layerC_incident"]["total_echo_turns"],
        "nojudge": m3["layerC_incident"]["runs_with_no_judge"],
        "judge": m3["layerC_incident"]["runs_with_judge"],
    },
    "micro": {
        "m4": [[p["true_usefulness"], p["confidence"]] for p in mm["M4_confidence"]["pairs"]],
        "rho": mm["M4_confidence"]["spearman_rho"],
        "m1": {"echo": [mm["M1_trigger"]["echo_precision"], mm["M1_trigger"]["echo_recall"]],
               "herm": [mm["M1_trigger"]["hermes_precision"], mm["M1_trigger"]["hermes_recall"]]},
        "m3": [mm["M3_drift"]["precision"], mm["M3_drift"]["recall"], mm["M3_drift"]["f1"]],
        "m3c": f"TP {mm['M3_drift']['tp']} · FP {mm['M3_drift']['fp']} · FN {mm['M3_drift']['fn']} · TN {mm['M3_drift']['tn']} · {mm['M3_drift']['excluded_warmup']} warm-up excl.",
        "m5": [mm["M5_retrieval"]["recall_no_weights"], mm["M5_retrieval"]["recall_with_confidence_weights"]],
    },
}

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Echo — evaluation figures (HTML / ECharts)</title>
<script src="echarts.min.js"></script>
<style>
  :root{ --ink:#19222C; --ink2:#5B6B7B; --ink3:#8492A0; --line:#EEF1F4;
         --echo:#0FA295; --echoDeep:#0A6E66; --A:#A9B4BF; --B:#E1A458;
         --full:#7E9BD0; --good:#46A56B; --bad:#DD6B4B; --layerC:#D9748B; --agent:#CCD3DA; }
  *{box-sizing:border-box}
  body{margin:0;background:#F4F6F8;color:var(--ink);
       font-family:-apple-system,'SF Pro Display','SF Pro Text','Inter','Helvetica Neue',Arial,sans-serif;
       -webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
  .wrap{max-width:1140px;margin:0 auto;padding:30px 24px 60px}
  .page-h{font-size:13px;letter-spacing:.5px;text-transform:uppercase;color:var(--ink3);font-weight:600;margin:0 0 18px}
  .card{background:#fff;border-radius:18px;box-shadow:0 1px 2px rgba(16,24,40,.05),0 4px 16px rgba(16,24,40,.05);
        padding:22px 24px;margin-bottom:22px}
  .card h3{margin:0;font-size:17.5px;font-weight:600;letter-spacing:.1px}
  .card .sub{margin:5px 0 0;font-size:12.5px;color:var(--ink3)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:22px}
  .chart{width:100%}
  .badge{display:inline-flex;gap:6px;align-items:center;background:#F1F9F8;color:var(--echoDeep);
         border:1px solid rgba(15,162,149,.35);border-radius:999px;padding:5px 12px;font-size:12px;font-weight:600;margin-top:6px}
  .note{font-size:12px;color:var(--ink2);margin-top:10px;line-height:1.55}
  .micro-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .mtitle{font-size:13.5px;font-weight:600;color:var(--ink);margin:2px 0 0}
</style></head>
<body><div class="wrap">
  <p class="page-h">Echo · evaluation figures · HTML / ECharts</p>

  <div class="grid2">
    <div class="card"><h3>Preference recall — PersonaMem</h3>
      <p class="sub">Multiple-choice probe accuracy · 3 seeds · n = 540 · whiskers ±1 SD</p>
      <div id="c_pm" class="chart" style="height:330px"></div>
      <span class="badge">+17.8 pts vs cold · ⅓ the context</span></div>
    <div class="card"><h3>Preference adherence — PrefEval</h3>
      <p class="sub">Generation adheres to the stored preference · 3 seeds · n = 300</p>
      <div id="c_pe" class="chart" style="height:330px"></div>
      <span class="badge">retrieves 1-of-200 → 82% (oracle 90%)</span></div>
  </div>

  <div class="card"><h3>Proactive satisfaction across the conversation</h3>
    <p class="sub">Independent GLM-5.2 score (1–5) of each turn's first output · 15 personas × 3 seeds · band = 95% CI</p>
    <div id="c_sat" class="chart" style="height:400px"></div>
    <span class="badge">Echo vs baselines · Cliff's δ = 0.84 / 0.86 · p &lt; 10⁻⁷² · n = 450 paired</span></div>

  <div class="grid2">
    <div class="card"><h3>Error propagation — bad skills retired</h3>
      <p class="sub">Deterministic harness · 5 seeds · 15% signal noise · planted ground truth</p>
      <div id="c_err" class="chart" style="height:320px"></div>
      <span class="badge">Echo 3/3 &amp; 10/10 · 0 false positives · Baseline B 0</span></div>
    <div class="card"><h3>Why — final confidence separates them</h3>
      <p class="sub">Bad skills decay below c_retire = 0.10; good skills hold ≈ 0.85</p>
      <div id="c_conf" class="chart" style="height:320px"></div>
      <span class="badge">clean separation, no tuning</span></div>
  </div>

  <div class="card"><h3>System overhead — modest, on a cheap off-latency tier</h3>
    <p class="sub">Tokens per 10-turn run · Baseline A also revises (fair agent-token comparison)</p>
    <div id="c_over" class="chart" style="height:360px"></div>
    <p class="note">Fair agent-token Δ (Echo vs A) = <b>+__FAIRPCT__%</b> · steady-state add = Layer B only (<b>+__SSPCT__%</b>/turn) ·
      Layer C (judge) is rare: <b>__FIRES__ firings / __TURNS__ turns (≈1 per 35)</b>, __NOJUDGE__/__NRUN__ runs never fired it.</p></div>

  <div class="card"><h3>Per-module micro-metrics</h3>
    <p class="sub">Deterministic · planted ground truth · invariant to run scale</p>
    <div class="micro-grid">
      <div><p class="mtitle">M4 · confidence tracks usefulness</p><div id="c_m4" class="chart" style="height:230px"></div></div>
      <div><p class="mtitle">M1 · nomination ties the Hermes rule</p><div id="c_m1" class="chart" style="height:230px"></div></div>
      <div><p class="mtitle">M3 · drift detection (small n)</p><div id="c_m3" class="chart" style="height:230px"></div></div>
      <div><p class="mtitle">M5 · weighting uplift null on this case</p><div id="c_m5" class="chart" style="height:230px"></div></div>
    </div>
    <p class="note">M3: __M3C__ · M5 real value: see PersonaMem / PrefEval above.</p></div>

</div>
<script>
const D = __DATA__;
const ECHO="#0FA295", ECHO_DEEP="#0A6E66", A="#A9B4BF", B="#E1A458", FULL="#7E9BD0",
      GOOD="#46A56B", BAD="#DD6B4B", LAYERC="#D9748B", AGENT="#CCD3DA",
      INK="#19222C", INK2="#5B6B7B", INK3="#8492A0", LINE="#EEF1F4";
const FONT="-apple-system,'SF Pro Display','Inter','Helvetica Neue',Arial,sans-serif";
const base = {textStyle:{fontFamily:FONT}, animation:false};
function mk(id){ const e=echarts.init(document.getElementById(id),null,{renderer:'canvas',devicePixelRatio:2}); return e; }
function axX(data,extra){return Object.assign({type:'category',data:data,axisTick:{show:false},
  axisLine:{lineStyle:{color:'#DCE2E8'}},axisLabel:{color:'#3F4D5A',fontSize:13,fontWeight:500}},extra||{});}
function axY(extra){return Object.assign({type:'value',splitLine:{lineStyle:{color:LINE}},
  axisLine:{show:false},axisTick:{show:false},axisLabel:{color:INK3,fontSize:11}},extra||{});}
// error-bar custom renderer
function errItem(params,api){const xv=api.value(0);
  const lo=api.coord([xv,api.value(1)]), hi=api.coord([xv,api.value(2)]); const x=lo[0],cap=6;
  const st={stroke:INK2,lineWidth:1.5,fill:null};
  return {type:'group',children:[
    {type:'line',shape:{x1:x,y1:lo[1],x2:x,y2:hi[1]},style:st},
    {type:'line',shape:{x1:x-cap,y1:hi[1],x2:x+cap,y2:hi[1]},style:st},
    {type:'line',shape:{x1:x-cap,y1:lo[1],x2:x+cap,y2:lo[1]},style:st}]};}

// ---- PersonaMem / PrefEval bars ----
function bar(id,d,colors,opts){const e=mk(id);
  const err=d.cats.map((c,i)=>[i,d.mean[i]-d.sd[i],d.mean[i]+d.sd[i]]);
  const cats=d.cats.map((c,i)=>d.subs[i]?(c+'\n'+d.subs[i]):c);
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:opts.right||14,top:24,bottom:38,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'none'},valueFormatter:v=>v.toFixed(1)+'%'},
    xAxis:axX(cats,{axisLabel:{color:'#3F4D5A',fontSize:13,fontWeight:500,lineHeight:15,
       formatter:v=>v.split('\n').map((s,i)=>i?'{s|'+s+'}':'{b|'+s+'}').join('\n'),
       rich:{b:{fontWeight:600,fontSize:13,color:'#3F4D5A'},s:{fontSize:11,color:INK3,padding:[2,0,0,0]}}}}),
    yAxis:axY({max:opts.ymax,axisLabel:{color:INK3,fontSize:11,formatter:'{value}%'}}),
    series:[
      {type:'bar',barWidth:'50%',z:2,
       data:d.mean.map((v,i)=>({value:v,itemStyle:{color:colors[i],borderRadius:[7,7,0,0]}})),
       label:{show:true,position:'top',distance:8,formatter:p=>p.value.toFixed(opts.dp!=null?opts.dp:1)+'%',
              color:INK,fontSize:15,fontWeight:700}},
      {type:'custom',renderItem:errItem,data:err,z:6,silent:true}
    ]}));}
bar('c_pm',D.personamem,[A,FULL,ECHO],{ymax:80,dp:1});
bar('c_pe',D.prefeval,[A,ECHO,ECHO_DEEP],{ymax:100,dp:0});

// ---- Satisfaction line + CI band ----
(function(){const e=mk('c_sat'); const t=D.sat.turns;
  const band=t.map((x,i)=>D.sat.hi[i]-D.sat.lo[i]);
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:64,top:16,bottom:40,containLabel:true},
    tooltip:{trigger:'axis',valueFormatter:v=>v==null?'':(+v).toFixed(2)},
    legend:{show:true,top:0,right:0,orient:'vertical',icon:'roundRect',itemWidth:18,itemHeight:4,
            textStyle:{color:INK2,fontSize:12},data:['Echo','Baseline B','Baseline A']},
    xAxis:axX(t,{name:'interaction (turn)',nameLocation:'middle',nameGap:28,nameTextStyle:{color:INK3,fontSize:11},
                 boundaryGap:false,axisLabel:{color:INK3,fontSize:11}}),
    yAxis:axY({min:1,max:5,interval:1}),
    series:[
      {name:'lo',type:'line',data:D.sat.lo,stack:'ci',symbol:'none',lineStyle:{opacity:0},areaStyle:{opacity:0},silent:true,legendHoverLink:false,tooltip:{show:false}},
      {name:'ciband',type:'line',data:band,stack:'ci',symbol:'none',lineStyle:{opacity:0},
       areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'rgba(15,162,149,.20)'},{offset:1,color:'rgba(15,162,149,.05)'}])},
       silent:true,legendHoverLink:false,tooltip:{show:false}},
      {name:'Baseline A',type:'line',data:D.sat.A,smooth:false,symbol:'circle',symbolSize:6,
       lineStyle:{color:A,width:2,type:[2,5]},itemStyle:{color:A}},
      {name:'Baseline B',type:'line',data:D.sat.B,smooth:false,symbol:'circle',symbolSize:6,
       lineStyle:{color:B,width:2,type:[2,5]},itemStyle:{color:B}},
      {name:'Echo',type:'line',data:D.sat.echo,smooth:0.35,symbol:'circle',symbolSize:7,
       lineStyle:{color:ECHO,width:3.4},itemStyle:{color:ECHO},
       endLabel:{show:true,formatter:p=>p.value.toFixed(1),color:ECHO_DEEP,fontWeight:700,fontSize:13},
       emphasis:{focus:'series'}}
    ]}));})();

// ---- Error prop grouped bars ----
(function(){const e=mk('c_err');
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:14,top:30,bottom:30,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
    legend:{show:true,top:0,left:0,itemWidth:11,itemHeight:11,textStyle:{color:INK2,fontSize:12},data:['Echo','Baseline B (freq. decay)']},
    xAxis:axX(['3 planted bad','10 planted bad']),
    yAxis:axY({max:10}),
    series:[
      {name:'Echo',type:'bar',barWidth:'30%',itemStyle:{color:ECHO,borderRadius:[6,6,0,0]},
       data:[D.errk.n3.echo,D.errk.n10.echo],
       label:{show:true,position:'top',formatter:p=>p.value+'/'+(p.dataIndex?10:3),color:ECHO_DEEP,fontWeight:700,fontSize:13}},
      {name:'Baseline B (freq. decay)',type:'bar',barWidth:'30%',itemStyle:{color:B,borderRadius:[6,6,0,0]},
       data:[D.errk.n3.b,D.errk.n10.b],
       label:{show:true,position:'top',formatter:p=>p.value+'/'+(p.dataIndex?10:3),color:B,fontWeight:700,fontSize:12}}
    ]}));})();

// ---- Confidence separation scatter ----
(function(){const e=mk('c_conf');
  const jit=i=>((i*2654435761)%1000/1000-0.5)*0.5;
  const good=D.conf.good.map((v,i)=>[0+jit(i),v]), bad=D.conf.bad.map((v,i)=>[1+jit(i+7),v]);
  const mg=D.conf.good.reduce((a,b)=>a+b,0)/D.conf.good.length;
  const mb=D.conf.bad.reduce((a,b)=>a+b,0)/D.conf.bad.length;
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:14,top:16,bottom:30,containLabel:true},
    tooltip:{trigger:'item',valueFormatter:v=>(+v).toFixed(2)},
    xAxis:{type:'value',min:-0.5,max:1.5,interval:1,axisLine:{show:false},axisTick:{show:false},
           splitLine:{show:false},axisLabel:{color:'#3F4D5A',fontSize:13,fontWeight:600,
           formatter:v=>v===0?'Good skills':(v===1?'Bad skills':'')}},
    yAxis:axY({min:0,max:1,interval:0.25}),
    series:[
      {type:'scatter',data:good,symbolSize:11,itemStyle:{color:GOOD,opacity:.7},
       markLine:{symbol:'none',silent:true,data:[{yAxis:mg,lineStyle:{color:GOOD,width:2.4}}],
                 label:{formatter:'mean '+mg.toFixed(2),color:GOOD,fontWeight:700,position:'insideStartTop'}}},
      {type:'scatter',data:bad,symbolSize:11,itemStyle:{color:BAD,opacity:.7},
       markLine:{symbol:'none',silent:true,
         data:[{yAxis:mb,lineStyle:{color:BAD,width:2.4},label:{formatter:'mean '+mb.toFixed(2),color:BAD,fontWeight:700,position:'insideEndBottom'}},
               {yAxis:0.30,lineStyle:{color:'#AAB4BF',type:'dashed',width:1},label:{formatter:'c_min 0.30',color:INK3,position:'insideStartTop'}},
               {yAxis:0.10,lineStyle:{color:BAD,type:'dashed',width:1},label:{formatter:'c_retire 0.10',color:BAD,position:'insideStartTop'}}]}}
    ]}));})();

// ---- Overhead stacked ----
(function(){const e=mk('c_over');
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:16,top:28,bottom:28,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:v=>Math.round(v).toLocaleString()},
    legend:{show:true,top:0,left:0,itemWidth:12,itemHeight:12,textStyle:{color:INK2,fontSize:12},
            data:['agent reply','Layer B (every turn)','Layer C (on alarm)']},
    xAxis:axX(D.over.cats),
    yAxis:axY({axisLabel:{color:INK3,fontSize:11,formatter:v=>(v/1000)+'k'}}),
    series:[
      {name:'agent reply',type:'bar',stack:'t',barWidth:'46%',itemStyle:{color:AGENT,borderRadius:[0,0,0,0]},data:D.over.agent},
      {name:'Layer B (every turn)',type:'bar',stack:'t',itemStyle:{color:ECHO},data:D.over.lb},
      {name:'Layer C (on alarm)',type:'bar',stack:'t',itemStyle:{color:LAYERC,borderRadius:[6,6,0,0]},data:D.over.lc,
       label:{show:true,position:'top',formatter:p=>{const i=p.dataIndex;const t=D.over.agent[i]+D.over.lb[i]+D.over.lc[i];return (t/1000).toFixed(1)+'k';},
              color:INK,fontWeight:700,fontSize:13}}
    ]}));})();

// ---- micro: M4 scatter ----
(function(){const e=mk('c_m4');
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:14,top:24,bottom:34,containLabel:true},
    tooltip:{trigger:'item',formatter:p=>'usefulness '+p.value[0].toFixed(2)+'<br>confidence '+p.value[1].toFixed(2)},
    xAxis:axY({name:'true usefulness',nameLocation:'middle',nameGap:24,nameTextStyle:{color:INK3,fontSize:11},
               min:0.4,max:1.0,interval:0.2,splitLine:{show:false}}),
    yAxis:axY({name:'confidence',min:0.2,max:0.7,interval:0.15}),
    graphic:[{type:'text',right:8,top:6,style:{text:'ρ = +'+D.micro.rho.toFixed(2),fill:ECHO_DEEP,font:'700 14px '+FONT}}],
    series:[
      {type:'line',data:[[0.4,0.30],[1.0,0.62]],symbol:'none',lineStyle:{color:'#E3E8ED',type:'dashed',width:1},silent:true},
      {type:'scatter',data:D.micro.m4,symbolSize:13,itemStyle:{color:ECHO,opacity:.85}}
    ]}));})();

// ---- micro: M1 grouped ----
(function(){const e=mk('c_m1');
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:14,top:30,bottom:24,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
    legend:{show:true,top:0,right:0,itemWidth:10,itemHeight:10,textStyle:{color:INK2,fontSize:11},data:['Echo','Hermes rule']},
    xAxis:axX(['Precision','Recall']),
    yAxis:axY({max:1,interval:0.5}),
    series:[
      {name:'Echo',type:'bar',barWidth:'30%',itemStyle:{color:ECHO,borderRadius:[5,5,0,0]},data:D.micro.m1.echo,
       label:{show:true,position:'top',formatter:p=>p.value.toFixed(2),color:ECHO_DEEP,fontSize:11,fontWeight:600}},
      {name:'Hermes rule',type:'bar',barWidth:'30%',itemStyle:{color:A,borderRadius:[5,5,0,0]},data:D.micro.m1.herm,
       label:{show:true,position:'top',formatter:p=>p.value.toFixed(2),color:INK2,fontSize:11,fontWeight:600}}
    ]}));})();

// ---- micro: M3 ----
(function(){const e=mk('c_m3');
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:14,top:24,bottom:24,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
    xAxis:axX(['Precision','Recall','F1']),
    yAxis:axY({max:1,interval:0.5}),
    series:[{type:'bar',barWidth:'46%',itemStyle:{color:ECHO,borderRadius:[5,5,0,0]},data:D.micro.m3,
      label:{show:true,position:'top',formatter:p=>p.value.toFixed(2),color:ECHO_DEEP,fontSize:12,fontWeight:700}}]}));})();

// ---- micro: M5 ----
(function(){const e=mk('c_m5');
  e.setOption(Object.assign({},base,{
    grid:{left:6,right:14,top:24,bottom:24,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:v=>(+v).toFixed(3)},
    xAxis:axX(['no weights','+ conf. weights']),
    yAxis:axY({max:0.5,interval:0.25}),
    series:[{type:'bar',barWidth:'46%',
      data:[{value:D.micro.m5[0],itemStyle:{color:A,borderRadius:[5,5,0,0]}},
            {value:D.micro.m5[1],itemStyle:{color:ECHO,borderRadius:[5,5,0,0]}}],
      label:{show:true,position:'top',formatter:p=>p.value.toFixed(3),color:INK,fontSize:12,fontWeight:600}}]}));})();
</script>
</body></html>"""


def main():
    html = (TEMPLATE
            .replace("__DATA__", json.dumps(DATA))
            .replace("__FAIRPCT__", f"{DATA['over']['fairpct']:.1f}")
            .replace("__SSPCT__", f"{DATA['over']['ss_pct']:.0f}")
            .replace("__FIRES__", str(int(DATA['over']['fires'])))
            .replace("__TURNS__", str(DATA['over']['turns']))
            .replace("__NOJUDGE__", str(DATA['over']['nojudge']))
            .replace("__NRUN__", str(DATA['over']['nojudge'] + DATA['over']['judge']))
            .replace("__M3C__", DATA['micro']['m3c']))
    out = FIG / "charts.html"
    out.write_text(html)
    print("wrote", out)


if __name__ == "__main__":
    main()
