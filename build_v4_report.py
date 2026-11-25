"""Ten-page Thai v0.4 report from frozen, independently verified evidence.

Presentation only: no controller evaluation or modification of frozen sources.
--preview generates labeled layout material without numerical conclusions.
"""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse
import hashlib
import html
import json
import re
import shutil

import fitz
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT=Path(__file__).resolve().parent
REGIMES=('legacy_instant','legacy_transit3','integer_transit3','finite_pipeline','startup','delayed_noisy')
POLICIES=('static','capacity_v2','capacity_balance','v3_full','v4_hybrid','v4_no_risk','v4_no_recovery')
BASELINES=('capacity_v2','capacity_balance','v3_full')
NAMES={'static':'Static','capacity_v2':'Capacity v2','capacity_balance':'Capacity balance',
       'v3_full':'V3 full','v4_hybrid':'V4 hybrid','v4_no_risk':'V4 no risk','v4_no_recovery':'V4 no recovery'}
REGIME_LABELS={r:f'R{i}' for i,r in enumerate(REGIMES)}
GROUP_NAMES={
 'surge_only':'Flash: งานเข้าเพิ่ม','ingress_slow':'Flash: รับเข้าช้า',
 'processing_slow':'Flash: ประมวลผลช้า','delivery_slow':'Flash: ส่งออกช้า',
 'shifting_bottleneck':'Flash: คอขวดเปลี่ยนจุด','generated_underload':'สร้างใหม่: ภาระต่ำ',
 'generated_sustained_processing_fault':'สร้างใหม่: ช้าต่อเนื่อง',
 'generated_switching_fault':'สร้างใหม่: คอขวดสลับ','generated_pulsed_short_fault':'สร้างใหม่: พุ่ง/ขัดข้องสั้น',
 'generated_asymmetric_recovery':'สร้างใหม่: ฟื้นตัวไม่สมมาตร',
 'generated_sustained_overload':'สร้างใหม่: งานเกินกำลัง'}
INK,TEAL,BLUE,RED,GRAY,PURPLE='#17324B','#147D72','#4A79AA','#B7473A','#8595A6','#7651A5'
COLORS={'static':GRAY,'capacity_v2':BLUE,'capacity_balance':TEAL,'v3_full':RED,
        'v4_hybrid':PURPLE,'v4_no_risk':'#B39BCB','v4_no_recovery':'#C1CCD4'}
PENDING='รอผล'


def read(path):
 return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
 return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dig(mapping,*keys,default=None):
 for key in keys:
  if not isinstance(mapping,dict) or key not in mapping:return default
  mapping=mapping[key]
 return mapping


def num(value,digits=2):
 return PENDING if value is None else f'{value:,.{digits}f}'


def pct(value):
 return 'ไม่กำหนด' if value is None else f'{value:.2f}%'


def interval(value,digits=2):
 return PENDING if value is None else f'{num(value[0],digits)} ถึง {num(value[1],digits)}'


def relative(new,old):
 return None if new is None or old is None or old<=1e-6 else 100*(new-old)/old


def status(value):
 if value is None:return '<b class="muted">รอผล</b>'
 return '<b class="green">ผ่าน</b>' if value else '<b class="red">ไม่ผ่าน</b>'


def table(headers,rows,highlight=None,small=False):
 head=''.join(f'<th>{value}</th>' for value in headers)
 body=[]
 for index,row in enumerate(rows):
  cls=' class="highlight"' if highlight is not None and index==highlight else ''
  body.append('<tr'+cls+'>'+''.join(f'<td class="{"left" if j==0 or len(re.sub("<[^>]+>", "", str(cell)))>24 else "num"}">{cell}</td>' for j,cell in enumerate(row))+'</tr>')
 return f'<table class="{"compact" if small else ""}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def caption(text):
 return '<p class="caption">'+text+'</p>'


def evidence(files):
 return caption('หลักฐาน: '+', '.join('<span class="file">'+html.escape(name)+'</span>' for name in files))


def prepare_fonts(out):
 (out/'fonts').mkdir(exist_ok=True)
 for name in ('NotoSansThai-Regular.ttf','NotoSansThai-Bold.ttf','NotoSans-Regular.ttf','NotoSans-Bold.ttf'):
  source=ROOT/'report/fonts'/name
  if not source.is_file():source=Path('/usr/share/fonts/truetype/noto')/name
  if not source.is_file():raise FileNotFoundError(name)
  shutil.copyfile(source,out/'fonts'/name)


def figures(summary,out,preview=False):
 destination=out/'figures';destination.mkdir(exist_ok=True)
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10.4,
  'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,
  'axes.labelcolor':INK,'xtick.color':INK,'ytick.color':INK,'text.color':INK,
  'axes.edgecolor':'#DCE4EA','savefig.facecolor':'white'})
 for metric,name,title in [('risk','risk_by_regime','STATIC-relative CVaR90'),('mean','mean_by_regime','Original-horizon mean demand deficit')]:
  fig,ax=plt.subplots(figsize=(9.5,2.85))
  if preview:
   ax.set_axis_off();ax.text(.5,.5,'LAYOUT ONLY - WAITING FOR FROZEN VALIDATION',ha='center',va='center',color=GRAY)
   ax.text(.5,.3,title,ha='center',va='center',color=INK)
  else:
   for p in ('capacity_v2','capacity_balance','v3_full','v4_hybrid'):
    values=[summary['regimes'][r]['static_relative_harm'][p]['cvar90'] if metric=='risk' else summary['regimes'][r]['overall'][p]['mean_deficit'] for r in REGIMES]
    ax.plot(range(6),values,'o-',label=NAMES[p],color=COLORS[p],linewidth=2,markersize=5)
   ax.set_xticks(range(6),[REGIME_LABELS[r] for r in REGIMES]);ax.set_xlabel('Nested realism regimes (categorical; see page 3)')
   ax.set_ylabel('Work units');ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_:f'{x/1000:.1f}k'))
   ax.grid(color='#E5EBEF');ax.set_axisbelow(True);ax.legend(ncol=4,fontsize=8.3,loc='upper center',bbox_to_anchor=(.5,1.22))
  fig.subplots_adjust(left=.09,right=.98,top=.81,bottom=.23)
  fig.savefig(destination/(name+'.png'),dpi=220);plt.close(fig)


CSS='''
@font-face {font-family:Thai;src:url(fonts/NotoSansThai-Regular.ttf);font-weight:normal;}
@font-face {font-family:Thai;src:url(fonts/NotoSansThai-Bold.ttf);font-weight:bold;}
@font-face {font-family:Sans;src:url(fonts/NotoSans-Regular.ttf);font-weight:normal;}
@font-face {font-family:Sans;src:url(fonts/NotoSans-Bold.ttf);font-weight:bold;}
body {font-family:Thai, Sans, sans-serif;font-size:11.1pt;line-height:1.5;color:#17324B;margin:0;}
h1 {font-size:23pt;line-height:1.26;margin:0 0 11pt;color:#17324B;}
h1.cover {font-size:27pt;line-height:1.23;margin-top:10pt;}
h2 {font-size:13.4pt;line-height:1.3;margin:14pt 0 6pt;}
h3 {font-size:11.3pt;line-height:1.35;margin:10pt 0 5pt;}
p {margin:0 0 8pt;}
.eyebrow {font-family:Sans;font-size:8.4pt;color:#147D72;letter-spacing:1.3pt;margin:0 0 13pt;}
.subtitle {font-size:11pt;color:#61788B;}
.small {font-size:9.2pt;line-height:1.43;}
.caption {font-size:8.0pt;line-height:1.4;color:#64798B;margin:6pt 0 9pt;}
.file {font-family:Sans;font-size:7.7pt;color:#426984;}
.green {color:#147D72;}.red {color:#B7473A;}.muted {color:#64798B;}
.notice {padding:10pt 12pt;background-color:#EDF5F3;border-left:3pt solid #147D72;margin:9pt 0 12pt;}
.warn {padding:10pt 12pt;background-color:#FFF3EE;border-left:3pt solid #B7473A;margin:9pt 0 12pt;}
table {border-collapse:collapse;width:523pt;margin:8pt 0 9pt;font-size:9.4pt;line-height:1.35;}
th {background-color:#E8EFF4;border-bottom:1pt solid #17324B;font-weight:bold;text-align:right;padding:6pt 5pt;}
th:first-child {text-align:left;}td {padding:5pt 5pt;border-bottom:.6pt solid #DEE6EB;vertical-align:top;}
td.num {text-align:right;white-space:nowrap;}td.left {text-align:left;}
table.compact {font-size:8.8pt;}table.compact td {padding:4.2pt 4pt;}table.compact th {padding:4.5pt 4pt;}
.dense-table-layout table.compact td {padding:3.4pt 4pt;}
.dense-table-layout h2 {margin-top:10pt;}
tr.highlight td {background-color:#FFF1EA;}
table.hero {border-top:2pt solid #147D72;border-bottom:1pt solid #DDE6E9;margin:13pt 0;}
table.hero td {background:white;border:0;padding:9pt 9pt 9pt 0;text-align:left;}
.big {font-family:Sans;font-size:30pt;color:#147D72;font-weight:bold;line-height:1.2;margin:0 0 5pt;}
.label {font-size:9.6pt;color:#456174;}
img {max-width:100%;}pre {font-family:Sans;font-size:8.5pt;line-height:1.65;padding:11pt 12pt;background:#EFF3F6;margin:9pt 0;}
ul,ol {padding-left:17pt;margin:5pt 0 9pt;}li {margin-bottom:5pt;}
a {color:#2E6E95;text-decoration:none;}
'''


