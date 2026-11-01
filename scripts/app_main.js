// AI Daily 本体（docs/index.html に埋め込む）。データは data/app.json（collect.py が毎朝生成）
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt=n=>(n??0).toLocaleString("ja-JP");
const k=n=>n>=1000?(n/1000).toFixed(n>=100000?0:1)+"k":String(n);
const PAL=["#e8590c","#c2337f","#1c7ed6","#2b8a3e","#7048e8","#d6336c","#0c8599","#f08c00","#5c940d","#868e96"];
let D=null, DAYS=[], di=0, view="rank", mode="weekly";

const listAt=i=>(mode==="weekly"?D.ranks:D.daily_ranks)[DAYS[i]]||[];
const rankMap=i=>{const m={};(i>=0?listAt(i):[]).forEach(r=>m[r.full_name]=r.rank);return m};
const starsOf=(n,upto)=>(D.series[n]||[]).slice(0,upto+1);
const gains=arr=>arr.slice(1).map((v,i)=>v!=null&&arr[i]!=null?v-arr[i]:null);
function spark(arr,w=64,h=18){
  const v=arr.filter(x=>x!=null);if(v.length<2)return `<svg width="${w}" height="${h}" aria-hidden="true"><text x="${w}" y="${h-4}" text-anchor="end">蓄積中</text></svg>`;
  const mx=Math.max(...v,1),st=w/(arr.length-1);let p=[];
  arr.forEach((x,i)=>{if(x!=null)p.push(`${(i*st).toFixed(1)},${(h-2-(x/mx)*(h-4)).toFixed(1)}`)});
  const last=p.at(-1).split(",");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${p.join(" ")}" fill="none" stroke="var(--acc)" stroke-width="1.5"/><circle cx="${last[0]}" cy="${last[1]}" r="2" fill="var(--acc)"/></svg>`;
}
function mv(cur,prev){
  if(di===0)return `<span class="mv eq">―</span>`;
  if(prev==null)return `<span class="mv new">NEW</span>`;
  const d=prev-cur;return d>0?`<span class="mv up">↑${d}</span>`:d<0?`<span class="mv dn">↓${-d}</span>`:`<span class="mv eq">―</span>`;
}
function streak(n,i){let c=0;for(let j=i;j>=0;j--){if((D.ranks[DAYS[j]]||[]).some(r=>r.full_name===n))c++;else break}return c}
function renderRank(){
  const rows=listAt(di), pv=rankMap(di-1);
  $("#wkh").textContent=mode==="weekly"?"+週 / 推移":"+日 / 推移";
  $("#rows").innerHTML=rows.map(r=>{const [o,n]=r.full_name.split("/");
    return `<tr class="r${r.rank}" data-n="${esc(r.full_name)}"><td class="no"><b>${r.rank}</b>${mv(r.rank,pv[r.full_name])}</td>
    <td class="ttl"><b>${esc(n)}</b><small>${esc(o)}${r.language?" · "+esc(r.language):""}</small></td>
    <td class="wk"><b>+${fmt(r.delta)}${r.basis==="trending"?"†":r.basis==="new"?"★":""}</b>${spark(gains(starsOf(r.full_name,di)).slice(-13))}</td>
    <td class="ds"><span>${esc(r.summary_ja||r.description||"—")}</span></td></tr>`}).join("")||`<tr><td colspan="4" class="empty2">この日のデータはありません</td></tr>`;
}
function renderTrend(){
  const from=Math.max(0,di-7), idx=[];for(let i=from;i<=di;i++)idx.push(i);
  const top=(D.ranks[DAYS[di]]||[]).slice(0,10).map(r=>r.full_name);
  const W=Math.min(820,document.querySelector("main").clientWidth-30),H=250,L=20,R=118,T=12,B=22,N=12;
  const x=i=>idx.length<2?(L+(W-R))/2:L+(i-from)*((W-L-R)/(idx.length-1)), y=rk=>T+(Math.min(rk,N)-1)*((H-T-B)/(N-1));
  let s=`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Top10の順位推移">`;
  for(let rk=1;rk<=10;rk++)s+=`<line x1="${L}" x2="${W-R}" y1="${y(rk)}" y2="${y(rk)}" stroke="var(--grid)"/>`;
  idx.forEach(i=>s+=`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${DAYS[i].slice(5).replace("-","/")}</text>`);
  top.forEach((n,j)=>{const pts=idx.map(i=>{const m=rankMap(i);return m[n]?[x(i),y(m[n])]:null}).filter(Boolean);const c=PAL[j%PAL.length];
    s+=`<g data-n="${esc(n)}" style="cursor:pointer">`+(pts.length>1?`<polyline points="${pts.map(p=>p.join(",")).join(" ")}" fill="none" stroke="${c}" stroke-width="2.5" stroke-linejoin="round"/>`:"")+
    pts.map(p=>`<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="${c}"/>`).join("")+
    `<text x="${W-R+6}" y="${y(j+1)+3}" style="fill:${c};font-weight:600">${j+1}. ${esc(n.split("/")[1].slice(0,15))}</text></g>`});
  $("#bump").innerHTML=s+"</svg>"+(idx.length<3?`<div class="sub">データは10/31から蓄積中です。日数が増えるほど推移が見えるようになります。</div>`:"");
  const cur=rankMap(di),pr=rankMap(di-1),rows=D.ranks[DAYS[di]]||[];
  const climb=di?rows.map(r=>({r,d:pr[r.full_name]?pr[r.full_name]-r.rank:0})).filter(x=>x.d>0).sort((a,b)=>b.d-a.d).slice(0,5):[];
  $("#climb").innerHTML=climb.map(x=>`<li data-n="${esc(x.r.full_name)}"><b>${esc(x.r.full_name)}</b><span><span class="mv up">↑${x.d}</span> ${x.r.rank}位</span></li>`).join("")||"<li>前日比較できるデータがありません</li>";
  const lc={};rows.forEach(r=>{const l=r.language||"その他";lc[l]=(lc[l]||0)+1});
  const ls=Object.entries(lc).sort((a,b)=>b[1]-a[1]).slice(0,7),mx=ls.length?ls[0][1]:1;
  $("#langs").innerHTML=ls.map(([l,c])=>`<div class="bar"><span>${esc(l)}</span><i style="width:${c/mx*100}%"></i><span>${c}件</span></div>`).join("");
  $("#newbies").innerHTML=di?rows.filter(r=>!pr[r.full_name]).map(r=>`<li data-n="${esc(r.full_name)}"><b>${esc(r.full_name)}</b><span>+${fmt(r.delta)}</span></li>`).join("")||"<li>なし</li>":"<li>前日比較できるデータがありません</li>";
  $("#streak").innerHTML=[...rows].map(r=>({r,s:streak(r.full_name,di)})).sort((a,b)=>b.s-a.s).slice(0,5).map(x=>`<li data-n="${esc(x.r.full_name)}"><b>${esc(x.r.full_name)}</b><span>${x.s}日連続</span></li>`).join("");
}
function lineChart(arr,{w,h,bars,labels}){
  const pts=arr.map((v,i)=>[i,v]).filter(p=>p[1]!=null);if(pts.length<2)return `<div class="sub">データ蓄積中（2日分以上で表示）</div>`;
  const L=40,R=6,T=8,B=18,vs=pts.map(p=>p[1]),mx=Math.max(...vs),mn=bars?0:Math.min(...vs),rg=Math.max(1,mx-mn);
  const x=i=>L+i*((w-L-R)/Math.max(1,arr.length-1)),y=v=>T+(1-(v-mn)/rg)*(h-T-B);
  let s=`<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img">`;
  [0,.5,1].forEach(f=>{const v=mn+rg*f;s+=`<line x1="${L}" x2="${w-R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/><text x="${L-4}" y="${y(v)+3}" text-anchor="end">${k(Math.round(v))}</text>`});
  [0,arr.length-1].forEach(i=>s+=`<text x="${x(i)}" y="${h-4}" text-anchor="${i?"end":"start"}">${labels[i].slice(5).replace("-","/")}</text>`);
  if(bars){const bw=(w-L-R)/arr.length*.7;pts.forEach(([i,v])=>s+=`<rect x="${x(i)-bw/2}" y="${y(v)}" width="${bw}" height="${Math.max(0,y(0)-y(v))}" rx="2" fill="var(--acc)" opacity="${i===arr.length-1?1:.55}"/>`)}
  else{const p=pts.map(([i,v])=>`${x(i)},${y(v)}`).join(" ");s+=`<polyline points="${p}" fill="none" stroke="var(--acc)" stroke-width="2"/><circle cx="${x(pts.at(-1)[0])}" cy="${y(pts.at(-1)[1])}" r="3.5" fill="var(--acc)"/>`}
  return s+"</svg>";
}
function openSheet(n){
  const r=listAt(di).find(x=>x.full_name===n)||(D.ranks[DAYS[di]]||[]).find(x=>x.full_name===n);if(!r)return;
  const w=Math.min(600,window.innerWidth)-32, st=starsOf(n,di), lab=DAYS.slice(0,di+1);
  $("#sheetin").innerHTML=`<div class="shead"><div><small style="color:var(--mut)">${esc(r.language||"")}</small><h3>${esc(n)}</h3></div><button class="x" aria-label="閉じる" onclick="closeSheet()">×</button></div>
   <p style="margin:6px 0">${esc(r.summary_ja||r.description)}</p>
   <div class="stats"><div><small>順位</small><b>${r.rank}位</b></div><div><small>+${mode==="weekly"?"週":"日"}</small><b>${fmt(r.delta)}</b></div><div><small>累計</small><b>${k(r.stars)}</b></div><div><small>連続</small><b>${streak(n,di)}日</b></div></div>
   <div class="card"><h2>累計スター</h2><div class="sub">${lab.length}日</div>${lineChart(st,{w:w-30,h:150,labels:lab})}</div>
   <div class="card"><h2>日別の獲得スター</h2><div class="sub">${lab.length-1}日</div>${lineChart(gains(st),{w:w-30,h:130,bars:true,labels:lab.slice(1)})}</div>
   <a class="gh" href="${esc(r.url)}" target="_blank" rel="noopener">GitHubで開く →</a>`;
  $("#sheet").hidden=false;
}
function closeSheet(){$("#sheet").hidden=true}
$("#sheet").onclick=e=>{if(e.target.id==="sheet")closeSheet()};
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeSheet()});
document.addEventListener("click",e=>{if(e.target.closest("#v-news"))return;const t=e.target.closest("[data-n]");if(t&&!t.closest("#sheet"))openSheet(t.dataset.n)});
document.querySelectorAll("#v-rank .chip").forEach(b=>b.onclick=()=>{mode=b.dataset.k;document.querySelectorAll("#v-rank .chip").forEach(x=>x.setAttribute("aria-pressed",x===b));renderRank()});
/*__NEWS__*/
function head(){
  const T={rank:["LEADERBOARD","GitHub <em>スター</em>獲得ランキング"],trend:["TRENDS","<em>推移</em>と傾向"],news:["DAILY BRIEFING","<em>AI</em>ニュース"]}[view];
  $("#eyebrow").textContent=T[0];$("#title").innerHTML=T[1];
  const N=view==="news";document.querySelector(".step").style.visibility=N?"hidden":"visible";
  $("#day").textContent=(DAYS[di]||"").slice(5).replace("-","/");
  $("#prev").disabled=di<=0;$("#next").disabled=di>=DAYS.length-1;
  $("#meta").textContent=N?(ND[ni]?`${md(ND[ni].date)}（${ND[ni].weekday}）${ND[ni].updated||""} 更新 ・ 対象 ${ND[ni].period}`:"AIニュースはまだありません")
    :`${DAYS[di]} 集計 ・ 追跡 ${D.pool_size??"—"} リポジトリ`;
}
function render(){head();if(view==="rank")renderRank();if(view==="trend")renderTrend();if(view==="news")renderNews()}
function setView(v){view=v;document.querySelectorAll("nav button").forEach(b=>b.setAttribute("aria-selected",b.dataset.v===v));
  ["rank","trend","news"].forEach(x=>$("#v-"+x).hidden=x!==v);try{localStorage.setItem("view",v)}catch(e){}render();window.scrollTo(0,0)}
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>setView(b.dataset.v));
$("#prev").onclick=()=>{di--;render()};$("#next").onclick=()=>{di++;render()};
window.addEventListener("resize",()=>{if(view==="trend")renderTrend()});
(async()=>{
  try{D=await (await fetch("data/app.json",{cache:"no-store"})).json()}catch(e){D=null}
  if(!D){$("#meta").textContent="データを読み込めませんでした。通信状態を確認して再読み込みしてください";return}
  DAYS=D.dates;di=DAYS.length-1;ND=D.news||[];ni=ND.length-1;
  let v0="rank";try{v0=localStorage.getItem("view")||"rank"}catch(e){}
  if(["rank","trend","news"].includes(location.hash.slice(1)))v0=location.hash.slice(1);
  setView(v0);
})();
if("serviceWorker" in navigator&&location.protocol==="https:")navigator.serviceWorker.register("sw.js");
