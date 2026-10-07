'use strict';
const tg=window.Telegram?.WebApp;tg?.ready();tg?.expand();
const $=id=>document.getElementById(id);
const fmt=x=>Number.isFinite(x)?x.toLocaleString('tr-TR',{maximumFractionDigits:2}):'—';
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=x=>Number.isFinite(x)?fmt(x)+' TL':'—';
const positive=x=>Number.isFinite(x)&&x>0;
const date=x=>{if(!x)return '—';const d=new Date(x);return Number.isNaN(d.valueOf())?esc(x):d.toLocaleString('tr-TR');};
const safeKAP=u=>typeof u==='string'&&/^https:\/\/(www\.)?kap\.org\.tr\/tr\/Bildirim\/\d+$/.test(u);
const link=u=>safeKAP(u)?`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">KAP açıklamasını aç ↗</a>`:'';
const STORAGE='mk-paper-positions-v1';
let data=null,bundle=null,bundlePath=null,manifest=null,tab='top',limit=30,query='',detailTicker=null,positions={},storageOK=true;
function validatePositions(input){
 if(!input||typeof input!=='object'||Array.isArray(input))throw Error('Takip yedeği geçersiz.');
 const output={};
 for(const [ticker,p] of Object.entries(input)){
  if(!/^[A-Z0-9]{2,16}$/.test(ticker)||!p||!positive(p.entry_price)||!positive(p.quantity)||p.cost!==100000||Math.abs(p.entry_price*p.quantity-100000)>0.01||!Number.isFinite(Date.parse(p.added_at)))throw Error('Takip yedeğindeki alım kaydı geçersiz.');
  output[ticker]={entry_price:p.entry_price,quantity:p.quantity,cost:100000,added_at:p.added_at,entry_price_date:typeof p.entry_price_date==='string'?p.entry_price_date:null,entry_scan_at:typeof p.entry_scan_at==='string'?p.entry_scan_at:null,entry_shares:positive(p.entry_shares)?p.entry_shares:null};
 }
 return output;
}
try{positions=validatePositions(JSON.parse(localStorage.getItem(STORAGE)||'{}'));}catch{positions={};storageOK=false;}
function persist(next){
 try{localStorage.setItem(STORAGE,JSON.stringify(next));positions=next;storageOK=true;return true;}catch{$('notice').textContent='Tarayıcı kayıt izni vermedi. Sanal alım kaydedilmedi; tarayıcı depolama ayarını kontrol et.';return false;}
}
async function api(){
 const r=await fetch('radar-manifest.json?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('Panel verisi alınamadı.');
 const next=await r.json();if(next.schema!==1||!/^radar-data\/[a-f0-9]{64}\.json$/.test(next.data))throw Error('Panel veri biçimi desteklenmiyor.');
 if(next.data!==bundlePath){const response=await fetch(next.data,{cache:'no-cache'});if(!response.ok)throw Error('Yayımlanan veri dosyası alınamadı.');const candidate=await response.json();if(!Array.isArray(candidate.snapshot?.rows))throw Error('Veri dosyası geçersiz.');bundle=candidate;bundlePath=next.data;}
 manifest=next;return bundle.snapshot;
}
function ranked(){
 const map=new Map(data.rows.map(r=>[r.ticker,r]));
 const codes=data.ranked||data.rows.filter(r=>r.status==='HESAPLANDI'&&Number.isFinite(r.potential)&&positive(r.fair_value)).sort((a,b)=>b.potential-a.potential||a.ticker.localeCompare(b.ticker)).map(r=>r.ticker);
 return codes.map(t=>map.get(t)).filter(Boolean);
}
function visibleRows(){
 const rank=ranked(),seen=new Set(rank.map(r=>r.ticker));
 let rows=tab==='top'?rank.slice(0,30):tab==='watch'?Object.keys(positions).map(t=>data.rows.find(r=>r.ticker===t)||{ticker:t,status:'HENUZ_TARANMADI',bands:{}}):[...rank,...data.rows.filter(r=>!seen.has(r.ticker)).sort((a,b)=>a.ticker.localeCompare(b.ticker))];
 return rows.filter(r=>(r.ticker+' '+(r.company||'')).toLocaleUpperCase('tr-TR').includes(query));
}
function positionValue(r){
 const p=positions[r.ticker];if(!p)return null;
 if(!positive(r.price))return {p,reason:'Güncel yayımlanmış fiyat yok'};
 if(r.status==='SERMAYE_KONTROLU'||r.status==='PAY_SINIFI_KONTROLU'||(positive(p.entry_shares)&&positive(r.shares)&&Math.abs(r.shares/p.entry_shares-1)>1e-6))return {p,reason:'Sermaye/pay değişimi: lot düzeltmesi gerekli'};
 if(p.entry_price_date&&r.price_date&&r.price_date<p.entry_price_date)return {p,reason:'Yayımlanan fiyat alış fiyatından eski'};
 const value=p.quantity*r.price,profit=value-p.cost;
 return {p,value,profit,pct:profit/p.cost*100};
}
function pnlHTML(r){const value=positionValue(r);if(!value)return '';const p=value.p;
 return `<div class="paper"><div><small>Sanal alış · ${date(p.added_at)}<br>Alış fiyatının tarihi: ${date(p.entry_price_date)}</small><strong>${money(p.entry_price)} × ${fmt(p.quantity)} lot</strong></div><div><small>100.000 TL başlangıç</small><strong class="${value.profit>=0?'potential':'negative'}">${value.reason?esc(value.reason):money(value.profit)+' ('+fmt(value.pct)+'%)'}</strong></div>${!value.reason?'<p>Güncel sanal değer: '+money(value.value)+'</p>':''}</div>`;
}
function canFollow(r){return r&&r.status==='HESAPLANDI'&&positive(r.price);}
function toggleFollow(ticker){
 const next={...positions};const r=data.rows.find(r=>r.ticker===ticker);
 if(next[ticker])delete next[ticker];else{
  if(!canFollow(r))return;
  next[ticker]={entry_price:r.price,quantity:100000/r.price,cost:100000,added_at:new Date().toISOString(),entry_price_date:r.price_date||null,entry_scan_at:data.as_of,entry_shares:positive(r.shares)?r.shares:null};
 }
 if(persist(next)){render();if(detailTicker===ticker)detail(ticker);}
}
function portfolioStats(){
 const entries=Object.keys(positions).map(t=>positionValue(data.rows.find(r=>r.ticker===t)||{ticker:t}));
 const valid=entries.filter(x=>!x.reason);const profit=valid.reduce((s,x)=>s+x.profit,0),cost=valid.reduce((s,x)=>s+x.p.cost,0);
 $('portfolioStats').innerHTML=`<div><strong>${entries.length}</strong><small>Takip edilen hisse</small></div><div><strong>${money(entries.length*100000)}</strong><small>Toplam sanal alım</small></div><div><strong class="${profit>=0?'potential':'negative'}">${valid.length?money(profit):'—'}</strong><small>${valid.length?fmt(profit/cost*100)+'% · ':''}${valid.length}/${entries.length} hesaplanabilir</small></div>`;
 $('portfolioMessage').textContent=entries.some(x=>x.reason)?'Eksik fiyat veya sermaye değişimi olan hisseler toplam kâr/zarara katılmadı. Kartta gerekçesi görünür.':'';
}
function render(){
 if(!data)return;const rows=visibleRows(),rank=new Map(ranked().map((r,i)=>[r.ticker,i+1]));
 $('portfolio').hidden=tab!=='watch';if(tab==='watch')portfolioStats();
 $('cards').innerHTML=rows.slice(0,limit).map(r=>`<article class="card"><button class="card-detail" data-detail="${esc(r.ticker)}"><div class="card-head"><div><span class="rank">${rank.has(r.ticker)?'#'+rank.get(r.ticker):''}</span><span class="symbol">${esc(r.ticker)}</span></div><span>›</span></div><p class="company">${esc(r.company||'')}</p><div class="grid"><div class="cell"><small>Fiyat</small><strong>${money(r.price)}</strong></div><div class="cell"><small>Baz değer</small><strong>${money(r.fair_value)}</strong></div><div class="cell"><small>Potansiyel</small><strong class="${r.potential>=0?'potential':'negative'}">${Number.isFinite(r.potential)?fmt(r.potential)+'%':'—'}</strong></div></div><span class="tag ${r.status==='HESAPLANDI'?'good':''}">${esc((r.status||'VERI_EKSIK').replaceAll('_',' '))}</span><p class="previous">${r.model_version==='MK-COVERAGE-RI-0.2'?'Alternatif artık kâr · ':''}Bilanço: ${esc(r.period_end||r.period||'—')}${r.target_context?.previous_filing_target?' · Önceki baz: '+money(r.target_context.previous_filing_target.fair_value):''}</p></button>${pnlHTML(r)}<button class="follow ${positions[r.ticker]?'active':''}" data-follow="${esc(r.ticker)}" ${!positions[r.ticker]&&!canFollow(r)?'disabled':''}>${positions[r.ticker]?'⭐ Takipten çıkar':'☆ Takibe ekle · 100.000 TL'}</button></article>`).join('')||'<p class="muted">Bu görünümde hisse yok. Tüm hisselerden arayıp takibe ekleyebilirsin.</p>';
 $('more').hidden=rows.length<=limit;
}
async function load(){try{
 data=await api();const rankedRows=ranked();$('stats').innerHTML=`<div><strong>${data.total}</strong><small>Evren kodu</small></div><div><strong>${rankedRows.length}</strong><small>Fiyatlı hesap / 500 hedef</small></div><div><strong>${rankedRows[0]?esc(rankedRows[0].ticker):'—'}</strong><small>En yüksek baz potansiyel</small></div>`;
 $('stamp').textContent='Son tarama: '+date(data.as_of)+' · Yayın: '+date(manifest.exported_at);
 const old=Date.now()-Date.parse(data.as_of||'')>86400000;
 $('notice').textContent=(old?'Son tarama 24 saatten eski. ':'')+`Finansal hesabı olan: ${data.coverage?.financial_calculated??data.rows.filter(r=>positive(r.fair_value)).length} · Fiyatıyla sıralanan: ${rankedRows.length}/500. `+'Bilgisayar kapalıyken son yayımlanan veriler gösterilir. Yeni fiyat/bilanço için bilgisayarda güncelle ve yayımla.'+(!storageOK?' Takip kayıtları okunamadı; yedeğini kullan.':'');render();
 if(detailTicker&&$('detail').open)detail(detailTicker);
}catch(e){$('notice').textContent=e.message+(data?' Ekrandaki sonuçlar önceki başarılı yüklemeden kaldı.':'');}}
const metricNames={revenue:'Hasılat',gross_profit:'Brüt kâr',operating_profit:'Faaliyet kârı',net_income_parent:'Ana ortaklık net kârı',equity_parent:'Ana ortaklık özkaynağı',cash:'Nakit',assets:'Toplam varlıklar'};
function reportHTML(report,label){
 if(!report)return `<div class="report"><h3>${label}</h3><p class="muted">Bu döneme ait yerel rapor kaydı yok.</p></div>`;
 return `<div class="report"><h3>${label}</h3><strong>${esc(report.period_end)}</strong><p class="muted">Yayın: ${date(report.published_at)}<br>${esc(report.statement_type)} · ${esc(report.basis)} · ${esc(report.currency||'Para birimi doğrulanmadı')}</p><dl>${Object.entries(metricNames).map(([key,name])=>`<div><dt>${name}</dt><dd>${Number.isFinite(report.metrics?.[key])?fmt(report.metrics[key])+' '+esc(report.currency||''):'—'}</dd></div>`).join('')}</dl>${report.input_error?'<p class="negative">'+esc(report.input_error)+'</p>':''}${(report.source_urls||[]).map(link).join('')}</div>`;
}
function newsHTML(r){
 const meta=data.news_meta||{};const events=(r.news||[]).filter(n=>safeKAP(n.url));
 return `<h3>İş anlaşmaları ve önemli KAP açıklamaları</h3><p class="muted">Son kontrol: ${date(meta.last_attempt)} · ${esc(meta.status||'NOT_FETCHED')}<br>Son 90 günün anahtar sözcüklerle seçilmiş başlıkları. Başlık/süreç bir ihalenin kazanıldığını veya kâr oluştuğunu kanıtlamaz; açıklamayı açıp kontrol et.</p>${meta.status&&meta.status!=='OK'?'<p class="negative">Haber kapsamı eksik veya henüz yenilenmedi. Gösterilen eski kayıtlar korunmuş olabilir.</p>':''}${events.map(n=>`<article class="news"><span class="tag">${esc(n.category)}</span><small>${date(n.published_at)}</small><h4>${esc(n.title)}</h4><p>${esc(n.summary)}</p>${link(n.url)}</article>`).join('')||'<p class="muted">Seçilen haber aralığında eşleşen açıklama kaydı yok. Bu, şirketin hiç yeni işi olmadığı anlamına gelmez.</p>'}`;
}
function detail(ticker){
 const r=data.rows.find(r=>r.ticker===ticker)||{ticker,status:'HENUZ_TARANMADI',bands:{}};detailTicker=ticker;
 const c=r.target_context||{},prev=c.previous_filing_target,last=c.last_valid_target,reports=r.financial_reports||{};
 const histories=bundle.histories?.[ticker]||[];const profile=r.company_profile||{},companyURL=typeof profile.kap_company_url==='string'&&/^https:\/\/(www\.)?kap\.org\.tr\/tr\/sirket-bilgileri\/ozet\/\d+$/.test(profile.kap_company_url)?profile.kap_company_url:null;
 $('detailBody').innerHTML=`<h2>${esc(ticker)}</h2><p class="muted">${esc(r.company||'')}</p><button id="detailFollow" class="follow" ${!positions[ticker]&&!canFollow(r)?'disabled':''}>${positions[ticker]?'⭐ Takipten çıkar':'☆ 100.000 TL sanal alımla takibe ekle'}</button>${pnlHTML(r)}<p>${esc(r.status)} · Fiyat tarihi: ${date(r.price_date)}</p><h3>Şirket bilgileri</h3><p>${esc(profile.company_type||'Şirket')} · ${esc(ticker)}<br>Hesapta kullanılan pay sayısı: ${fmt(r.shares)} · Tarih: ${esc(r.shares_as_of||'—')}</p>${companyURL?'<p><a href="'+esc(companyURL)+'" target="_blank" rel="noopener noreferrer">KAP şirket bilgileri ↗</a></p>':''}<h3>Güncel değer senaryoları</h3><div class="grid">${['bear','base','bull'].map(k=>`<div class="cell"><small>${{bear:'Senaryo 1',base:'Baz',bull:'Senaryo 3'}[k]}</small><strong>${money(r.bands?.[k]?.price)}</strong></div>`).join('')}</div><p>Hesaplama dönemi: ${esc(r.period_end||r.period||'—')} · ${esc(r.basis||'')}<br>Model: ${esc(r.model_version||'—')}${r.earnings_reference_period?'<br>Yıllık kâr referansı: '+esc(r.earnings_reference_period)+' · Güncel TTM değildir.':''}</p>${prev?'<p>Önceki bilanço baz hedefi: <b>'+money(prev.fair_value)+'</b> · '+esc(prev.period_end)+'</p>':'<p class="muted">Önceki dönem hedef kaydı yok; önceki rapor aşağıda varsa incelenebilir.</p>'}${Number.isFinite(c.target_change_pct)?'<p>Pay sayısı aynı olan hedef değişimi: '+fmt(c.target_change_pct)+'%</p>':''}${!c.current_target_available&&last?'<p class="negative">Yeni hedef doğrulanamadı. Son geçerli eski hedef: '+money(last.fair_value)+' · '+esc(last.period_end)+'. Güncel hedef değildir.</p>':''}<h3>Önemli notlar</h3><p>${esc(r.reason||'')}</p><ul>${(r.warnings||[]).map(w=>'<li>'+esc(w)+'</li>').join('')}</ul><div class="source-links">${(r.source_urls||[]).map(link).join('')}</div><h3>Güncel ve önceki dönem bilançosu</h3><p class="muted">${esc(reports.note||'Raporlanan tutarlar; dönem ve parasal ölçüm farklılığı kontrol edilmeden büyüme karşılaştırması yapılmaz.')}</p><div class="report-grid">${reportHTML(reports.current,'Güncel rapor')}${reportHTML(reports.previous,'Önceki dönem raporu')}</div>${newsHTML(r)}<h3>Bilanço hedef geçmişi</h3>${histories.map(h=>`<div class="history"><strong>${esc(h.period_end)} · ${money(h.fair_value)}</strong><p class="muted">${esc(h.published_at)} · ${esc(h.model_version)}</p></div>`).join('')||'<p class="muted">Geçmiş hedef kaydı yok.</p>'}`;
 $('detailFollow').onclick=()=>toggleFollow(ticker);if(!$('detail').open)$('detail').showModal();
}
$('search').oninput=e=>{query=e.target.value.toLocaleUpperCase('tr-TR');limit=30;render();};
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;limit=30;document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('selected',x===b));render();});
$('cards').onclick=e=>{const follow=e.target.closest('[data-follow]');if(follow){toggleFollow(follow.dataset.follow);return;}const card=e.target.closest('[data-detail]');if(card)detail(card.dataset.detail);};
$('more').onclick=()=>{limit+=30;render();};$('close').onclick=()=>{$('detail').close();detailTicker=null;};$('detail').addEventListener('close',()=>{detailTicker=null;});
$('exportPortfolio').onclick=()=>{const blob=new Blob([JSON.stringify({schema:1,positions},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='mk-radar-takip.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('importPortfolio').onchange=async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>1000000)throw Error('Yedek çok büyük.');const backup=JSON.parse(await file.text());if(backup.schema!==1)throw Error('Yedek sürümü desteklenmiyor.');const incoming=validatePositions(backup.positions);const conflicts=Object.keys(incoming).filter(t=>positions[t]);for(const ticker of conflicts)delete incoming[ticker];if(persist({...positions,...incoming})){render();$('portfolioMessage').textContent='Yedek eklendi. Mevcut alış kayıtları korunarak yalnız yeni hisseler aktarıldı.';}}catch(error){$('portfolioMessage').textContent=error.message;}finally{e.target.value='';}};
load();setInterval(load,60000);