def pages(bundle,preview=False):
 s=bundle.get('summary',{});d=bundle.get('decision',{});p=bundle['protocol']
 selection=bundle.get('selection',{});v=bundle.get('verification',{});a=bundle.get('audit',{})
 dev=bundle.get('development_decision',{})
 tests=bundle.get('tests',{});repro=bundle.get('repro',{})
 recovery=bundle.get('recovery',{})
 attribution=bundle.get('tail_attribution',{})
 cfg=p.get('controller_configuration',{})
 if not isinstance(cfg,dict):cfg={}
 primary=d.get('primary',{})
 def ov(regime,policy,metric):return dig(s,'regimes',regime,'overall',policy,metric)
 def risk(regime,policy):return dig(s,'regimes',regime,'static_relative_harm',policy,'cvar90')
 def check(name):return dig(d,'advancement_checks',name,'passed')
 def count_checks(name):
  values=dig(d,name,'checks')
  return PENDING if values is None else f'{sum(x["passed"] for x in values)}/{len(values)}'
 def dev_count(name):
  values=dig(dev,name,'checks')
  return PENDING if values is None else f'{sum(x["passed"] for x in values)}/{len(values)}'
 final='delayed_noisy'
 advance=d.get('advancement_passed')
 title='ยังไม่สรุปผล: รอ validation ที่ล็อกแล้ว' if preview else ('ผ่านเกณฑ์ที่ประกาศครบทุกข้อ' if advance else 'ไม่ผ่านเกณฑ์ที่ประกาศครบทุกข้อ')
 stamp='LAYOUT PREVIEW / NO VALIDATION RESULTS' if preview else bundle['date_label']
 main_class='notice' if advance else 'warn'
 primary_reduction=primary.get('risk_reduction')
 primary_color=GRAY if primary_reduction is None else (TEAL if primary_reduction>=0 else RED)
 risk_pct=None if primary_reduction is None or primary.get('baseline_risk',0)<=1e-6 else 100*primary_reduction/primary['baseline_risk']
 expected='ขอบเขตที่ประกาศ' if preview else 'ขอบเขตที่ตรวจแล้ว'
 cost_rows=[[REGIME_LABELS[r],num(ov(r,'v4_hybrid','moves'),2),
             num(ov(r,'v4_hybrid','resource_minutes_lost'),1),
             num(ov(r,'v4_hybrid','startup_capacity_minutes_lost'),1)] for r in REGIMES]
 ablation_intervals={policy:dig(s,'regimes',final,'static_harm_comparisons','v4_hybrid',policy,'cvar90','risk_reduction_ci95')
                     for policy in ('v4_no_risk','v4_no_recovery')}
 if all(value is not None and value[0]<=0<=value[1] for value in ablation_intervals.values()):
  ablation_interpretation='ผล R5 ในหน้า 9 มี CI ของผลได้ด้าน CVaR90 คร่อมศูนย์ทั้งสอง ablation และรุ่น no recovery มีค่าจุดต่ำกว่า hybrid เล็กน้อย จึงยังระบุไม่ได้ว่า risk penalty หรือ recovery แต่ละส่วนทำให้เกิดการลดลงมากเมื่อเทียบ V3; ผลเทียบ V3 เป็นการเปลี่ยนหลายองค์ประกอบพร้อมกัน'
 else:
  ablation_interpretation='ผล ablation ต้องอ่านทั้งค่าจุดและ CI การเทียบ V3 เปลี่ยนหลายองค์ประกอบพร้อมกัน จึงไม่ใช้ผลรวมดังกล่าวยืนยันสาเหตุจาก risk penalty หรือ recovery แต่ละส่วนโดยลำพัง'
 family_attribution=dig(attribution,'regimes',final,'global_static_relative_cvar90','v4_hybrid','families',default=[])
 if family_attribution:
  tail_names={'generated_switching_fault':'คอขวดสลับสังเคราะห์','shifting_bottleneck':'คอขวดเปลี่ยนจุด Flash','generated_sustained_overload':'งานเกินกำลังสังเคราะห์'}
  positive=[item for item in family_attribution if item['global_cvar90_contribution']>1e-6]
  tail_text='ส่วนหาง R5 ของ hybrid มาจาก '+', '.join(tail_names.get(item['group'],GROUP_NAMES[item['group']])+' '+pct(item['share_of_global_cvar90_pct']) for item in positive[:3])+'. คำนวณด้วย cutoff และตัวหารส่วนหางเดียวกันทุกครอบครัว เป็นการแจกแจงผลเชิงพรรณนา ไม่ใช่การระบุสาเหตุ'
 else:
  tail_text='รอการแจกแจงส่วนหางจากแถวผลที่ตรวจแล้ว โดยใช้ cutoff และตัวหารส่วนหางเดียวกันทุกครอบครัว การแจกแจงนี้เป็นคำอธิบายผล ไม่ใช่การระบุสาเหตุ'
 p1=f'''
 <p class="eyebrow">ADAPTIVE NERVOUS SYSTEM / RESEARCH NOTE 04</p>
 <h1 class="cover">ผสานการจัดสมดุล<br>กับการป้องกันความเสียหาย</h1>
 <p class="subtitle">รายงาน v0.4.0 | {stamp}<br>
 {expected}: 704 สถานการณ์ / 29,568 episodes / 3,300,864 นาทีจำลอง</p>
 <table class="hero"><tr><td style="width:52%"><p class="big" style="color:{primary_color}">{num(primary_reduction)}</p>
 <p class="label">CVaR90 ลดลงเทียบ V3 ใน R5<br>หน่วยงาน / อัตราลดลง {pct(risk_pct)}</p></td>
 <td><p class="big" style="color:#7651A5">{pct(relative(ov(final,'v4_hybrid','mean_deficit'),ov(final,'capacity_balance','mean_deficit')))}</p>
 <p class="label">การเปลี่ยนค่าเฉลี่ยเทียบ Balance ใน R5<br>ค่าบวกหมายถึงงานที่ยังไม่สำเร็จมากขึ้น</p></td></tr></table>
 <div class="{main_class}"><b>{title}</b><br>
 เกณฑ์หลัก CVaR90: {status(primary.get('passed'))} | เกณฑ์ค่าเฉลี่ยทั้งสองวิธีฐาน: {status(check('mean_noninferiority_every_regime'))}<br>
 ผลรวมเกณฑ์ไปต่อ: {status(advance)}</div>
 <h2>สามคำถามที่การทดลองนี้ตอบ</h2>
 {table(['เป้าหมาย','สิ่งที่ตรวจจริง'],[
  ['ลดความเสียหายส่วนหาง','เทียบ CVaR90 ที่อ้างอิง Static กับ V3, V2 และ Capacity balance แยกทั้ง 6 ระดับ'],
  ['รวม Balance กับการควบคุมความเสี่ยง','ข้อเสนอจัดสมดุลแข่งกับการถือเดิม ผ่านคะแนนความเสี่ยงที่มีขนาดจำกัด และกลไกฟื้นจากการค้างอยู่กับการจัดสรรเดิม'],
  ['เพิ่มความสมจริง','รวมเวลาย้าย การปัดทรัพยากรเป็นจำนวนเต็ม บัฟเฟอร์จำกัด การส่งต่องาน การเริ่มทำงาน และข้อมูลล่าช้ามี noise']
 ],small=True)}
 <p>ผลหลักใช้ระดับ R5 ซึ่งรวมสมมติฐานทั้งหมด ขอบล่างและบนของ 95% CI สำหรับ
 CVaR90 ของ V3 ลบ V4 คือ <b>{interval(primary.get('risk_reduction_ci95'))}</b> หน่วย
 ค่าลดลงนี้วัดความเสียหายเทียบ Static ในแต่ละกรณี ไม่ใช่ค่าเฉลี่ยของงานค้างทุกกรณี</p>
 {table(['R5: mean เทียบ','เปลี่ยนจาก baseline','CI ขอบล่างของ slack'],[
  [NAMES[baseline],pct(relative(ov(final,'v4_hybrid','mean_deficit'),ov(final,baseline,'mean_deficit'))),
   num((dig(s,'regimes',final,'comparisons','v4_hybrid',baseline,'mean_noninferiority','allowance_slack_ci95') or [None])[0])]
  for baseline in ('capacity_v2','capacity_balance')],small=True)}
 {caption('R5: mean ของ V4 สูงกว่า Balance '+num(relative(ov(final,'v4_hybrid','mean_deficit'),ov(final,'capacity_balance','mean_deficit')),4)+'% แม้ผ่านส่วนเผื่อ 5%; slack = 1.05 x mean(baseline) - mean(V4) และขอบล่างต้องไม่ติดลบ')}
 <p class="small">การดีกว่า V3 อย่างเดียวไม่เพียงพอ: ยังต้องรักษาค่าเฉลี่ยเมื่อเทียบทั้ง V2 และ Balance
 ไม่เพิ่มงานตกหล่นหรืองานที่เหลือท้ายช่วง และผ่านการตรวจความถูกต้อง รายงานเก็บผลที่ไม่ผ่านไว้ด้วย</p>
 <p class="small"><b>ขอบเขต:</b> เป็นหลักฐานจากงานและกลไกจำลองที่ระบุ การผ่านเกณฑ์เป็นเหตุผลให้ทดลองต่อ
 ไม่ใช่การอนุมัติควบคุมทรัพยากรในระบบจริง</p>
 {evidence(['v4_results/validation/summary.json','decision.json','v4_protocol.json'])}
 '''
 p2=f'''
 <p class="eyebrow">01 / HYBRID CONTROLLER</p>
 <h1>เลือกการย้าย พร้อมคิดต้นทุนของการอยู่นิ่ง</h1>
 <p>ตัวควบคุมใช้ประวัติ อัตราที่ประมาณได้ และนาฬิกาการย้ายชุดเดียวกัน
 ทุกครั้งประเมินทั้งการถือเดิมและรายการย้ายที่ทำได้จริง ข้อเสนอจาก Balance
 มุ่งจัดสรรตามส่วนกลับของอัตราที่ประมาณได้ เพื่อให้กำลังของสามขั้นใกล้กัน</p>
 {table(['ส่วนประกอบ','การทำงาน'],[
  ['ข้อเสนอจาก Balance','เลือกการย้ายใน catalog ที่เข้าใกล้เป้าหมายจัดสมดุล และให้ความสำคัญเมื่อคะแนนบวกไม่น้อยกว่า 90% ของตัวเลือกที่ดีที่สุด'],
  ['คะแนนผลได้และความเสี่ยง','มองล่วงหน้า 16 นาที: ผลได้เฉลี่ยจาก 9 แบบพื้นฐาน หัก risk weight คูณค่าเฉลี่ยความเสียหายด้านบวกใน 25% มุม stress ที่แย่ที่สุด จากอีก 24 แบบ'],
  ['ข้อจำกัดทางกายภาพ','งบ 207 หน่วย สำรองอย่างน้อย 10 ต่อขั้น warmup 5 นาที และ dwell 8 นาที; จำกัดจำนวนทรัพยากรที่ย้ายรวมตามค่าที่เลือกจาก grid'],
 ],small=True)}
 <div class="notice"><b>ค่าที่เลือกหลัง development:</b> risk weight = {num(cfg.get('risk_weight'),1)};
 ย้ายทรัพยากรรวมได้สูงสุด {num(cfg.get('maximum_transfer'),0)} หน่วยต่อการย้าย<br>
 ต้นทุนพยากรณ์ = deficit เฉลี่ย + 0.5 x deficit ท้ายหน้าต่าง ไม่มี terminal veto แยกอีกชั้น</div>
 <h2>สองกลไกฟื้นจากการจัดสรรที่ค้างอยู่</h2>
 <p><b>เมื่อแรงกดดันสะสม:</b> เมื่อคิวที่สังเกตได้รวมเกิน 10 และลดลงไม่เกิน max(10, 2% ของคิวก่อนหน้า)
 พร้อมความไม่สมดุลของการจัดสรร จึงรวมกรณีคิวทรงตัวหรือลดช้าไว้ด้วย
 ลดทั้งน้ำหนักความเสี่ยงและเกณฑ์ยอมย้ายตามระยะเวลาที่สะสม โดยไม่ลดต่ำกว่า 0.25 เท่า
 เกณฑ์ตั้งต้นคือ max(10, 0.01 x งานเข้าที่สังเกตได้เฉลี่ย) จึงผูกกับงานเข้า ไม่ได้เพิ่มตามกองงานค้างโดยตรง</p>
 <p><b>เมื่อคิวต่ำต่อเนื่อง:</b> หลังสังเกตคิวรวมปลายช่วงไม่เกิน 10 ติดต่อกัน 3 ครั้ง
 อนุญาตการย้ายกลับตาม Balance
 ถ้าค่าเฉลี่ยต้นทุนไม่เพิ่ม และไม่มีแบบพยากรณ์ใดใน 33 แบบเพิ่มต้นทุนเกิน 10 หน่วย
 จึงทดสอบการกลับสู่สมดุลได้แม้ผลได้จากการลดคิวขณะนั้นมีน้อย</p>
 <h2>ข้อมูลเก่าไม่กลายเป็นข้อห้ามตลอดไป</h2>
 <p class="small">จุดอ้างอิงอัตราที่วัดขณะทำงานเต็มกำลังหมดอายุหลัง 8 นาที ใช้หลักฐานล่าสุดในหน้าต่าง 5 ครั้ง
 การหมดอายุของ anchor เดิมอาจทำให้ช่วงแคบลง ส่วนข้อมูลบางหรือช้าขยายแถบสมมติฐานอีกทางหนึ่ง
 การไหลที่น้อยเพราะงานไม่พอเป็นเพียงขอบเขต ไม่ใช่กำลังผลิตที่วัดได้แน่นอน
 การกลับเข้าหา prior เป็นสมมติฐานของ estimator ไม่ใช่หลักฐานว่าระบบฟื้นจริงแล้ว</p>
 <div class="warn small"><b>25% ในคะแนน stress ต่างจาก CVaR90 ของผลทดลอง:</b>
 แบบพยากรณ์เป็นกรณีสมมติทางวิศวกรรม ไม่ใช่ความน่าจะเป็นที่สอบเทียบแล้ว
 ไม่มีการอ้างว่าคะแนนนี้รับประกันว่าจะลด CVaR90 ของผลจริง</div>
 <p class="small">{ablation_interpretation}</p>
 {evidence(['v4_policy.py','v4_selected_config.json','v4_protocol.json: controller_semantics'])}
 '''
 p3=f'''
 <p class="eyebrow">02 / NESTED REALISM</p>
 <h1>เพิ่มกลไกหกชั้น แล้วรายงานแยกกัน</h1>
 <p>แต่ละระดับเพิ่มกลไกต่อจากระดับก่อน ใช้ครอบครัวงานและ seed ที่จับคู่กัน
 จึงเปรียบเทียบความไวต่อกลไกได้ แต่ไม่ถือว่าทั้งหกระดับเป็นการทดลองอิสระ
 และไม่รวมเป็นเปอร์เซ็นต์ประสิทธิภาพตัวเดียว</p>
 {table(['ระดับ','สิ่งที่เพิ่ม','ความหมาย'],[
  ['R0: legacy instant','กลไกเดิม','งานเป็น fluid; ผลของขั้นก่อนเข้าสู่ขั้นถัดไปได้ในนาทีเดียวกัน; ย้ายทรัพยากรได้ทันที'],
  ['R1: transit 3','เวลาย้าย 3 นาที','ผู้ให้เสียทรัพยากรทันที ผู้รับเริ่มใช้ก่อนบริการที่ t+3; ทรัพยากรที่อยู่ระหว่างทางทำงานไม่ได้'],
  ['R2: integer resources','จัดสรรเป็นจำนวนเต็ม','ปัดเป้าหมายด้วย stable largest remainder โดยรักษางบรวม; งานและอัตราบริการยังเป็น fluid'],
  ['R3: finite pipeline','ส่งต่องาน + บัฟเฟอร์','งานใช้หนึ่งช่วงบริการในการส่งต่อระหว่างขั้น; ความจุคิว 60,000 / 40,000 / 40,000; งานล้นถูกบันทึกเป็น dropped'],
  ['R4: startup','กำลังเริ่มต้นไม่เต็ม','ทรัพยากรที่เพิ่งมาถึงทำงานได้ 50% และ 75% ในสองช่วงแรกที่เริ่มใช้งาน แล้วจึงครบ 100%'],
  ['R5: delayed noisy','ข้อมูลช้าและคลาดเคลื่อน','ข้อมูลล่าช้า 2 นาที; คูณด้วย noise แบบ lognormal ที่ log-sigma 5% และมีค่าเฉลี่ยตัวคูณ 1']
 ],small=True)}
 <div class="warn"><b>ตัวเลขทั้งหมดเป็น stress assumptions ที่กำหนดขึ้น:</b>
 D=3, startup 50%/75%, sensor lag 2, noise 5% และขนาดบัฟเฟอร์
 ไม่ใช่ค่าที่วัดจากระบบจริง จึงยังบอกความเหมาะสมของค่าตั้งระบบจริงไม่ได้</div>
 <h2>แยกสิ่งที่รู้ได้จากความจริงของ simulator</h2>
 <p class="small">นโยบายรู้กลไกที่ประกาศและคำสั่งทรัพยากรของตน ได้เฉพาะ observation ตามระดับนั้น
 ไม่ได้เห็น productivity จริง อนาคต รายชื่อสถานการณ์ หรือผล Static ที่จำลองแยกไว้
 อัตรา flow ต้องหารด้วย resource exposure ของช่วงต้นทางเดียวกัน รวมผล startup
 การหารด้วยจำนวนทรัพยากรปัจจุบันหลังย้ายจะสร้างอัตราเทียม</p>
 <p class="small">ต้นทุนทรัพยากรถูกแยกเป็นเวลาของทรัพยากรที่ยังเดินทาง และกำลังที่หายช่วง startup
 ทั้งคู่มีหน่วยทรัพยากร x นาที ไม่มีการตีเป็นต้นทุนทางการเงิน</p>
 <h2>ต้นทุนทรัพยากรที่วัดได้ของ V4 ใน H เดิม</h2>
 {table(['ระดับ','ครั้งย้าย','ระหว่างทาง','startup'],cost_rows,small=True)}
 {caption('ระหว่างทาง = pending resources; startup = active ลบ effective resources จึงแยกกันและไม่นับซ้ำ; ผลช่วงติดตามแยกไว้ใน JSON')}
 {evidence(['v4_environment.py','v4_scenarios.py','v4_simulation.py','scenario_manifest.json'])}
 '''
 p4=f'''
 <p class="eyebrow">03 / METRIC AND EXPERIMENT UNIT</p>
 <h1>งานที่ทิ้งต้องไม่ทำให้คะแนนดูดีขึ้น</h1>
 <div class="notice"><b>Demand deficit ทุกนาที</b><br>
 งานเสนอเข้ารวม - งานสำเร็จรวม<br>
 = งานใน service queues + งานระหว่างขั้น + งานตกหล่นสะสม</div>
 <p>ตัวชี้วัดหลักของแต่ละกรณีคือค่าเฉลี่ย deficit เมื่อสิ้นแต่ละนาทีภายใน H เดิม
 ไม่หารด้วยงานที่รับเข้าบัฟเฟอร์สำเร็จหรืองานที่ทำเสร็จ จึงไม่ให้รางวัลกับการปฏิเสธหรือทิ้งงาน</p>
 <p class="small"><b>ตัวอย่างนิยาม ไม่ใช่ผลทดลอง:</b> เสนองาน 100 หน่วย สำเร็จ 70 เหลือในระบบ 20 และทิ้ง 10
 deficit เท่ากับ 30 ไม่ใช่ 20 ต่อให้คิวว่างภายหลัง งานที่ทิ้งยังไม่กลายเป็นงานสำเร็จ</p>
 {table(['ขอบเขต','การนับ'],[
  ['หน่วยทดลอง','11 รูปแบบ x 64 seeds = 704 สถานการณ์; แต่ละกรณีใช้ H=60 หรือ 96 นาทีตามครอบครัว'],
  ['ความสมจริงและนโยบาย','6 regimes x 7 นโยบายที่ deterministic = 29,568 rollouts; 4,224 คู่สถานการณ์-ระดับ'],
  ['ช่วงคะแนนเดิม','รวม 2,354,688 นาที; เฉลี่ยในแต่ละกรณีก่อนให้น้ำหนักกรณีเท่ากัน'],
  ['ช่วงติดตาม','อีก 32 นาทีต่อ rollout รวม 946,176 นาที; งานเข้าใหม่เป็นศูนย์และ productivity คงค่าจากนาทีสุดท้ายเดิม'],
  ['แหล่งข้อมูล','5 ครอบครัวจาก Flash Sale เดิม และ 6 ครอบครัวสังเคราะห์แยก; รายงานทั้งสองแหล่งและทุกครอบครัว']
 ],small=True)}
 <h2>ความเสี่ยงส่วนหางและความไม่แน่นอน</h2>
 <p>สำหรับแต่ละกรณี h = max(M ของนโยบาย - M ของ Static, 0)
 CVaR90 คือค่าเฉลี่ยของ h ในส่วนที่แย่ที่สุด 10% ของทุกกรณี
 ใช้น้ำหนักเศษที่ขอบเพื่อให้ส่วนหางมีมวล 10% พอดี และรวมกรณีที่เสียหายศูนย์ด้วย</p>
 <p class="small">ช่วงความเชื่อมั่นใช้ paired bootstrap 2,000 ครั้งภายในแต่ละครอบครัว
 รักษาคู่นโยบายและเทียบค่า risk functional ทั้งสองด้วยตัวอย่างเดียวกัน
 การให้น้ำหนักกรณีเท่ากันไม่ได้ทำให้ขนาดงานเท่ากัน กรณีงานเกินกำลังที่มี deficit สูงยังมีอิทธิพลต่อผลรวมได้มาก</p>
 <p class="small">CI สะท้อนความผันแปรจาก seed ในครอบครัวสังเคราะห์ที่กำหนดไว้
 ไม่ครอบคลุมความผิดพลาดของ simulator หรือความเป็นไปได้ของ workload แบบอื่น
 การทำซ้ำไฟล์ตรงทุกไบต์พิสูจน์การทำซ้ำได้ ไม่ได้เพิ่มขนาดตัวอย่างอิสระ</p>
 <div class="notice small"><b>ส่วนเผื่อ 5% ของค่าเฉลี่ย:</b> ตรวจ CI ของ 1.05 x mean(baseline) - mean(V4)
 เทียบทั้ง V2 และ Balance โดยตรง จึงนิยามได้แม้ baseline เป็นศูนย์
 นี่ไม่ใช่ส่วนเผื่อ CVaR90 และไม่เกี่ยวกับ noise 5% ในแบบจำลอง sensor</div>
 <h2>การระบายงานกับการทำงานครบเป็นคนละตัวชี้วัด</h2>
 <p class="small">Drain time วัดเฉพาะ retained queues + pipeline ซึ่งระบายได้จริง
 ถ้าทิ้งงานไปแล้ว deficit อาจไม่เป็นศูนย์แม้ retained work ว่าง
 เวลาระบายถูกจำกัดที่ 32 นาทีและรายงานสัดส่วนที่ยังไม่หมดด้วย
 นโยบายไม่รู้ล่วงหน้าว่าช่วงคะแนนจะจบหรือช่วงติดตามจะเริ่มเมื่อใด</p>
 {evidence(['v4_metrics.py','v4_protocol.json: scoring/analysis','v4_acceptance_predeclared.json'])}
 '''
 trial_rows=[]
 trials=selection.get('ranked_trials',[])
 if trials:
  for i,trial in enumerate(trials,1):
   rank=trial['rank'];config=trial['configuration']
   trial_rows.append([str(i),num(config['risk_weight'],1),num(config['maximum_transfer'],0),num(rank[0],0),num(rank[1],2),num(rank[2],0),num(rank[3],2)])
 else:
  for weight in (.1,.3,.6):
   for cap in (20,30):trial_rows.append([PENDING,num(weight,1),str(cap),PENDING,PENDING,PENDING,PENDING])
 development_note=''
 if trials:
  exceeding=[REGIME_LABELS[r] for r,item in trials[0]['per_regime'].items() if item['normalized_risk_ratio']>1+1e-6]
  if exceeding:
   development_note=f'<p class="small"><b>ข้อที่ยังไม่สำเร็จใน development:</b> ตัวเลือกอันดับหนึ่งยังมี normalized risk ratio สูงกว่า 1 ใน {", ".join(exceeding)} โดยค่าสูงสุดคือ {num(trials[0]["rank"][1],5)} จึงยังไม่ผ่าน risk point checks ทั้งหมด คงตัวเลือกตามลำดับที่ประกาศไว้ ไม่แก้เพื่อให้ผ่านย้อนหลัง</p>'
 p5=f'''
 <p class="eyebrow">04 / DEVELOPMENT AND FREEZE</p>
 <h1>เลือกจากหกค่าที่ประกาศไว้ และเก็บทุกผล</h1>
 <p>ใช้ 88 กรณี seed 2000-2007 ที่เคยเห็นแล้วจาก v0.3 เป็น development อย่างเปิดเผย
 ลอง risk weight 0.1, 0.3, 0.6 กับเพดานทรัพยากรที่ย้ายรวมต่อครั้ง 20 และ 30 หน่วย รวม 6 ค่า
 ไม่เปลี่ยนเกณฑ์ validation ตามผล development</p>
 {table(['อันดับ','weight','cap','ผิด mean','risk ratio','ผลรวม risk','ย้ายเฉลี่ย'],trial_rows,highlight=0 if trials else None,small=True)}
 {caption('ผิด mean = จำนวนข้อที่ค่าเฉลี่ยเกิน 105% ของ V2/Balance จาก 12 ข้อ; risk ratio เป็นค่าสูงสุดใน 6 ระดับเทียบ baseline ที่มี CVaR90 ต่ำสุด โดยตั้งตัวหารขั้นต่ำ 1 หน่วย')}
 <div class="notice"><b>ค่าที่เลือก:</b> {html.escape(selection.get('selected_label',PENDING))}<br>
 risk weight {num(cfg.get('risk_weight'),1)} และ เพดานย้ายรวม {num(cfg.get('maximum_transfer'),0)} หน่วย
 การเป็นตัวเลือกอันดับหนึ่งไม่ใช่หลักฐานว่าผ่านเกณฑ์ validation</div>
 {development_note}
 <h2>ลำดับการจัดอันดับที่ล็อกไว้</h2>
 <p class="small">เรียงตามจำนวนข้อ mean-margin ที่ไม่ผ่านก่อน ตามด้วย risk ratio ที่แย่ที่สุด
 ผลรวม CVaR90 ทั้งหกระดับ จำนวนย้ายเฉลี่ย แล้วใช้ weight/cap จากน้อยไปมากเพื่อแก้เสมอ
 ผลรวมข้ามระดับนี้ใช้เลือกจาก development เท่านั้น ไม่ใช้เป็น headline ของ validation</p>
 <p class="small">baseline ทั้งสี่ประเมินครั้งเดียวต่อระดับ จากนั้นเก็บทุก candidate ทุกระดับ
 รวม 60 งานและ 5,280 rollouts ของกรณีที่รู้จักแล้ว ไม่ใช่หลักฐานอิสระของ validation</p>
 <h2>ตรวจค่าที่เลือกครบเจ็ดนโยบายอีกครั้ง</h2>
 <p class="small">ผล development 3,696 rollouts: CI ของเกณฑ์หลัก {interval(dig(dev,'primary','risk_reduction_ci95'))};
 risk point ผ่าน {dev_count('risk_point_nonworsening')}, mean CI ผ่าน {dev_count('mean_noninferiority')},
 loss/endpoints ผ่าน {dev_count('loss_and_endpoint_point_nonworsening')}
 และตรวจ underload {num(dig(dev,'underload','episodes_checked'),0)} กรณี
 ยังใช้ 88 กรณีเดิม และการตรวจ integrity ก่อน freeze ไม่ใช่หลักฐานยืนยันสุดท้าย</p>
 <h2>สิ่งที่แช่แข็งก่อนเปิด seed ใหม่</h2>
 <p class="small">ตรึงตัวควบคุม ค่าที่เลือก environment observation ตัวชี้วัด bootstrap evaluator tests
 และ scenario manifest ด้วย hashes ก่อน seed 4000-4063 เก็บ plan, snapshot และผลทุกค่าไว้
 เกณฑ์ไม่เปลี่ยนตามผล การแก้หลังเห็น validation ต้องเป็นการทดลองรุ่นใหม่</p>
 {evidence(['development_grid/selection.json','plan.json','source_snapshot/','development/decision.json','v4_protocol.json'])}
 '''
 risk_rows=[]
 for r in REGIMES:
  risk_rows.append([REGIME_LABELS[r]]+[num(risk(r,x),1) for x in ('capacity_v2','capacity_balance','v3_full','v4_hybrid')])
 p6=f'''
 <p class="eyebrow">05 / STATIC-RELATIVE TAIL RISK</p>
 <h1>CVaR90 ลดจริงหรือย้ายความเสียหายไปที่อื่น</h1>
 <p>ตารางและกราฟใช้ความเสียหายเทียบ Static ของแต่ละนโยบายในกรณีเดียวกัน
 ค่าต่ำกว่าดีกว่า แต่ไม่อาจตีความว่าไม่มีกรณีเสียหายหรือมีความเสี่ยงของระบบจริงเท่านี้</p>
 {table(['ระดับ','V2','Balance','V3 full','V4 hybrid'],risk_rows,small=True)}
 <img src="figures/risk_by_regime.png" style="width:523pt;height:157pt">
 {caption('เส้นเชื่อมช่วยติดตามแต่ละนโยบายตามระดับ R0-R5; แกนนอนเป็นหมวดกลไก ไม่ใช่เวลาหรือสเกลความยากที่วัดได้')}
 <div class="{main_class}"><b>เกณฑ์หลัก R5:</b> {status(primary.get('passed'))}<br>
 R(V3) = {num(primary.get('baseline_risk'))}; R(V4) = {num(primary.get('candidate_risk'))}<br>
 ผลต่าง R(V3)-R(V4) = {num(primary_reduction)}; 95% CI {interval(primary.get('risk_reduction_ci95'))}</div>
 <p>อีกเงื่อนไขคือค่า risk ของ V4 ต้องไม่เกิน V3, V2 และ Balance ในทุกระดับ
 ผ่านตามค่าประมาณจุด <b>{count_checks('risk_point_nonworsening')}</b> ข้อ
 เงื่อนไขนี้ไม่มีส่วนเผื่อ 5% และไม่เรียกว่า statistical noninferiority ของความเสี่ยง</p>
 <h2>กลุ่มที่ยังสร้างความเสียหายในส่วนหาง</h2>
 <p class="small">{tail_text}</p>
 <p class="small">ใน R5 จำนวนกรณีที่แย่กว่า Static: V4 {num(dig(s,'regimes',final,'static_relative_harm','v4_hybrid','count'),0)}/704;
 V3 {num(dig(s,'regimes',final,'static_relative_harm','v3_full','count'),0)};
 V2 {num(dig(s,'regimes',final,'static_relative_harm','capacity_v2','count'),0)};
 Balance {num(dig(s,'regimes',final,'static_relative_harm','capacity_balance','count'),0)}</p>
 {evidence(['validation/summary.json: static_relative_harm/static_harm_comparisons','decision.json','tail_attribution.json'])}
 '''
 mean_rows=[]
 for r in REGIMES:
  mean_rows.append([REGIME_LABELS[r]]+[num(ov(r,x,'mean_deficit'),0) for x in ('capacity_v2','capacity_balance','v3_full','v4_hybrid')])
 family_rows=[]
 for group,label in GROUP_NAMES.items():
  part=dig(s,'regimes',final,'by_group',group,default={})
  vals=[dig(part,'overall',x,'mean_deficit') for x in ('static','v4_hybrid')]
  comp=dig(part,'comparisons','v4_hybrid','static',default={})
  counts=PENDING if not comp else f'{comp["wins"]}/{comp["ties"]}/{comp["losses"]}'
  family_rows.append([label,num(vals[0],0),num(vals[1],0),pct(comp.get('reduction_pct')),counts])
 source_rows=[]
 for key,label in [('legacy_flash','Flash เดิม'),('generated_synthetic','สังเคราะห์แยก')]:
  part=dig(s,'regimes',final,'source_slices',key,default={})
  source_rows.append([label]+[pct(dig(part,'comparisons','v4_hybrid',base,'reduction_pct')) for base in ('static','capacity_v2','capacity_balance')])
 p7=f'''
 <p class="eyebrow">06 / MEANS, SOURCES AND FAMILIES</p>
 <h1>รักษาค่าเฉลี่ย และเปิดผลครบทุกครอบครัว</h1>
 {table(['ระดับ','V2','Balance','V3 full','V4 hybrid'],mean_rows,small=True)}
 <p class="small">เกณฑ์ 5% เทียบกับทั้ง V2 และ Balance แยกกัน ใช้ขอบล่างของ CI ของ
 1.05 x mean(baseline) - mean(V4) ไม่ใช้การเลือก baseline ที่ดูดีที่สุดภายหลัง
 ผ่าน <b>{count_checks('mean_noninferiority')}</b> จาก 12 ข้อ</p>
 <h2>ครบทั้ง 11 ครอบครัวในระดับหลัก R5</h2>
 {table(['รูปแบบ','Static','V4','ลดลง','ชนะ/เสมอ/แพ้'],family_rows,small=True)}
 <h2>แยกแหล่งที่มาของ workload ใน R5</h2>
 {table(['แหล่ง','ลดจาก Static','ลดจาก V2','ลดจาก Balance'],source_rows,small=True)}
 <p class="small">ค่าลดลงติดลบหมายถึง V4 มี deficit มากกว่า comparator
 ถ้า baseline เป็นศูนย์จะไม่กำหนดอัตราส่วน แต่ผลต่างจำนวนงานและจำนวนกรณียังใช้ได้
 งานเกินกำลังมีขนาดกองงานสูง จึงอาจมีอิทธิพลต่อเปอร์เซ็นต์รวมมาก แม้น้ำหนักกรณีเท่ากัน</p>
 {evidence(['validation/summary.json: overall/comparisons/by_group/source_slices','validation/episodes.csv'])}
 '''
 loss_rows=[];tail_rows=[];end_rows=[]
 for r in REGIMES:
  label=REGIME_LABELS[r]
  loss_rows.append([label]+[num(ov(r,x,'total_dropped'),0) for x in ('capacity_v2','capacity_balance','v3_full','v4_hybrid')])
  tail_rows.append([label,num(ov(r,'v4_hybrid','tail_auc_deficit'),0),
                    pct(relative(ov(r,'v4_hybrid','tail_auc_deficit'),ov(r,'capacity_v2','tail_auc_deficit'))),
                    pct(relative(ov(r,'v4_hybrid','tail_auc_deficit'),ov(r,'capacity_balance','tail_auc_deficit')))])
  end_rows.append([label,num(ov(r,'v4_hybrid','end_deficit'),0),
                   pct(relative(ov(r,'v4_hybrid','end_deficit'),ov(r,'capacity_v2','end_deficit'))),
                   pct(relative(ov(r,'v4_hybrid','end_deficit'),ov(r,'capacity_balance','end_deficit')))])
 p8=f'''
 <p class="eyebrow">07 / LOSS, ENDPOINTS AND TRANSFER COST</p>
 <h1>ตรวจงานที่ทิ้งและภาระหลังช่วงคะแนน</h1>
 <h2>งานตกหล่นเฉลี่ยรวม H + 32 นาที</h2>
 {table(['ระดับ','V2','Balance','V3 full','V4 hybrid'],loss_rows,small=True)}
 <h2>Deficit ที่ขอบเขต H เดิม</h2>
 {table(['ระดับ','V4: หน่วยงาน','เปลี่ยนจาก V2','เปลี่ยนจาก Balance'],end_rows,small=True)}
 <h2>พื้นที่ใต้กราฟ deficit ช่วงติดตาม 32 นาที</h2>
 {table(['ระดับ','V4: งาน x นาที','เปลี่ยนจาก V2','เปลี่ยนจาก Balance'],tail_rows,small=True)}
 {caption('ค่าการเปลี่ยนเป็นบวกหมายถึงมี deficit หรือ tail AUC มากขึ้น; อัตราส่วนไม่กำหนดเมื่อ baseline เป็นศูนย์')}
 <p class="small">ใน R3-R5 ผลต่าง tail AUC ของ V4 เทียบ Balance เท่ากับ 32 x ผลต่างงานตกหล่นรวม
 งานที่ทิ้งเพิ่มยังอยู่ใน deficit ตลอดช่วงติดตาม ดังนั้นผลปลายช่วงเหล่านี้เชื่อมกันทางบัญชี
 ไม่ใช่หลักฐานอิสระซ้ำอีกชุด ส่วน tail end และ capped drain time พร้อม censoring อยู่ใน JSON</p>
 {evidence(['validation/summary.json: overall/comparisons.secondary','decision.json: loss_and_endpoint_point_nonworsening'])}
 '''
 criterion_rows=[
  ['หลัก: CVaR90 ใน R5','CI ขอบล่าง R(V3)-R(V4) > 0',status(primary.get('passed'))],
  ['หลักฐาน integrity','tests, hashes, accounting, replay',status(check('integrity'))],
  ['Underload','ไม่มีงานตกหล่นและไม่มีการย้ายทั้งช่วง',status(check('underload_zero_drops_and_moves'))],
 ]
 short_names={'capacity_v2':'V2','capacity_balance':'B','v3_full':'V3'}
 def failed_pairs(section,regime,metric=None):
  records=dig(d,section,'checks')
  if records is None:return PENDING
  labels=[short_names[item['baseline']] for item in records
          if item['regime']==regime and (metric is None or item['metric']==metric) and not item['passed']]
  return '<b class="red">'+'/'.join(labels)+'</b>' if labels else '<span class="green">ผ่าน</span>'
 failure_matrix=[]
 for r in REGIMES:
  failure_matrix.append([REGIME_LABELS[r],failed_pairs('risk_point_nonworsening',r),
                         failed_pairs('mean_noninferiority',r),
                         failed_pairs('loss_and_endpoint_point_nonworsening',r,'end_deficit'),
                         failed_pairs('loss_and_endpoint_point_nonworsening',r,'tail_auc_deficit'),
                         failed_pairs('loss_and_endpoint_point_nonworsening',r,'total_dropped')])
 ablation_rows=[]
 for policy in ('v4_hybrid','v4_no_risk','v4_no_recovery'):
  ablation_rows.append([NAMES[policy],num(risk(final,policy),1),num(ov(final,policy,'mean_deficit'),0),
                        num(ov(final,policy,'moves'),2),
                        'อ้างอิง' if policy=='v4_hybrid' else interval(ablation_intervals[policy])])
 p9=f'''
 <p class="eyebrow">08 / DECLARED DECISION AND ABLATIONS</p>
 <h1>ใช้เกณฑ์เดิม แม้ผลไม่เป็นไปตามเป้า</h1>
 {table(['เกณฑ์','เงื่อนไข','ผล'],criterion_rows,small=True)}
 <h2>คู่เปรียบเทียบที่ไม่ผ่านในแต่ละระดับ</h2>
 {table(['ระดับ','Risk point','Mean CI','H point','Tail point','Drop point'],failure_matrix,small=True)}
 {caption('V2 = Capacity v2; B = Balance; V3 = V3 full. H คือ end deficit, Tail คือ deficit AUC, Drop คืองานทิ้งรวม; คำว่าผ่านหมายถึงผ่านทุกคู่ที่กำหนดในช่องนั้น')}
 <div class="{main_class}"><b>ผลรวม: {status(advance)}</b><br>
 ทุกเงื่อนไขต้องผ่าน ผลที่ดีกว่า V3 ไม่แทนผลเทียบ V2/Balance และไม่ลบเงื่อนไขงานตกหล่น</div>
 <p class="small">Risk point checks ผ่าน {count_checks('risk_point_nonworsening')} ข้อ;
 mean CI checks ผ่าน {count_checks('mean_noninferiority')} ข้อ;
 loss/endpoint point checks ผ่าน {count_checks('loss_and_endpoint_point_nonworsening')} ข้อ
 Underload ตรวจ {num(dig(d,'underload','episodes_checked'),0)} กรณีของ hybrid
 และอนุญาตงานระหว่างขั้นที่เกิดจาก pipeline ตามปกติ</p>
 <h2>ถอดกลไกออกเพื่อดูข้อแลกเปลี่ยน: R5</h2>
 {table(['รุ่น','CVaR90','mean deficit','ย้าย','95% CI ผลได้ hybrid'],ablation_rows,highlight=0,small=True)}
 {caption('CI ของ R(รุ่นถอดกลไก) - R(hybrid); ค่าบวกเข้าข้าง hybrid ทั้งสอง CI ใน R5 คร่อมศูนย์ ไม่ใช่การพิสูจน์ว่าผลเท่ากัน')}
 <p class="small"><b>No risk:</b> ตั้ง risk weight เป็นศูนย์ แต่ยังมี physical/idle checks
 <b>No recovery:</b> ปิด pressure attenuation และ idle rebalance พร้อมกัน จึงไม่แยกผลสองส่วนนี้</p>
 <p class="small">Ablation ใช้ค่าที่เลือกให้ hybrid ร่วมกัน ไม่ได้ปรับแต่ละรุ่นแยกให้ดีที่สุด</p>
 <div class="notice small"><b>แยกหลักฐานสองชนิด:</b> เกณฑ์หลักและ mean noninferiority ใช้ช่วงความเชื่อมั่น
 ส่วน risk nonworsening, dropped work และ endpoints เป็นเงื่อนไขจากค่าประมาณจุด
 tolerance 1e-6 ใช้กับการเทียบเชิงตัวเลข ไม่ใช่ margin ความเสียหายเพิ่มเติม</div>
 {evidence(['v4_acceptance_predeclared.json','validation/decision.json','v4_policy.py: ablations'])}
 '''
 physical=a.get('physical_replay',{})
 observer=a.get('observation_only_replay',{})
 independent_ci=a.get('independent_acceptance_bootstrap',{})
 audit_rows=dig(a,'scope','episodes_checked')
 verify_rows=[
  ['Unit tests',f'{num(tests.get("tests_run"),0)} tests; failures {num(tests.get("failures"),0)}; errors {num(tests.get("errors"),0)}'],
  ['ขอบเขตผลและบัญชี',f'{num(audit_rows,0)} episodes ตรวจต่อชุดผลที่ประกาศ'],
  ['Replay ทางกายภาพ',f'{num(physical.get("traces"),0)} traces / {num(physical.get("minutes"),0)} นาทีที่กำหนดไว้ล่วงหน้า'],
  ['Replay จาก observation',f'{num(observer.get("traces"),0)} traces / {num(observer.get("decisions"),0)} การตัดสินใจ'],
  ['CI ที่คำนวณตรวจอิสระ',f'{num(independent_ci.get("mean_noninferiority_intervals_checked"),0)} mean-NI และ {num(independent_ci.get("paired_static_relative_cvar_intervals_checked"),0)} risk intervals'],
  ['Workflow จริง','42 งานประเมิน + งานรวมผล; จำกัด 3 workers พร้อมกัน'],
  ['Retry / reopen / negative control','ตรวจการลองใหม่ การเปิดงานเดิม และการปฏิเสธแถวผลที่จงใจผิด'],
  ['ทำซ้ำเต็มชุด',f'{num(repro.get("files_compared"),0)} ไฟล์ตรงกันทุกไบต์ หลัง resume การรันทวนที่ขัดจังหวะ'],
 ]
 p10=f'''
 <p class="eyebrow">09 / VERIFICATION, LIMITS AND REUSE</p>
 <h1>หลักฐานที่ตรวจได้ และสิ่งที่ยังไม่ทราบ</h1>
 {table(['การตรวจ','หลักฐาน'],verify_rows,small=True)}
 <p class="small"><b>รันทวนโดยกลับมาทำงานใน Runtime เดิม:</b> เก็บ {num(recovery.get('accepted_states_preserved'),0)} งานที่รับผลแล้ว
 และ {num(recovery.get('accepted_files_hash_size_mtime_preserved'),0)} ไฟล์เดิม การกู้ครั้งนี้รันซ้ำเฉพาะ
 delayed_noisy__v4_no_recovery เป็น attempt 2 ไม่ทราบสาเหตุหรือระยะเวลาการขัดจังหวะเดิม</p>
 <p class="small">การทวนใช้ 704 กรณีเดิม ไม่เพิ่มตัวอย่างอิสระ เทียบเฉพาะไฟล์ deterministic ไม่รวมเวลาและ runtime metadata
 ทุก rollout ตรวจบัญชี; physical replay ตรวจตัวอย่างที่กำหนด และตรวจ point means/CVaR กับ CI ที่ใช้ตัดสินแยกจาก implementation หลัก</p>
 <h2>ข้อจำกัดที่ต้องพกไปกับผล</h2>
 <p class="small">ทุกกรณียังเป็นงานสังเคราะห์สามขั้นแบบ fluid ไม่มีการยืนยันกับ telemetry จริง
 กลไก buffer, migration, startup และ noise เป็น stress assumptions
 ช่วงตามงาน 32 นาทีมีงานเข้าเป็นศูนย์และ productivity คงที่ ไม่ได้ครอบคลุมการเปลี่ยนโหลดต่อเนื่องทั้งหมด</p>
 <h2>ทำซ้ำจากแพ็กเกจ</h2>
 <pre>python -m pip install -r requirements.txt
python -m unittest -v test_lab test_capacity test_v3 test_v4 test_v4_metrics
python run_v4.py --split validation --out fresh_v4_results
python review_v4_results.py --out fresh_v4_results
python evaluate_v4.py --results fresh_v4_results</pre>
 <p class="small">START_COLAB_v040.ipynb จัดลำดับการทำซ้ำและบันทึกหลักฐานทดสอบใหม่ไว้ให้
 การประเมินผลสุดท้ายต้องมี independent review และผลทดสอบประกอบ
 ใช้โฟลเดอร์ผลใหม่เสมอ หากแก้ policy หลังเห็นผล ให้เริ่มเวอร์ชันและชุดทดสอบที่ยังไม่ใช้ใหม่
 ขั้นต่อไปที่มีประโยชน์คือวินิจฉัยกรณีที่ยังแย่ตามหลักฐาน และตรวจสัญญาณกับ telemetry โดยไม่ออกคำสั่งจริง</p>
 <h2>เอกสารพื้นฐาน</h2>
 <p class="caption">Wang &amp; Javidi: adaptive scheduling with reconfiguration delay
 <a href="https://arxiv.org/abs/1511.03417">arxiv.org/abs/1511.03417</a><br>
 Hsieh, Hou &amp; Liu: switching overhead and biased scheduling
 <a href="https://arxiv.org/abs/1701.03831">arxiv.org/abs/1701.03831</a><br>
 Koehler et al.: adaptive robust MPC
 <a href="https://arxiv.org/abs/1909.01813">arxiv.org/abs/1909.01813</a><br>
 Kubernetes HPA: tolerance และ stabilization เพื่อลดการแกว่งของการปรับขนาด
 <a href="https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/">kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/</a><br>
 ใช้เป็นพื้นฐานแนวคิด ไม่มีการถ่ายโอนทฤษฎีเสถียรภาพ ค่า default หรือข้อรับรองจากเอกสารเหล่านี้มาให้ ANS</p>
 {evidence(['validation/verification.json','independent_review.json','reproducibility.json','recovery_independent_evidence_check.json','v4_results/unit_tests.json'])}
 '''
 bodies=[p1,p2,p3,p4,p5,p6,p7,p8,p9,p10]
 for index in (6,7,8):bodies[index]='<div class="dense-table-layout">'+bodies[index]+'</div>'
 return bodies


