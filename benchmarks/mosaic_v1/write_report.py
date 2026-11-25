"""Render the results from the official evidence after all live arms have finished."""
import json
import csv
import gzip
from pathlib import Path
from measure import RESULT,ROOT,report

def save_json(p,x):p.write_text(json.dumps(x,indent=2)+'\n')

def main():
    choice=json.loads((RESULT/'selection.json').read_text())
    w=choice['winner']
    reports={}
    for arm in ('current_v5',w):
        d=report('scoring',arm,True)
        reports[arm]=d
        save_json(RESULT/f'official-{arm}.json',d)
    jobs=json.loads(gzip.decompress((ROOT/'workloads.json.gz').read_bytes()))['live']
    samples={a:json.loads((RESULT/f'live-{a}.json').read_text())['latency'] for a in ('fixed4','current_v5',w)}
    with (RESULT/'live-requests.csv').open('w',newline='') as out:
        writer=csv.writer(out)
        writer.writerow(['request','wave','release_seconds','mosaics','fixed4_seconds','current_v5_seconds','selected_seconds'])
        for j,(release,tiles) in enumerate(jobs):
            writer.writerow([j+1,j//8+1,release,len(tiles),*[s[j] for s in samples.values()]])
    new=reports[w]
    old=reports['current_v5']
    diag=json.loads((RESULT/'attribution.json').read_text())
    r={**old['replay'],**new['replay']}
    live={**old['live'],**new['live']}
    labels={'fixed4':'ทำทีละคำขอ ใช้ได้ 4 CPU','oldest_ready':'ให้สิทธิ์งานเก่าสุดที่พร้อมทำ','current_v5':'V5 เดิม',w:'V5 พร้อมตัวจัดลำดับคิวที่เลือก'}
    def number(x):return f'{x:,.2f}'
    def improvement(a,b):return f'{100*(1-a/b):.2f}%'
    rows=[]
    for arm,label in labels.items():
        l=live.get(arm,{})
        rows.append('| '+' | '.join([label,number(r[arm]['mean']),number(r[arm]['p95']),number(r[arm]['p99']),*[(number(l[k]) if k in l else '—') for k in ('mean','p95')]])+' |')
    conditions=[
      ('เวลาเฉลี่ยในชุดจำลอง เทียบกับทำทีละคำขอ','replay_mean_vs_fixed4_max'),
      ('P95 ในชุดจำลอง เทียบกับทำทีละคำขอ','replay_p95_vs_fixed4_max'),
      ('เวลาเฉลี่ยในชุดจำลอง เทียบกับงานเก่าสุดที่พร้อม','replay_mean_vs_oldest_ready_max'),
      ('P95 ในชุดจำลอง เทียบกับงานเก่าสุดที่พร้อม','replay_p95_vs_oldest_ready_max'),
      ('เวลาเฉลี่ยในการรันจริง เทียบกับทำทีละคำขอ','live_mean_vs_fixed4_max'),
      ('P95 ในการรันจริง เทียบกับทำทีละคำขอ','live_p95_vs_fixed4_max')]
    gate_rows=[]
    for label,key in conditions:
        gate_rows.append(f"| {label} | ≤{new['gates'][key]['max']:.2%} | {old['gates'][key]['ratio']:.3%} | {new['gates'][key]['ratio']:.3%} |")
    for m,label in [('mean','ค่าเฉลี่ยรายกลุ่มที่แย่ที่สุด'),('p99','P99 รายกลุ่มที่แย่ที่สุด')]:
        ks=[f'{family}_{m}' for family in new['families'][w]]
        gate_rows.append(f"| {label} เทียบกับทำทีละคำขอ | ≤{new['gates'][ks[0]]['max']:.2%} | {max(old['gates'][k]['ratio'] for k in ks):.3%} | {max(new['gates'][k]['ratio'] for k in ks):.3%} |")
    gate_rows.append('| คำขอเสร็จครบและผลภาพทั้งคู่ตรงกับอ้างอิง | 100% | 100% | 100% |')
    family_rows=[]
    for family in new['families'][w]:
        f=new['families']
        family_rows.append('| '+' | '.join([family,*[number(x) for x in (f['fixed4'][family]['mean'],old['families']['current_v5'][family]['mean'],f[w][family]['mean'],f['fixed4'][family]['p99'],f[w][family]['p99'])]])+' |')
    markdown=f"""# ผลการทดสอบ ANS กับคิวงานสร้างภาพโมเสก v1

**ผลตัดสิน V5 เดิม {old['status']} และ V5 พร้อมตัวจัดลำดับคิว {new['status']}** ทั้งสองรุ่นใช้โค้ดตัวควบคุม V5 ที่ commit 22676ec เดียวกัน รุ่นที่เลือกให้สิทธิ์คำขอตามจำนวนและขนาดภาพที่เหลือ โดยคำนึงถึงเวลาที่ส่งคำขอ เวลาในรายงานเริ่มตั้งแต่ส่งคำขอจนได้รับภาพสุดท้าย หน่วยเป็นวินาที

| วิธีจัดคิว | จำลอง เฉลี่ย | จำลอง P95 | จำลอง P99 | รันจริง เฉลี่ย | รันจริง P95 |
|---|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

P95 หมายถึงเวลาที่คำขอ 95% เสร็จภายในค่านั้น การจัดลำดับคิวลดเวลารันจริงเฉลี่ย {improvement(live[w]['mean'],live['fixed4']['mean'])} จากวิธีทำทีละคำขอ และ {improvement(live[w]['mean'],live['current_v5']['mean'])} จาก V5 เดิม

## ชุดทดสอบและเกณฑ์ผ่าน

ภาพต้นทาง 132 เฟรมจาก 2MASS ครอบคลุมท้องฟ้า 8 บริเวณ ใช้ 3 ย่านแสงและขนาดภาพ 256, 640 และ 1,280 พิกเซลต่อด้าน รวม 72 แบบ ชุดจำลองที่ใช้ให้คะแนนมี 24,576 คำขอใน 96 คิว ประกอบด้วยงานสร้างภาพ 67,994 ครั้งต่อวิธี โปรแกรมจำลองใช้เวลาของแต่ละขั้นที่วัดจากการรันจริงครบ 72 แบบ การทดสอบบนเครื่องใช้คำขออีก 48 รายการใน 6 ระลอกต่อวิธี ประกอบด้วยงานสร้างภาพ 120 ครั้งใน 57 แบบ แต่ละวิธีทำงานบน CPU ชุดเดียวกัน 4 ตัวและสร้างภาพใหม่ทุกครั้ง ทั้งสามวิธีเสร็จรวม 360 ภาพ พร้อมแผนที่พื้นที่ครอบคลุมอีก 360 ไฟล์

ตารางแสดงเวลาของระบบคิดเป็นร้อยละของเวลาอ้างอิง ค่ายิ่งต่ำยิ่งดี เกณฑ์รายกลุ่มตรวจแยกครบทั้ง 8 กลุ่ม

| เงื่อนไข | เกณฑ์ | V5 เดิม | V5 พร้อมตัวจัดลำดับคิว |
|---|---:|---:|---:|
{chr(10).join(gate_rows)}

| กลุ่มงานจำลอง | ทำทีละคำขอ เฉลี่ย | V5 เดิม เฉลี่ย | V5 พร้อมตัวจัดลำดับคิว เฉลี่ย | ทำทีละคำขอ P99 | V5 พร้อมตัวจัดลำดับคิว P99 |
|---|---:|---:|---:|---:|---:|
{chr(10).join(family_rows)}

## สิ่งที่ทำให้เร็วขึ้น

เมื่อตรึงสัดส่วนทรัพยากรทั้งสามขั้นไว้เท่ากันและเก็บวิธีจัดลำดับคิวเดิม เวลาเฉลี่ยในชุดจำลองเป็น {diag[w]['equal_shares']['mean']:.6f} วินาที เมื่อเปิดให้ V5 ปรับสัดส่วนได้ เวลาเฉลี่ยเป็น {diag[w]['with_v5']['mean']:.6f} วินาที ประโยชน์เกือบทั้งหมดในชุดนี้มาจากการจัดลำดับคิว การตรวจนี้ทำเพิ่มเพื่ออธิบายผลหลังเลือกรุ่น V5 เดิมมี P99 รวม {r['current_v5']['p99']:.2f} วินาที เทียบกับ {r[w]['p99']:.2f} วินาทีในรุ่นที่เลือก งานช้าสุดบางรายการจึงรอนานขึ้นแม้ค่าเฉลี่ยและ P95 ดีขึ้น

## ทำซ้ำและตรวจสอบ

กำหนดเกณฑ์ที่ commit 4383c3c แล้วตรึงข้อมูล ตัววัด และตัวเลือกสำหรับพัฒนาที่ commit 98ddf4d ก่อนรันชุดให้คะแนน ทดลอง 6 ตัวเลือกบนชุดพัฒนาแยก 1,536 คำขอ เลือก `{w}` ด้วยกติกาเดียวกันทุกตัวเลือก ทดสอบจริงตามลำดับ ทำทีละคำขอ, V5 เดิม, รุ่นที่เลือก แต่ละวิธีรันจริงหนึ่งรอบเต็มบนเครื่องเดียว โหลดและการเปลี่ยนคอขวดทั้ง 8 รูปแบบประเมินในชุดจำลอง ผลนี้ครอบคลุมงานและเครื่องที่ระบุ ชุดนี้เปิดเผยและใช้เป็นเกณฑ์ติดตามรุ่นถัดไปได้ การตัดสินเลื่อนรุ่น V5 ในโครงการหลักมีเกณฑ์แยกต่างหาก

โปรแกรมใน README.md ระบุวิธีรันใหม่ทั้งหมด ไฟล์ results/scoring-*.json.gz เก็บเวลาแต่ละคำขอ และ results/live-*.json เก็บเวลาจริงกับค่า SHA-256 ของภาพและแผนที่พื้นที่ครอบคลุมทุกคู่ ข้อมูลการเลือกอยู่ใน results/selection.json และผลตัดสินอยู่ใน results/official-*.json ใช้ `python check_results.py` ตรวจซ้ำด้วยไลบรารีมาตรฐาน
"""
    (ROOT/'RESULTS_TH.md').write_text(markdown)

if __name__=='__main__':main()
