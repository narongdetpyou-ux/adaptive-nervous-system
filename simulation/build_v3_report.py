"""Render an eight-page Thai report from completed, verified v0.3 evidence.

Presentation code only: this never runs a policy or modifies frozen experiment
files. PyMuPDF shapes embedded Noto Thai fonts; matplotlib draws data charts.
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
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parent
POLICIES = ('static','abc_capacity','capacity_v2','capacity_balance','v3_signal_only','v3_full')
REGIMES = ('instant','transit_1','transit_3')
NAMES = {'static':'Static','abc_capacity':'ABC + capacity gate','capacity_v2':'Capacity v2',
         'capacity_balance':'Capacity balance','v3_signal_only':'V3 signal only','v3_full':'V3 full'}
GROUP_NAMES = {
    'surge_only':'Flash: งานเข้าเพิ่ม','ingress_slow':'Flash: รับเข้างานช้า',
    'processing_slow':'Flash: ประมวลผลช้า','delivery_slow':'Flash: ส่งออกช้า',
    'shifting_bottleneck':'Flash: คอขวดเปลี่ยนจุด','generated_underload':'สร้างใหม่: ภาระต่ำ',
    'generated_sustained_processing_fault':'สร้างใหม่: ช้าต่อเนื่อง',
    'generated_switching_fault':'สร้างใหม่: คอขวดสลับ',
    'generated_pulsed_short_fault':'สร้างใหม่: พุ่ง/ขัดข้องสั้น',
    'generated_asymmetric_recovery':'สร้างใหม่: ฟื้นตัวไม่สมมาตร',
    'generated_sustained_overload':'สร้างใหม่: งานเกินกำลัง'}
INK,TEAL,BLUE,RED,GRAY = '#17324B','#147D72','#4A79AA','#B7473A','#8595A6'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def num(value,digits=2):
    return f'{value:,.{digits}f}'


def pct(value):
    return 'ไม่มีค่า' if value is None else f'{value:.2f}%'


def ci(value):
    return 'ไม่มีค่า' if value is None else f'{value[0]:.2f} ถึง {value[1]:.2f}'


def effect(value):
    if value is None:
        return 'อัตราส่วนไม่กำหนด'
    return ('ลดลง ' if value>=0 else 'เพิ่มขึ้น ')+pct(abs(value))


def status(value):
    return '<b class="green">ผ่าน</b>' if value else '<b class="red">ไม่ผ่าน</b>'


def table(headers,rows,highlight=None,small=False):
    head=''.join(f'<th>{value}</th>' for value in headers)
    body=[]
    for index,row in enumerate(rows):
        cls=' class="highlight"' if highlight is not None and index==highlight else ''
        body.append('<tr'+cls+'>'+''.join(f'<td class="{"left" if j==0 or len(re.sub("<[^>]+>", "", str(cell)))>24 else "num"}">{cell}</td>'
                                             for j,cell in enumerate(row))+'</tr>')
    return f'<table class="{"compact" if small else ""}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def caption(text):
    return '<p class="caption">'+text+'</p>'


def evidence(files):
    return caption('หลักฐาน: '+', '.join('<span class="file">'+html.escape(name)+'</span>' for name in files))


def acceptance(summary,verification,tests,rows,audit_pass):
    instant=summary['regimes']['instant']
    primary=instant['comparisons']['v3_full']['static']['improvement_ci95'][0]>0
    v2=instant['comparisons']['v3_full']['capacity_v2']
    noninferiority=(v2['reduction_pct_ci95'][0]>=-5 if v2['reduction_pct_ci95'] is not None
                    else v2['improvement_ci95'][0]>=-1e-6)
    surge=instant['by_group']['surge_only']['overall']
    surge_ok=surge['v3_full']['mean_backlog']<=surge['static']['mean_backlog']+1e-6
    mean_ok=all(summary['regimes'][r]['comparisons']['v3_full']['static']['improvement_ci95'][0]>0 for r in REGIMES)
    risk_ok=True;tail_ok=True;terminal_ok=True
    for regime in REGIMES:
        part=summary['regimes'][regime];risk=part['static_relative_harm'];overall=part['overall']
        new,old=risk['v3_full']['cvar90'],risk['capacity_v2']['cvar90']
        risk_ok &= (new<old or (abs(new)<=1e-6 and abs(old)<=1e-6))
        tail_ok &= overall['v3_full']['tail_auc']<=overall['capacity_v2']['tail_auc']+1e-6
        terminal_ok &= overall['v3_full']['end_backlog']<=overall['capacity_v2']['end_backlog']+1e-6
    underload=[row for row in rows if row['policy']=='v3_full' and row['group']=='generated_underload']
    underload_ok=bool(underload) and all(
        row['total_moves']==0 and all(abs(row[k])<=1e-6 for k in
                                     ('mean_backlog','peak_backlog','end_backlog','tail_mean_backlog','tail_end_backlog'))
        for row in underload)
    integrity=verification['status']=='SUCCEEDED' and tests['successful'] and audit_pass and underload_ok
    integrity &= summary['max_mass_error']<=1e-6 and summary['max_budget_error']<=1e-6
    result={'primary_pass':bool(primary),'noninferiority_pass':bool(noninferiority),
            'surge_pass':bool(surge_ok),'mean_all_regimes_pass':bool(mean_ok),
            'cvar_all_regimes_pass':bool(risk_ok),'tail_auc_all_regimes_pass':bool(tail_ok),
            'terminal_all_regimes_pass':bool(terminal_ok),'integrity_underload_pass':bool(integrity)}
    result['advancement_pass']=all(result.values())
    return result


def prepare_fonts(out):
    (out/'fonts').mkdir(exist_ok=True)
    for name in ('NotoSansThai-Regular.ttf','NotoSansThai-Bold.ttf','NotoSans-Regular.ttf','NotoSans-Bold.ttf'):
        source=ROOT/'report/fonts'/name
        if not source.is_file():source=Path('/usr/share/fonts/truetype/noto')/name
        if not source.is_file():raise FileNotFoundError(name)
        shutil.copyfile(source,out/'fonts'/name)


def figures(summary,out):
    destination=out/'figures';destination.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10.5,
        'axes.spines.top':False,'axes.spines.right':False,'axes.spines.left':False,
        'axes.labelcolor':INK,'xtick.color':INK,'ytick.color':INK,'text.color':INK,
        'axes.edgecolor':'#DCE4EA','savefig.facecolor':'white'})
    overall=summary['regimes']['instant']['overall']
    values=[overall[p]['mean_backlog'] for p in POLICIES]
    fig,ax=plt.subplots(figsize=(9.5,3.5))
    bars=ax.barh(range(6),values,height=.58,color=[GRAY,GRAY,BLUE,TEAL,'#A9B5C1',RED])
    ax.set_yticks(range(6),[NAMES[p] for p in POLICIES]);ax.invert_yaxis()
    ax.set_xlim(0,max(values)*1.16);ax.grid(axis='x',color='#E5EBEF');ax.set_axisbelow(True)
    ax.tick_params(axis='y',length=0,pad=8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x,_:f'{x/1000:.0f}k'))
    ax.set_xlabel('Mean backlog over original horizon (work units; lower is better)')
    for bar,value in zip(bars,values):
        ax.text(value+max(values)*.012,bar.get_y()+bar.get_height()/2,f'{value:,.0f}',va='center',fontsize=10)
    fig.subplots_adjust(left=.24,right=.99,top=.98,bottom=.2)
    fig.savefig(destination/'instant_policy_means.png',dpi=220);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(9.5,3.45))
    for policy,color in [('static',GRAY),('capacity_v2',BLUE),('capacity_balance',TEAL),('v3_full',RED)]:
        axes[0].plot([0,1,3],[summary['regimes'][r]['overall'][policy]['mean_backlog'] for r in REGIMES],
                     'o-',label=NAMES[policy],color=color,linewidth=1.8,markersize=4.5)
        axes[1].plot([0,1,3],[summary['regimes'][r]['static_relative_harm'][policy]['cvar90'] for r in REGIMES],
                     'o-',label=NAMES[policy],color=color,linewidth=1.8,markersize=4.5)
    for ax,title in zip(axes,('Mean backlog','Worst 10% positive harms vs Static')):
        ax.set_title(title,fontsize=10.5);ax.set_xticks([0,1,3]);ax.set_xlabel('Transfer delay (minutes)')
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_:f'{x/1000:.0f}k'))
        ax.grid(color='#E5EBEF');ax.set_axisbelow(True)
    axes[0].set_ylabel('Work units');axes[0].legend(fontsize=8.1,loc='upper left')
    fig.subplots_adjust(left=.065,right=.99,top=.9,bottom=.2,wspace=.25)
    fig.savefig(destination/'delay_and_downside.png',dpi=220);plt.close(fig)


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
tr.highlight td {background-color:#FFF1EA;}
table.hero {border-top:2pt solid #147D72;border-bottom:1pt solid #DDE6E9;margin:13pt 0;}
table.hero td {background:white;border:0;padding:9pt 9pt 9pt 0;text-align:left;}
.big {font-family:Sans;font-size:30pt;color:#147D72;font-weight:bold;line-height:1.2;margin:0 0 5pt;}
.label {font-size:9.6pt;color:#456174;}
img {max-width:100%;}pre {font-family:Sans;font-size:8.5pt;line-height:1.65;padding:11pt 12pt;background:#EFF3F6;margin:9pt 0;}
ul,ol {padding-left:17pt;margin:5pt 0 9pt;}li {margin-bottom:5pt;}
a {color:#2E6E95;text-decoration:none;}
'''


def pages(summary,development,verification,criteria,protocol,tests,audit,repro,date_label):
    instant=summary['regimes']['instant'];current=instant['overall']['v3_full'];v2=instant['overall']['capacity_v2']
    primary=instant['comparisons']['v3_full']['static'];versus_v2=instant['comparisons']['v3_full']['capacity_v2']
    versus_balance=instant['comparisons']['v3_full']['capacity_balance']
    strongest=min(POLICIES,key=lambda p:instant['overall'][p]['mean_backlog'])
    main_status='ผ่านเกณฑ์รุ่นใหม่ที่กำหนดไว้' if criteria['advancement_pass'] else 'ไม่ผ่านเกณฑ์รุ่นใหม่ที่กำหนดไว้'
    headline_class='notice' if criteria['advancement_pass'] else 'warn'
    main_scope=f'{summary["scenarios"]} สถานการณ์ / {summary["episodes"]:,} episodes / {summary["minutes"]:,} นาทีจำลอง'
    p1=f'''
    <p class="eyebrow">ADAPTIVE NERVOUS SYSTEM / RESEARCH NOTE 03</p>
    <h1 class="cover">ทดสอบการย้ายทรัพยากร<br>เมื่อมีความไม่แน่นอน</h1>
    <p class="subtitle">ผลทดลอง v0.3.0 | {date_label}<br>{main_scope}</p>
    <table class="hero"><tr><td style="width:53%"><p class="big">{pct(primary['reduction_pct'])}</p>
    <p class="label">งานค้างเฉลี่ยลดลงเทียบ Static<br>กรณีย้ายได้ทันที / 95% CI {ci(primary['reduction_pct_ci95'])}%</p></td>
    <td><p class="big" style="color:#B7473A">{pct(versus_v2['reduction_pct'])}</p>
    <p class="label">อัตราลดงานค้างเทียบ Capacity v2<br>ค่าติดลบหมายถึงงานค้างมากกว่า v2</p></td></tr></table>
    <div class="{headline_class}"><b>{main_status}</b><br>
    เกณฑ์หลักเทียบ Static: {status(criteria['primary_pass'])} | ไม่ด้อยกว่า v2 เกิน 5%: {status(criteria['noninferiority_pass'])}<br>
    เกณฑ์ไปต่อครบทุกข้อ: {status(criteria['advancement_pass'])}</div>
    <p>ในกรณีไม่เสียเวลาย้าย <b>V3 full</b> มีงานค้างเฉลี่ย {num(current['mean_backlog'])} หน่วย
    เทียบกับ v2 ที่ {num(v2['mean_backlog'])} หน่วย จึง{effect(versus_v2['reduction_pct'])}เมื่อเทียบรุ่นเดิม
    ช่วงความเชื่อมั่นของอัตราลดลงคือ {ci(versus_v2['reduction_pct_ci95'])}%</p>
    <h2>สิ่งที่ต้องดูพร้อมกับค่าเฉลี่ย</h2>
    <p>การย้ายที่น้อยลงช่วยลดทรัพยากรที่หยุดทำงานระหว่างทาง แต่ไม่ได้แปลว่าเสียหายน้อยลงทุกกรณี
    เราจึงวัดงานค้างท้ายช่วง ความเสียหายในกลุ่มกรณีแย่ที่สุด และการระบายงานต่ออีก 32 นาที
    พร้อมแยกผลเมื่อการย้ายใช้เวลา 0, 1 และ 3 นาที</p>
    <p>วิธีที่มีงานค้างเฉลี่ยต่ำที่สุดในกรณีย้ายทันทีคือ <b>{NAMES[strongest]}</b>
    โดย V3 full {effect(versus_balance['reduction_pct'])}เมื่อเทียบกับ Capacity balance
    ซึ่งเป็นวิธีฐานที่ใช้การประมาณอัตราทำงานเหมือนกันและมีกฎเลือกที่ง่ายกว่า</p>
    <p class="small">ตัวอย่างของข้อแลกเปลี่ยน: V3 แพ้ Static {instant['static_relative_harm']['v3_full']['count']} กรณี
    เทียบกับ v2 ที่ {instant['static_relative_harm']['capacity_v2']['count']} กรณี แต่ค่า CVaR90 เพิ่มจาก
    {num(instant['static_relative_harm']['capacity_v2']['cvar90'])} เป็น {num(instant['static_relative_harm']['v3_full']['cvar90'])}
    หน่วย การแพ้น้อยครั้งลงจึงยังเกิดพร้อมกับความเสียหายที่หนักขึ้นได้</p>
    {table(['แยกแหล่งสถานการณ์ / D=0','V3 ลดเทียบ Static','V3 ลดเทียบ v2'],[
        [label,pct(instant['source_slices'][source]['comparisons']['v3_full']['static']['reduction_pct']),
         pct(instant['source_slices'][source]['comparisons']['v3_full']['capacity_v2']['reduction_pct'])]
        for source,label in [('legacy_flash','Flash Sale เดิม'),('generated_synthetic','รูปแบบสังเคราะห์แยก')]],small=True)}
    <p class="small"><b>ขอบเขตข้อสรุป:</b> เป็นผลจากคิวและภาระงานสังเคราะห์ ภายใต้ชุดสมมติฐานที่ระบุ
    ใช้ตัดสินใจว่าจะวิจัยแนวทางใดต่อ ผลนี้ไม่ได้รับรองให้ควบคุมทรัพยากรในระบบใช้งานจริง</p>
    {evidence(['v3_results/validation/summary.json','decision.json','v3_protocol.json'])}
    '''
    p2=f'''
    <p class="eyebrow">01 / EXPERIMENT DESIGN</p><h1>แยกนโยบาย ออกจากต้นทุนการย้าย</h1>
    <p>ทุกวิธีเริ่มที่ <b>69 / 69 / 69</b> ในคิวต่อเนื่อง 3 ขั้น งบรวม 207 หน่วย
    ใช้รายการการย้ายเดิม เริ่มย้ายได้หลังช่วงอุ่นเครื่อง 5 นาที เว้นอย่างน้อย 8 นาทีระหว่างการย้าย
    และสำรองอย่างน้อย 10 หน่วยต่อขั้น ข้อมูลเข้าและกำลังทำงานจริงจับคู่กันทุกวิธี</p>
    {table(['ระบบจำลอง','สิ่งที่เกิดกับทรัพยากร'],[
       ['ย้ายทันที (D=0)','ผู้ให้ลดและผู้รับเพิ่ม ก่อนทำงานในนาทีเดียวกัน'],
       ['ย้าย 1 นาที (D=1)','ทรัพยากรที่ย้ายทำงานไม่ได้ 1 ช่วงนาที'],
       ['ย้าย 3 นาที (D=3)','ทรัพยากรที่ย้ายทำงานไม่ได้ 3 ช่วงนาที']])}
    <p class="small">หากย้ายตอน t ผู้ให้เสียทรัพยากรทันที ผู้รับเริ่มใช้ได้ก่อนทำงานที่ t+D
    ผลรวมทรัพยากรที่กำลังทำงานและที่อยู่ระหว่างทางยังเป็น 207 เสมอ
    ต้นทุนวัดเป็น <b>หน่วยทรัพยากร x นาที</b> ที่ใช้งานไม่ได้ ไม่มีการแปลงเป็นราคาเงิน</p>
    <div class="notice small"><b>บัญชีที่ต้องตรงทุกนาที:</b><br>
    ทรัพยากรที่ทำงาน + ทรัพยากรระหว่างทาง = 207<br>
    งานเข้ารวม = งานเสร็จรวม + งานค้างรวม</div>
    <h2>V3 เพิ่มการตรวจหลักฐานและแบบจำลอง</h2>
    <p>ก่อนย้าย ผู้รับต้องมีงานค้างล่าสุดเกิน 10 และเคยทำงานเต็มกำลังอย่างน้อย 2 ครั้งใน 5 นาทีล่าสุด
    ต้องเห็นความต่างของกำลังเทียบผู้ให้อย่างน้อยหนึ่งขั้น หรือมีหลักฐานเต็มกำลังครบทั้ง 5 ครั้ง
    ค่าอัตราจากช่วงภาระต่ำถือเป็นเพียงขอบล่าง ไม่ใช่กำลังผลิตเต็มที่ที่วัดได้</p>
    <p>V3 full ประเมินล่วงหน้า 8 นาที: แบบพื้นฐาน 9 แบบ และกรณีความไม่แน่นอนแยกแต่ละขั้นอีก 24 แบบ
    รวม 33 แบบ โดยนับเวลาย้ายตาม D จริง เก็บอัตราช้าที่เคยวัดได้เป็นขอบจำกัดของผู้ให้
    การย้ายต้องให้ผลดีเฉลี่ยผ่านเกณฑ์ และผ่านเงื่อนไขความเสียหายกับงานค้างท้ายการคาดการณ์</p>
    <h2>ชุดพัฒนาและชุดตรวจสอบ</h2>
    {table(['ชุด','ขอบเขต','การใช้'],[
       ['พัฒนา','44 กรณี / seed 1000-1003','กรณีเดิมที่เคยเห็นใน v2'],
       ['Validation','352 กรณี / seed 2000-2031','ล็อกโค้ดก่อนดูผลนโยบาย'],
       ['ABC gate','3 seed ต่อกรณี','เฉลี่ยภายในกรณีก่อนเปรียบเทียบ']])}
    <p class="small">เก็บครบ 11 รูปแบบ: 5 รูปแบบจาก Flash Sale เดิม และ 6 รูปแบบสังเคราะห์แยก
    มี 1,056 คู่สถานการณ์-ระยะย้าย แต่ไม่รวมระยะย้ายต่างกันเป็นค่าเฉลี่ยพาดหัวเดียว
    ช่วงความเชื่อมั่นใช้ paired bootstrap 2,000 ครั้งภายในแต่ละรูปแบบ</p>
    <p class="small">ช่วงอัตรา 15% หรือ 25% เมื่อข้อมูลเบาบางเป็นสมมติฐานทดสอบความทนทาน
    ไม่ใช่ความน่าจะเป็นของความผิดพลาด ส่วนการล็อกในเครื่องช่วยตรวจว่าไฟล์เปลี่ยนหรือไม่
    ไม่ใช่การลงทะเบียนงานวิจัยกับหน่วยงานภายนอก</p>
    {evidence(['v3_protocol.json','scenario_manifest.json','v3_policy.py','v3_simulation.py'])}
    '''
    rows3=[[NAMES[p],num(instant['overall'][p]['mean_backlog']),num(instant['overall'][p]['end_backlog']),
            num(instant['overall'][p]['moves'])] for p in POLICIES]
    p3=f'''
    <p class="eyebrow">02 / ZERO-DELAY COMPARISON</p><h1>ผลเทียบทั้ง 6 วิธี เมื่อย้ายได้ทันที</h1>
    <p>ตารางนี้ใช้เฉพาะช่วงคะแนนเดิม 60 หรือ 96 นาทีของแต่ละสถานการณ์
    &quot;งานค้างเฉลี่ย&quot; คือจำนวนงานที่ยังไม่เสร็จเมื่อสิ้นแต่ละนาที แล้วเฉลี่ยภายในกรณีและระหว่างกรณี
    &quot;ท้ายช่วง&quot; คือค่าที่ขอบเขต H เดิม ก่อนเริ่มช่วงติดตาม</p>
    {table(['วิธี','งานค้างเฉลี่ย','ท้ายช่วง H','ย้ายเฉลี่ย'],rows3,highlight=5)}
    <img src="figures/instant_policy_means.png" style="width:100%">
    {caption('หน่วยงานจำลอง; ยิ่งน้อยยิ่งดี ตารางและกราฟสร้างจากผล validation จริง ค่าเฉลี่ยไม่บอกว่าทุกกรณีดีขึ้น')}
    <h2>วิธีฐานที่ง่ายกว่ามีความสำคัญ</h2>
    <p><b>Capacity balance</b> ประมาณอัตราทำงานจากสิ่งที่สังเกตได้ แล้วเลือกการย้ายที่ทำให้กำลังแต่ละขั้นใกล้สมดุล
    ใช้ catalog, งบ, ระยะเว้น และการสำรองแบบเดียวกัน ผลของมันช่วยแยกว่า
    ประโยชน์มาจากการรู้กำลังงานโดยประมาณ หรือจำเป็นต้องมีตัวคัดกรองซับซ้อนเพิ่ม</p>
    <p class="small">Balance มีค่าเฉลี่ยและการระบายงานที่ดี แต่ CVaR90 เทียบ Static สูงกว่า v2 ในทั้งสาม D
    จึงยังไม่ใช่ผู้ชนะทุกด้านหรือข้อเสนอให้ใช้ควบคุมบริการจริงทันที</p>
    <p class="small"><b>V3 signal only</b> ใช้กฎหลักฐานใหม่ แต่ยังใช้แบบจำลองพื้นฐาน 9 แบบและสมมติย้ายทันที
    ใน D=0 ผลต่างจาก V3 full จึงรวมทั้งแบบความไม่แน่นอนที่เพิ่มและเงื่อนไขงานค้างท้ายการคาดการณ์
    ไม่ได้แยกผลของมุมความไม่แน่นอนเพียงอย่างเดียว ส่วน D=1/3 ยังรวมการรับรู้เวลาย้ายด้วย</p>
    {evidence(['v3_results/validation/summary.json: regimes.instant','v3_policy.py'])}
    '''
    delay_rows=[];resource_rows=[]
    for regime in REGIMES:
        part=summary['regimes'][regime];o=part['overall'];c=part['comparisons']['v3_full']
        delay_rows.append([str(part['migration_delay_minutes']),num(o['capacity_v2']['mean_backlog']),
                           num(o['capacity_balance']['mean_backlog']),num(o['v3_full']['mean_backlog']),pct(c['static']['reduction_pct'])])
        resource_rows.append([str(part['migration_delay_minutes']),num(o['capacity_v2']['moves']),num(o['v3_full']['moves']),
                              num(o['capacity_v2']['resource_minutes_lost']),num(o['v3_full']['resource_minutes_lost'])])
    p4=f'''
    <p class="eyebrow">03 / TRANSFER DELAY</p><h1>เมื่อการย้ายทำให้กำลังงานหายชั่วคราว</h1>
    <p>วิเคราะห์ 0, 1 และ 3 นาทีแยกกัน การเพิ่ม D เปลี่ยนผลของการย้ายผ่านกำลังทำงานจริงที่ลดลง
    ทุกนโยบายเผชิญกลไกเดียวกัน ส่วน Static ไม่มีการย้ายจึงได้ผลเดิมในทั้งสามกรณี</p>
    {table(['D (นาที)','v2: งานค้าง','Balance: งานค้าง','V3: งานค้าง','V3 ลด vs Static'],delay_rows,small=True)}
    <img src="figures/delay_and_downside.png" style="width:100%">
    {caption('ซ้าย: งานค้างเฉลี่ย; ขวา: ค่าเฉลี่ยความเสียหายด้านบวกใน 10% กรณีแย่ที่สุดเมื่อเทียบ Static ไม่ใช่ความเสี่ยงของระบบจริง')}
    <h2>ย้ายน้อยลงกับงานค้างน้อยลงเป็นคนละผล</h2>
    {table(['D','v2: ครั้งย้าย','V3: ครั้งย้าย','v2: ทรัพยากร x นาที','V3: ทรัพยากร x นาที'],resource_rows,small=True)}
    <p>ตัวคัดกรองที่เข้มขึ้นอาจลดจำนวนการย้ายและการเสียกำลังระหว่างทาง แต่ยังทำให้งานค้างเพิ่มได้
    หากปฏิเสธการแก้คอขวดหรือคืนสมดุลช้า จึงต้องอ่านตารางงานค้างและต้นทุนการย้ายคู่กัน
    ผลที่ D ต่างกันเป็นการทดสอบความไวต่อสมมติฐานเวลา ไม่ใช่ค่าที่วัดจากโครงสร้างพื้นฐานจริง</p>
    <p class="small">ขณะคำนวณอัตราจาก throughput ใช้เฉพาะทรัพยากรที่ทำงานจริง
    ไม่นับหน่วยที่กำลังเดินทางเป็นกำลังพร้อมใช้ และไม่ย้ายซ้ำก่อนครบ dwell 8 นาที</p>
    {evidence(['v3_results/validation/summary.json: regimes','episodes.csv','audit_v3.py'])}
    '''
    risk_rows=[]
    for regime in REGIMES:
        part=summary['regimes'][regime];risk=part['static_relative_harm']
        risk_rows.append([str(part['migration_delay_minutes']),f"{risk['capacity_v2']['count']} / {summary['scenarios']}",
                          f"{risk['v3_full']['count']} / {summary['scenarios']}",num(risk['capacity_v2']['cvar90']),num(risk['v3_full']['cvar90'])])
    family_rows=[]
    for group,part in instant['by_group'].items():
        c=part['comparisons']['v3_full']['static'];o=part['overall']
        family_rows.append([GROUP_NAMES[group],num(o['static']['mean_backlog'],0),num(o['v3_full']['mean_backlog'],0),
                            pct(c['reduction_pct']),f"{c['wins']}/{c['ties']}/{c['losses']}"])
    p5=f'''
    <p class="eyebrow">04 / DOWNSIDE AND FAMILIES</p><h1>นับกรณีที่แพ้ และวัดว่าแพ้มากแค่ไหน</h1>
    <p>กำหนดความเสียหายแต่ละกรณีเป็น max(งานค้างของวิธีนั้น - งานค้างของ Static, 0)
    แล้วหา <b>CVaR90</b>: ค่าเฉลี่ยของความเสียหายใน 10% กรณีแย่ที่สุด รวมศูนย์ตามมวลตัวอย่าง
    ค่าของ v2 และ V3 จึงเทียบกับ Static เดียวกัน</p>
    {table(['D','v2 แพ้ Static','V3 แพ้ Static','v2: CVaR90','V3: CVaR90'],risk_rows,small=True)}
    <h2>ครบทั้ง 11 รูปแบบ ในกรณีย้ายทันที</h2>
    {table(['รูปแบบ','Static','V3','ลดงานค้าง','ชนะ/เสมอ/แพ้'],family_rows,small=True)}
    <p class="small">ค่าติดลบในคอลัมน์ลดงานค้างหมายถึง V3 แย่กว่า Static ถ้าทั้งสองวิธีไม่มีงานค้าง
    อัตราร้อยละจะไม่กำหนด การเสมอหรือไม่มีความเสียหายในกลุ่มหนึ่งไม่พิสูจน์ว่าไม่มีความเสียหายในภาระงานอื่น</p>
    <p class="small">การลดจำนวนกรณีแพ้เพียงอย่างเดียวอาจซ่อนความเสียหายที่หนักขึ้นในบางกรณี
    รายงานฉบับเต็มจึงเก็บทั้งจำนวน ค่าเฉลี่ยความเสียหาย ค่าสูงสุด CVaR90 และช่วงความเชื่อมั่น
    ไว้ใน JSON โดยไม่ตัดกลุ่มที่ให้ผลลบออก การให้น้ำหนักกรณีเท่ากันไม่ได้ปรับสเกลของงานค้างให้เท่ากัน
    กลุ่มงานเกินกำลังที่มีงานค้างมากจึงมีอิทธิพลต่อผลรวมได้มาก อัตราร้อยละรวมใช้แทนทุกภาระงานไม่ได้</p>
    {evidence(['v3_results/validation/summary.json: static_relative_harm, by_group'])}
    '''
    tail_rows=[]
    for regime in REGIMES:
        o=summary['regimes'][regime]['overall']
        tail_rows.append([str(summary['regimes'][regime]['migration_delay_minutes']),
            num(o['v3_full']['end_backlog']-o['capacity_v2']['end_backlog']),
            num(o['v3_full']['tail_auc']-o['capacity_v2']['tail_auc']),
            num(o['v3_full']['tail_end_backlog']),pct(100*o['v3_full']['drain_uncleared_fraction'])])
    checks=[
      ['หลัก: D=0 เทียบ Static','ขอบล่าง 95% CI ผลดี > 0',status(criteria['primary_pass'])],
      ['ไม่ด้อยกว่า v2 เกิน 5%','ขอบล่าง 95% CI อัตราลดลง >= -5%',status(criteria['noninferiority_pass'])],
      ['Surge-only ใน D=0','ค่าเฉลี่ย V3 <= Static',status(criteria['surge_pass'])],
      ['ค่าเฉลี่ยเทียบ Static ทุก D','ขอบล่าง 95% CI ผลดี > 0',status(criteria['mean_all_regimes_pass'])],
      ['ความเสียหาย CVaR90 ทุก D','ต่ำกว่า v2 หรือทั้งคู่เป็นศูนย์',status(criteria['cvar_all_regimes_pass'])],
      ['พื้นที่งานค้างช่วงติดตามทุก D','ค่าเฉลี่ย tail AUC <= v2',status(criteria['tail_auc_all_regimes_pass'])],
      ['งานค้างท้าย H ทุก D','ค่าเฉลี่ยท้าย H <= v2',status(criteria['terminal_all_regimes_pass'])],
      ['ความถูกต้องและภาระต่ำ','invariants / tests / ไม่ย้ายเกินจำเป็น',status(criteria['integrity_underload_pass'])],
    ]
    p6=f'''
    <p class="eyebrow">05 / ENDPOINTS AND ACCEPTANCE</p><h1>ไม่ย้ายปัญหาออกนอกช่วงคะแนน</h1>
    <p>หลัง H เดิม ติดตามเท่ากันอีก 32 นาที โดยไม่มีงานเข้าใหม่และตรึงกำลังงานจริงเท่านาทีสุดท้าย
    นี่คือสมมติฐานระบายงานสังเคราะห์ นโยบายไม่รู้ล่วงหน้าว่า H สิ้นสุดเมื่อใด
    ถ้าไม่หมดใน 32 นาที ระบุว่า <b>ยังไม่ระบายหมด</b> และจำกัดเวลาที่รายงานไว้ที่ 32</p>
    {table(['D','ท้าย H: V3-v2','tail AUC: V3-v2','V3 ค้างท้ายติดตาม','V3 ยังไม่หมด'],tail_rows,small=True)}
    {caption('ค่าผลต่างเป็นบวกหมายถึง V3 มีงานค้างมากกว่า v2; tail AUC มีหน่วยงาน x นาที ตัวชี้วัดต้นทาง H แยกจากช่วงติดตามเสมอ')}
    <h2>กติกาที่ล็อกไว้ก่อนเห็นผล validation</h2>
    {table(['เกณฑ์','เงื่อนไข','ผล'],checks,small=True)}
    <div class="{headline_class}"><b>ผลรวมเกณฑ์ไปต่อ: {status(criteria['advancement_pass'])}</b><br>
    ต้องผ่านทุกข้อ ไม่สามารถใช้ผลดีเทียบ Static แทนเกณฑ์ไม่ด้อยกว่า v2 หรือเงื่อนไขความเสียหาย</div>
    <p class="small">เกณฑ์หลักและไม่ด้อยกว่าใช้ช่วงความเชื่อมั่น ส่วน surge, CVaR, tail AUC และท้าย H
    เป็นเกณฑ์วิศวกรรมจากค่าประมาณจุดที่ระบุไว้ล่วงหน้า มิได้อ้างความครอบคลุมทางสถิติพร้อมกันทุกเงื่อนไข
    เผื่อความคลาดเคลื่อนเชิงตัวเลข 1e-6 ตาม protocol และรายงานผลแยกทุกระยะย้าย</p>
    <p class="small">เวลาเฉลี่ยที่จำกัดไว้ 32 นาทีเป็นตัวชี้วัดภายในช่วงติดตามเท่านั้น
    ไม่ใช่ประมาณการเวลาระบายหมดแบบไม่จำกัด จึงต้องอ่านพร้อมสัดส่วนกรณีที่ยังเหลืองาน
    และไม่เฉลี่ยเฉพาะกรณีที่ระบายหมดแล้วเรียกว่าเวลาเฉลี่ยทั้งหมด</p>
    {evidence(['v3_protocol.json: acceptance_criteria','validation/decision.json','summary.json'])}
    '''
    dev=development['regimes']['instant'];do=dev['overall'];dr=dev['static_relative_harm']
    initial=read(ROOT/'v3_results/development_initial/summary.json')['regimes']['instant']
    cfg=protocol['controller_configuration']
    p7=f'''
    <p class="eyebrow">06 / DEVELOPMENT RECORD</p><h1>สิ่งที่ปรับจากชุดพัฒนาที่เคยเห็นแล้ว</h1>
    <p>ชุด validation ของ v2 ถูกตรวจไปแล้ว จึงนำมาใช้เป็นข้อมูลพัฒนารุ่นใหม่อย่างเปิดเผย
    การวินิจฉัย 176 กรณีทำซ้ำได้ตรงเดิม และใช้อนาคตจริงเฉพาะตัวประเมินออฟไลน์
    เพื่อตรวจว่าการย้ายแต่ละครั้งดีกว่าถือทรัพยากรเดิมในช่วงล็อก 8 นาทีหรือไม่</p>
    <p>จาก 752 การย้ายที่มีหน้าต่างครบ พบ 99 ครั้งที่ให้ผลเฉพาะช่วงแย่กว่าถือไว้
    กลุ่ม surge-only มี 11 จาก 21 ครั้ง โดย 9 ครั้งมีหลักฐานผู้รับเต็มกำลังไม่เกินหนึ่งครั้งก่อนย้าย
    อีกตัวอย่างหนึ่งคือการประมาณผู้ให้ฟื้นสู่ค่า nominal ทั้งที่ยังช้าอยู่ ผลเหล่านี้เป็นเหตุผลให้เพิ่มกฎหลักฐานและขอบความไม่แน่นอน</p>
    <h2>มีการแก้หนึ่งรอบก่อนล็อก validation</h2>
    <p>ตัวเลือกแรกทดลองเฉพาะ D=0 บน 44 กรณีที่รู้จักแล้ว รวม 352 episodes
    มันเข้มงวดกับทั้ง 33 แบบจำลองและนำทุกแบบมาเฉลี่ยคะแนน
    รุ่นที่ล็อกกลับไปใช้ 9 แบบพื้นฐานสำหรับจัดอันดับ รักษาการไม่แย่ลงใน 9 แบบนั้น
    อนุญาตความเสียหายจำลองในแบบ stress ได้สูงสุด 5% และเพิ่มเงื่อนไขไม่เพิ่มงานค้างท้ายการคาดการณ์โดยเฉลี่ยในแบบพื้นฐาน</p>
    {table(['พัฒนา / D=0','v2','V3 แรก','V3 ที่ล็อก'],[
      ['งานค้างเฉลี่ย',num(do['capacity_v2']['mean_backlog']),num(initial['overall']['v3_full']['mean_backlog']),num(do['v3_full']['mean_backlog'])],
      ['ย้ายเฉลี่ย',num(do['capacity_v2']['moves']),num(initial['overall']['v3_full']['moves']),num(do['v3_full']['moves'])],
      ['CVaR90 เทียบ Static',num(dr['capacity_v2']['cvar90']),num(initial['static_relative_harm']['v3_full']['cvar90']),num(dr['v3_full']['cvar90'])]],small=True)}
    <p class="small">ผลพัฒนาแสดงต้นทุนของความระมัดระวัง: surge-only เสมอ Static แต่ภาพรวมอาจเสียโอกาสแก้คอขวด
    เก็บผลตัวเลือกแรกและผลหลังแก้ไว้ครบ การทดสอบ seed ใหม่จึงประเมินตัวเลือกที่ล็อกแล้ว ไม่ได้ปรับจนผ่านบน validation</p>
    <p class="small">ผลออฟไลน์เฉพาะการย้ายหนึ่งครั้งไม่ได้เท่ากับผลของการลบการย้ายนั้นออกจากทั้ง episode
    เพราะการเปลี่ยนคิววันนี้เปลี่ยนสิ่งที่นโยบายเห็นและการตัดสินใจครั้งต่อไปด้วย
    คำอธิบายว่าตัวกรองทำให้คืนสมดุลช้าจึงเป็นสมมติฐานจากรูปแบบที่พบ ต้องทดสอบแยกหากจะระบุสาเหตุ</p>
    <p class="small">การแก้รอบพัฒนาทำให้ค่าเฉลี่ยดีขึ้นจากตัวเลือกแรก แต่ยังไม่ได้ผ่านเงื่อนไขที่ต้องการเทียบ v2
    งานรอบ validation มีหน้าที่วัดตัวเลือกนี้ตามกติกาเดิม การเก็บผลที่ไม่ผ่านช่วยป้องกันการเลือกเฉพาะภาพที่ดูดี
    และทำให้การทดลองถัดไปเริ่มจากข้อจำกัดที่ตรวจพบจริง</p>
    <h2>ตัวเลข 5% สองตัวมีความหมายต่างกัน</h2>
    <p><b>5% ใน stress model:</b> ค่า {num(100*cfg['stress_regression_fraction'],0)}% นี้เป็นกติกาออนไลน์
    ว่าตัวคาดการณ์อาจยอมให้ค่า cost ของบางมุมความไม่แน่นอนแย่กว่าการถือไว้เพียงใด
    โดยใช้ cost ของการถือไว้เป็นฐานและตั้งฐานต่ำสุดที่ 1 เป็นสมมติฐานวิศวกรรม
    ไม่มีความหมายว่าโอกาสเสียหายเท่ากับ 5%</p>
    <p><b>5% ในเกณฑ์ไม่ด้อยกว่า:</b> เป็นขอบเขตประเมินผลภายนอก เทียบ mean backlog ของ V3 กับ v2
    โดยใช้ขอบล่าง 95% CI ของอัตราลดลง ต้องไม่น้อยกว่า -5% สองค่านี้ไม่ได้ทดแทนกัน</p>
    {evidence(['V3_DEVELOPMENT_NOTES.md','v3_results/diagnosis/diagnosis.json','v3_results/development/summary.json'])}
    '''
    replay=verification['independent_trace_replay']
    causal=audit['checks']['observation_only_controller_replay']
    p8=f'''
    <p class="eyebrow">07 / VERIFICATION AND REUSE</p><h1>ตรวจสอบได้ และเก็บผลที่ไม่ผ่านไว้</h1>
    {table(['การตรวจ','ผลที่ยืนยัน'],[
      ['ชุดทดสอบ',f"{tests['tests_run']} tests ผ่าน / failures {tests['failures']} / errors {tests['errors']}"],
      ['กระบวนการจริง','Neuron Flow: 18 งานทดลอง + งานรวมผล / 3 workers'],
      ['Retry และเปิดงานซ้ำ','ล้มชั่วคราวก่อนคำนวณ 1 ครั้ง; เปิดใหม่ไม่ทำงานที่เสร็จซ้ำ'],
      ['ข้อมูลจงใจผิด','validator ปฏิเสธ; งานที่พึ่งพาถูกบล็อก; งานอิสระสำเร็จ'],
      ['Replay ทางกายภาพ',f"{replay['traces']} traces / {replay['minutes']:,} นาทีที่กำหนดไว้ล่วงหน้า"],
      ['Replay จากสิ่งที่เห็น',f"{causal['traces']} traces / {causal['decisions']:,} การตัดสินใจของ 3 วิธีใหม่"],
      ['ทำซ้ำชุดเต็ม',f"{repro['files_compared']} ไฟล์ผลหลักตรงกันทุกไบต์จากการรันใหม่"],
    ])}
    <p class="small">ทุก episode ตรวจทรัพยากร งานคงค้าง และบัญชีช่วงย้าย ตัวอย่าง traces ที่กำหนดไว้ตรวจซ้ำด้วยเลขคณิตอีกชุด
    และทวนการตัดสินใจจากสิ่งที่สังเกตเท่านั้น การผ่านการตรวจแสดงว่าโค้ดสอดคล้องกับแบบจำลองและกติกานี้
    ไม่ได้พิสูจน์ว่าแบบจำลองตรงกับบริการจริง</p>
    <p class="small">การรันซ้ำชุดเต็มเป็นหลักฐานว่าทำซ้ำได้ ไม่ได้เพิ่มจำนวนสถานการณ์อิสระ
    เวลาเหตุการณ์ runtime และระยะเวลารันไม่นำมาเทียบทุกไบต์ เพราะเปลี่ยนตามการทำงานจริง
    ช่วงความเชื่อมั่นสร้างจากโมดูลสถิติที่ล็อกไว้ การตรวจอิสระทวนค่าจุดและบัญชีผล ไม่ได้อ้างว่าเขียน bootstrap อีกชุด</p>
    <h2>ขอบเขตที่ยังไม่ได้ทดสอบ</h2>
    <p class="small">ทุกข้อมูลเป็นสังเคราะห์ และทุกวิธีแชร์คิว fluid ต่อเนื่อง 3 ขั้น งานเดินผ่านหลายขั้นได้ในนาทีเดียว
    ไม่มีการทิ้งงาน ไม่มีขนาดบัฟเฟอร์จำกัด และยังไม่รวมการแย่งทรัพยากร การเริ่มบริการใหม่ ความหน่วงการวัด หรือราคาเงินจริง
    ช่วงความไม่แน่นอน 15%/25% เป็นมุมทดสอบที่กำหนด ไม่ใช่ช่วงความเชื่อมั่นที่สอบเทียบแล้ว</p>
    <h2>ทำซ้ำผลจากแพ็กเกจ</h2>
    <pre>python -m pip install -r requirements.txt
python -m unittest -v test_lab test_capacity test_v3
python run_v3.py --split validation --out fresh_v3_results</pre>
    <p class="small">ใช้โฟลเดอร์ผลลัพธ์ใหม่ การทำซ้ำรุ่นที่ล็อกแล้วไม่ต้อง freeze ใหม่
    การแก้ตัวควบคุมหรือกติกาหลังเห็นผลต้องเริ่มการทดลองรุ่นถัดไป
    จุดที่ควรนำไปศึกษาต่อคือเหตุที่วิธีฐานเรียบง่ายได้ผลดี และเมื่อใดการยับยั้งการย้ายทำให้แก้คอขวดช้า</p>
    <h2>หลักฐานและพื้นฐานแนวคิด</h2>
    <p class="caption">ข้อมูลและโค้ดตั้งต้นของผู้ใช้: ABC Causal Service 0.2.0, Neuron Flow 0.1.0 และ minute trace สังเคราะห์
    ตรวจที่ <span class="file">source_checksums.sha256</span> และ <span class="file">provenance.json</span><br>
    แนวคิดการคาดการณ์เพื่อควบคุม: Rawlings, Mayne &amp; Diehl, <i>Model Predictive Control: Theory, Computation, and Design</i>
    <a href="https://sites.engineering.ucsb.edu/~jbraw/mpc/index.html">sites.engineering.ucsb.edu/~jbraw/mpc/</a>
    ใช้เป็นพื้นฐานทั่วไป ไม่ใช่หลักฐานรับรองวิธีนี้</p>
    {evidence(['validation/verification.json','independent_review.json','reproducibility.json','v3_results/unit_tests.json'])}
    '''
    return [p1,p2,p3,p4,p5,p6,p7,p8]


def is_pass(payload):
    return payload.get('status') in ('PASS','PASSED','SUCCEEDED') or payload.get('passed') is True


def audit_passed(audit,summary,verification):
    checks=audit['checks']
    names=('frozen_v3_and_parent_v2_identity_verified','upstream_sources_verified',
           'environment_manifest_verified','row_trace_summary_receipt_hashes_verified',
           'csv_rows_match_json','full_row_scope_and_accounting_verified')
    return (all(checks[name] is True for name in names)
        and checks['observation_only_controller_replay']['all_actions_and_full_evidence_match'] is True
        and checks['generated_underload']['passed'] is True
        and audit['scope']['episodes_checked']==summary['episodes']
        and audit['review_metadata']['summary_sha256']==verification['summary_sha256']
        and audit['review_metadata']['protocol_sha256']==verification['freeze_verification']['protocol_sha256'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'report_v030')
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'v3_results/validation'
    summary=read(source/'summary.json');verification=read(source/'verification.json')
    development=read(ROOT/'v3_results/development/summary.json');protocol=read(ROOT/'v3_protocol.json')
    decision=read(source/'decision.json');audit=read(source/'independent_review.json');repro=read(source/'reproducibility.json')
    tests=read(ROOT/'v3_results/unit_tests.json')
    if verification['status']!='SUCCEEDED' or not tests['successful']:
        raise RuntimeError('Completed validation and passing tests are required')
    if not audit_passed(audit,summary,verification) or not is_pass(repro) or not repro['all_byte_identical']:
        raise RuntimeError('Independent review and full rerun evidence must pass before rendering')
    digest=sha(source/'summary.json')
    if digest!=verification['summary_sha256']:
        raise RuntimeError('Summary digest differs from workflow receipt')
    rows=[]
    for path in sorted((source/'rows').glob('*.json')):rows.extend(read(path)['rows'])
    criteria=acceptance(summary,verification,tests,rows,True)
    external={
        'primary_pass':decision['primary']['passed'],
        'noninferiority_pass':decision['noninferiority']['passed'],
        'advancement_pass':decision['advancement_passed'],
    }
    mapping={'surge_pass':'surge_only_mean','mean_all_regimes_pass':'mean_vs_static_each_regime',
             'cvar_all_regimes_pass':'static_relative_cvar_each_regime','tail_auc_all_regimes_pass':'tail_auc_each_regime',
             'terminal_all_regimes_pass':'original_terminal_each_regime','integrity_underload_pass':'invariants_tests_underload'}
    external.update({key:decision['advancement_checks'][name]['passed'] for key,name in mapping.items()})
    if external!=criteria:
        raise RuntimeError('Report acceptance differs from independent decision')
    prepare_fonts(out);figures(summary,out)
    date=datetime.fromisoformat(protocol['frozen_at_utc']).astimezone(ZoneInfo('Asia/Bangkok'))
    bodies=pages(summary,development,verification,criteria,protocol,tests,audit,repro,date.strftime('%d/%m/%Y'))
    screen='''html {background:#E7EDF0;}section.page {box-sizing:border-box;width:210mm;min-height:297mm;background:white;padding:13mm 12.7mm;margin:9mm auto;}@media print {html {background:white;}section.page {margin:0;page-break-after:always;}}'''
    (out/'ANS_v030_Report_TH.html').write_text('<!doctype html><html lang="th"><head><meta charset="utf-8"><title>ANS v0.3.0</title><style>'+CSS+screen+'</style></head><body>'+''.join('<section class="page">'+body+'</section>' for body in bodies)+'</body></html>',encoding='utf-8')
    document=fitz.open();archive=fitz.Archive(str(out));checks=[]
    for number,body in enumerate(bodies,1):
        page=document.new_page(width=595.276,height=841.89)
        spare,scale=page.insert_htmlbox(fitz.Rect(36,35,559.276,793),body,css=CSS,archive=archive,scale_low=1)
        if spare<0 or scale!=1:raise RuntimeError(f'Page {number} overflow: spare={spare}, scale={scale}')
        page.draw_line(fitz.Point(36,807),fitz.Point(559.276,807),color=(.84,.89,.92),width=.5)
        footer='ANS v0.3.0  |  SYNTHETIC VALIDATION  |  '+date.strftime('%d %b %Y').upper()
        page.insert_text((36,822),footer,fontsize=7.5,fontname='helv',color=(.38,.47,.54))
        page.insert_text((535,822),f'{number} / 8',fontsize=7.5,fontname='helv',color=(.38,.47,.54))
        checks.append({'page':number,'spare_height_pt':spare,'scale':scale})
    document.set_metadata({'title':'ANS v0.3.0 - รายงานผลการทดลอง','author':'Adaptive Nervous System experiment',
        'subject':'Thai report of synthetic resource-transfer validation; conditional findings and failed criteria retained'})
    target=out/'ANS_v030_Report_TH.pdf';document.save(target,deflate=True,garbage=4);document.close()
    reopened=fitz.open(target);(out/'qa_pages').mkdir(exist_ok=True)
    for number,page in enumerate(reopened,1):page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(out/'qa_pages'/f'page_{number:02}.png')
    text='\n'.join(page.get_text() for page in reopened)
    if len(reopened)!=8 or pct(summary['regimes']['instant']['comparisons']['v3_full']['static']['reduction_pct']) not in text:
        raise RuntimeError('PDF text/page verification failed')
    write={'pages':len(reopened),'content_boxes':checks,'summary_sha256':digest,
           'protocol_sha256':verification['freeze_verification']['protocol_sha256'],'acceptance':criteria,
           'rendered_pngs':len(reopened),'visual_review':'required after rendering'}
    (out/'pdf_layout_checks.json').write_text(json.dumps(write,indent=2)+'\n')
    reopened.close();print(json.dumps({'pdf':str(target),'html':str(out/'ANS_v030_Report_TH.html'),'layout':checks,'acceptance':criteria},indent=2))


if __name__=='__main__':main()