def load_bundle(preview=False):
 if preview:
  return {'protocol':read(ROOT/'v4_protocol_draft.json')}
 from evaluate_v4 import evaluate, INTEGRITY_FLAGS, _read_rows, load_acceptance
 source=ROOT/'v4_results/validation'
 result={name:read(source/filename) for name,filename in (
  ('summary','summary.json'),('decision','decision.json'),('verification','verification.json'),
  ('audit','independent_review.json'),('repro','reproducibility.json'))}
 result['protocol']=read(ROOT/'v4_protocol.json')
 result['tests']=read(ROOT/'v4_results/unit_tests.json')
 result['selection']=read(ROOT/'v4_results/development_grid/selection.json')
 result['development_decision']=read(ROOT/'v4_results/development/decision.json')
 result['tail_attribution']=read(source/'tail_attribution.json')
 result['recovery']=read(ROOT/'v4_results/recovery_independent_evidence_check.json')
 s=result['summary'];v=result['verification'];a=result['audit'];tests=result['tests'];repro=result['repro']
 if v['status']!='SUCCEEDED':raise RuntimeError('The final validation workflow must succeed')
 if not (tests.get('passed') is True or tests.get('successful') is True) or tests['failures'] or tests['errors']:
  raise RuntimeError('Passing combined unit-test evidence is required')
 if not all(a['acceptance_integrity'].get(key) is True for key in INTEGRITY_FLAGS):
  raise RuntimeError('Every independent integrity requirement must pass before reporting')
 if repro.get('status') not in ('PASS','PASSED','SUCCEEDED') or repro.get('all_byte_identical') is not True:
  raise RuntimeError('A successful fresh full deterministic reproduction is required')
 recovery=result['recovery']
 if (repro.get('repeat_resumed_after_interruption') is not True or recovery.get('status') not in ('PASS','PASSED')
     or recovery.get('accepted_states_preserved')!=41 or recovery.get('accepted_files_hash_size_mtime_preserved')!=123
     or recovery.get('deterministic_artifacts_identical')!=88 or recovery.get('bound_files_changed') is not False
     or recovery.get('primary_summary_verification_and_decision_unchanged') is not True
     or recovery['actual_attempts']['delayed_noisy__v4_no_recovery']!=2):
  raise RuntimeError('Resumed-repetition evidence does not match the report description')
 if (s['scenarios'],s['episodes'],s['minutes'])!=(704,29568,3300864):
  raise RuntimeError('Unexpected validation scope')
 summary_hash=sha(source/'summary.json')
 if summary_hash!=v['summary_sha256'] or summary_hash!=a['artifact_evidence']['summary_sha256']:
  raise RuntimeError('Summary does not match workflow and independent-review evidence')
 if result['tail_attribution']['evidence']['summary_sha256']!=summary_hash:
  raise RuntimeError('Tail attribution describes a different summary')
 for regime in REGIMES:
  for policy in POLICIES:
   item=result['tail_attribution']['regimes'][regime]['global_static_relative_cvar90'][policy]
   if item['absolute_reconciliation_error']>1e-6:
    raise RuntimeError('Tail-family contributions do not sum to the recorded global CVaR')
 if a['scope']['episodes_checked']!=s['episodes']:
  raise RuntimeError('Independent review does not cover the declared episode rows')
 if a['observation_only_replay']['actions_and_evidence_match'] is not True:
  raise RuntimeError('Observation replay did not match policy actions and evidence')
 declared,declaration_hash=load_acceptance()
 if result['protocol']['acceptance_criteria']!=declared:
  raise RuntimeError('Final protocol changed the pre-development acceptance object')
 if result['decision']['acceptance_declaration_sha256']!=declaration_hash:
  raise RuntimeError('Decision used a different acceptance declaration')
 if result['selection']['selected_configuration']!=result['protocol']['controller_configuration']:
  raise RuntimeError('Frozen configuration differs from the selected development candidate')
 if len(result['selection']['ranked_trials'])!=6:
  raise RuntimeError('The six development grid members must all be retained')
 computed=evaluate(s,_read_rows(source),integrity=a['acceptance_integrity'],
                   acceptance=declared,split='validation')
 for key in ('primary','risk_point_nonworsening','mean_noninferiority',
             'loss_and_endpoint_point_nonworsening','underload','integrity',
             'advancement_checks','failed_criteria','advancement_passed','decision'):
  if result['decision'][key]!=computed[key]:
   raise RuntimeError('Report decision differs from the exact evaluator: '+key)
 date=datetime.fromisoformat(result['protocol']['frozen_at_utc']).astimezone(ZoneInfo('Asia/Bangkok'))
 result['date_label']=date.strftime('%d/%m/%Y')
 result['footer_date']=date.strftime('%d %b %Y').upper()
 result['summary_sha256']=summary_hash
 result['protocol_sha256']=sha(ROOT/'v4_protocol.json')
 return result


def contact_sheet(directory,pages_count):
 from PIL import Image,ImageDraw
 width,height=340,482;columns=2;rows=(pages_count+columns-1)//columns
 sheet=Image.new('RGB',(columns*width,rows*(height+28)),'#E7EEF2')
 draw=ImageDraw.Draw(sheet)
 for index in range(pages_count):
  im=Image.open(directory/f'page_{index+1:02}.png').convert('RGB')
  im.thumbnail((width-12,height-8))
  x=(index%columns)*width+(width-im.width)//2
  y=(index//columns)*(height+28)+24
  sheet.paste(im,(x,y));draw.text(((index%columns)*width+12,(index//columns)*(height+28)+7),f'PAGE {index+1}',fill=INK)
 sheet.save(directory/'contact_sheet.png')


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--out',type=Path,default=ROOT/'report_v040')
 parser.add_argument('--preview',action='store_true',help='Create labeled layout material without loading validation outcomes')
 args=parser.parse_args()
 out=args.out.resolve()/('layout_preview' if args.preview else '')
 out.mkdir(parents=True,exist_ok=True)
 bundle=load_bundle(args.preview);prepare_fonts(out)
 figures(bundle.get('summary'),out,args.preview)
 bodies=pages(bundle,args.preview)
 if len(bodies)!=10:raise RuntimeError('Expected ten designed report pages')
 # Use only punctuation with verified glyph coverage in the embedded Thai fonts.
 bodies=[body.replace('\u201c','&quot;').replace('\u201d','&quot;').replace('\u00b7',' x ')
         .replace('\u00d7',' x ').replace('\u2212','-') for body in bodies]
 screen='''html {background:#E7EDF0;}section.page {box-sizing:border-box;width:210mm;min-height:297mm;background:white;padding:13mm 12.7mm;margin:9mm auto;}@media print {html {background:white;}section.page {margin:0;page-break-after:always;}}'''
 basename='ANS_v040_Layout_Preview' if args.preview else 'ANS_v040_Report_TH'
 (out/(basename+'.html')).write_text('<!doctype html><html lang="th"><head><meta charset="utf-8"><title>ANS v0.4.0</title><style>'+CSS+screen+'</style></head><body>'+''.join('<section class="page">'+body+'</section>' for body in bodies)+'</body></html>',encoding='utf-8')
 document=fitz.open();archive=fitz.Archive(str(out));layout=[]
 for number,body in enumerate(bodies,1):
  page=document.new_page(width=595.276,height=841.89)
  spare,scale=page.insert_htmlbox(fitz.Rect(36,35,559.276,793),body,css=CSS,archive=archive,scale_low=1)
  if spare<0 or scale!=1:raise RuntimeError(f'Page {number} overflow: spare={spare}, scale={scale}')
  page.draw_line(fitz.Point(36,807),fitz.Point(559.276,807),color=(.84,.89,.92),width=.5)
  footer='ANS v0.4.0  |  '+('LAYOUT ONLY - NO VALIDATION RESULTS' if args.preview else 'SYNTHETIC VALIDATION  |  '+bundle['footer_date'])
  page.insert_text((36,822),footer,fontsize=7.5,fontname='helv',color=(.38,.47,.54))
  page.insert_text((530,822),f'{number} / 10',fontsize=7.5,fontname='helv',color=(.38,.47,.54))
  layout.append({'page':number,'spare_height_pt':spare,'scale':scale})
 document.set_metadata({'title':'ANS v0.4.0 - รายงานการทดลอง' if not args.preview else 'ANS v0.4.0 - Layout preview only',
  'author':'Adaptive Nervous System experiment','subject':'Synthetic validation of a hybrid allocation controller; risk, losses and uncertainty kept explicit'})
 target=out/(basename+'.pdf');document.save(target,deflate=True,garbage=4);document.close()
 reopened=fitz.open(target);qa=out/'qa_pages';qa.mkdir(exist_ok=True)
 for number,page in enumerate(reopened,1):
  page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(qa/f'page_{number:02}.png')
 extracted='\n'.join(page.get_text() for page in reopened)
 if len(reopened)!=10 or '29,568' not in extracted or '3,300,864' not in extracted:
  raise RuntimeError('PDF page/text verification failed')
 if not args.preview and PENDING in extracted:
  raise RuntimeError('Final report still contains a pending-value placeholder')
 contact_sheet(qa,len(reopened))
 review={'pages':len(reopened),'content_boxes':layout,'preview_only':args.preview,
         'summary_sha256':bundle.get('summary_sha256'),'protocol_sha256':bundle.get('protocol_sha256'),
         'acceptance':dig(bundle,'decision','advancement_checks'),
         'pdf_sha256':sha(target),'rendered_pngs':len(reopened),
         'visual_review':'required after rendering'}
 (out/'pdf_layout_checks.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
 reopened.close()
 print(json.dumps({'pdf':str(target),'preview_only':args.preview,'layout':layout},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

