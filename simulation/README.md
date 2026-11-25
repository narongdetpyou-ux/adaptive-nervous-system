# Adaptive Nervous System v0.4.0

## ผล: ลด CVaR90 ได้ชัดเจน แต่ยังไม่ผ่านเกณฑ์ยกระดับทั้งหมด

ในเงื่อนไขที่รวมข้อจำกัดครบ (`delayed_noisy`) V4 hybrid ลด Static-relative CVaR90 จาก V3 **20,899.18 เป็น 13,199.35 หน่วย หรือ 36.84%** ความต่างเท่ากับ **7,699.82** มี paired 95% interval **[5,673.51, 9,924.06]** ซึ่งผ่านเกณฑ์หลักที่ตรึงไว้ก่อนทดสอบ ค่าเฉลี่ย demand deficit ต่ำกว่า V3 **4.20%** (95% interval ของการลด **3.36–5.07%**) V3 และทุก baseline ถูกนำมารันใหม่บนกรณีเดียวกัน; ไม่ได้เปรียบเทียบตัวเลขข้ามชุดข้อมูล

**ผลจาก evaluator: ไม่ผ่านเกณฑ์ยกระดับทั้งหมด** ข้อที่ไม่ผ่านเพียงหมวดเดียวคือ loss/endpoints เมื่อเทียบกับ Capacity balance โดยมี 15 คู่ย่อยไม่ผ่าน: end deficit และ tail AUC ในทั้งหกระดับ รวมกับ dropped work ในสามระดับที่มี finite buffer ไม่มีการลดเกณฑ์หรือปรับ policy หลังเปิดผลนี้

### CVaR90 ในชุดใหม่ 704 กรณีต่อระดับ

ค่าต่ำกว่าดีกว่า แต่ละแถวใช้สถานการณ์และ physics เดียวกัน ตัวเลขนี้คือความเสียหายเพิ่มจาก Static ในส่วนแย่ที่สุด 10% ไม่ใช่ค่าเฉลี่ยงานค้างทั้งหมด และค่า Static เป็นศูนย์ตามนิยาม จึงต้องอ่านควบคู่กับค่าเฉลี่ย งานสูญหาย และงานปลายทาง

| Regime | V3 | V2 | Capacity balance | V4 hybrid | V4 ลดจาก V3 |
|---|---:|---:|---:|---:|---:|
| legacy_instant | 4,762.59 | 1,707.46 | 2,182.24 | 444.27 | 90.67% |
| legacy_transit3 | 15,120.65 | 6,597.13 | 6,046.31 | 3,404.74 | 77.48% |
| integer_transit3 | 14,909.67 | 6,198.40 | 6,166.29 | 3,308.92 | 77.81% |
| finite_pipeline | 21,057.05 | 46,061.87 | 12,661.73 | 9,454.73 | 55.10% |
| startup | 21,779.23 | 47,983.72 | 12,763.06 | 10,582.13 | 51.41% |
| delayed_noisy | 20,899.18 | 46,382.66 | 13,550.43 | 13,199.35 | 36.84% |

V4 มี point CVaR90 ต่ำกว่าทั้ง V3, V2 และ Balance ทุกระดับ รวม18/18คู่ผ่าน เงื่อนไขนี้เป็น point engineering check ไม่ใช่ข้อกล่าวอ้างว่าทุกคู่แตกต่างทางสถิติ: ในระดับสุดท้าย ความต่าง CVaR90 ของ Balance ลบ V4 เท่ากับ351.07 แต่95% interval[-1,339.24,1,976.32] ยังคร่อมศูนย์

### ผลเทียบ Capacity balance ในระดับที่รวมข้อจำกัดครบ

| ตัวชี้วัด | Capacity balance | V4 hybrid | ความหมาย |
|---|---:|---:|---|
| Mean demand deficit | 47,620.68 | 48,571.45 | V4 สูงกว่า2.00% |
| Total dropped work | 45,360.78 | 49,486.03 | V4 สูงกว่า9.09% |
| Original terminal deficit | 51,586.83 | 55,712.08 | V4 สูงกว่า8.00% |
| Tail deficit AUC | 1,454,657.21 | 1,586,665.11 | V4 สูงกว่า9.07% |

เกณฑ์ mean noninferiority ผ่านเพราะกำหนดขอบยอมรับไว้5% แต่ V4 ไม่ได้มีค่าเฉลี่ยเท่ากับหรือดีกว่า Balance: ช่วง95%ของการลด mean deficit อยู่ที่[-2.83%,-1.24%] ซึ่งหมายถึง V4 มี mean deficit สูงกว่า ขอบล่างของ allowance slack ยังเป็นบวกจึงผ่านเงื่อนไข5%

ในสามระดับที่มี finite buffer ความต่าง tail AUC เท่ากับ32เท่าของงานที่ถูกทิ้งเพิ่ม งานปลายทางกับ tail จึงเชื่อมกันผ่านความเสียหายถาวรเดียวกัน ไม่ใช่หลักฐานอิสระสามชุดว่าระบบเสียหายมากขึ้น

### ผลเกณฑ์ที่ตรึงไว้

| หมวด | ผล |
|---|---|
| CVaR90 เทียบ V3 ใน delayed_noisy ตาม95% CI | ผ่าน |
| Point CVaR90 ไม่สูงกว่า V3/V2/Balance ทุกระดับ | ผ่าน18/18คู่ |
| Mean deficit ไม่ด้อยกว่า V2/Balance เกิน5% ตาม95% CI | ผ่าน12/12คู่ |
| Drops, end deficit, tail AUC ไม่สูงกว่า V2/Balance | ผ่าน21/36คู่; ไม่ผ่าน15คู่ ทั้งหมดเทียบBalance |
| Integrity | ผ่าน6/6เงื่อนไข |
| Underload ไม่มีการย้ายและไม่มีงานถูกทิ้ง | ผ่าน384/384episodes |
| ต้องผ่านทุกหมวดพร้อมกัน | **ยังไม่ผ่าน** |

### ผลของกลไกย่อยยังไม่ชัดทุกส่วน

ในระดับสุดท้าย การเอา risk penalty ออกให้ CVaR90=13,254.48 เทียบhybrid13,199.35 ความต่าง55.13มี95% interval[-223.39,369.40] การเอา recovery ทั้งสองส่วนออกให้ CVaR90=13,147.07 ซึ่งต่ำกว่าhybridเล็กน้อย ความต่าง no-recovery ลบ hybrid เท่ากับ-52.28 มี interval[-127.92,2.22] ทั้งสองช่วงคร่อมศูนย์ จึงไม่ควรอ้างว่ากลไกใดกลไกหนึ่งเป็นสาเหตุที่ยืนยันแล้วของการลดCVaRเมื่อเทียบV3 รุ่นใหม่ยังเปลี่ยน horizon, hurdle, uncertainty handling และ forecast physics ร่วมด้วย

### ความเสี่ยงที่ยังเหลือ

การแยก contribution ของ global CVaR90 ในระดับสุดท้ายพบ generated_switching_fault 56.78%, shifting_bottleneck 33.78% และ generated_sustained_overload 9.44% สองกลุ่มที่คอขวดเปลี่ยนตำแหน่งรวมประมาณ 90.56% เป็นจุดที่ควรตรวจต่อ แต่อัตราส่วนนี้เป็นการอธิบายผล ไม่ได้พิสูจน์สาเหตุเชิงกลไก

ตัวอย่างที่ไม่ควรซ่อน: switching-fault seed 4015 มี hybrid mean deficit 75,052.24 เทียบ Static 53,124.20, V3 57,680.03 และ Balance 45,246.35 ดังนั้นความเสี่ยงรวมที่ลดลงไม่ได้รับประกันว่าทุกกรณีจะดีขึ้น เก็บห้ากรณีที่ hybrid เสียหายมากที่สุดต่อระดับและตัวเลขทุก baseline ใน `v4_results/validation/tail_attribution.json`

หลักฐานหลัก: `v4_results/validation/summary.json`, `decision.json`, `independent_review.json`, `tail_attribution.json` และ `report_v040/ANS_v040_Report_TH.pdf` การตรวจอิสระไม่พบความคลาดเคลื่อน และยืนยันครบ 29,568 episodes, 462 traces, 51,576 decisions กับสถิติที่ใช้ตัดสินผล

### การทำซ้ำและการกู้คืนงาน

รอบทำซ้ำครบชุดให้ไฟล์ผลลัพธ์ที่กำหนดแน่นอนทั้ง **88 ไฟล์ตรงกับรอบหลักทุกไบต์** รวม summary, CSV, scenario manifest, trace index และข้อมูล rows/traces ของทั้ง 42 คู่ policy–regime ผลตรวจอยู่ใน `v4_results/validation/reproducibility.json` การทำซ้ำนี้ใช้กรณีเดิม จึงไม่เพิ่มจำนวนสถานการณ์อิสระทางสถิติ

รอบทำซ้ำมีการขัดจังหวะทางเทคนิคหลัง 41 evaluations สำเร็จแล้ว และทำต่อผ่านกลไกกู้คืนของ Runtime เดิมจนจบ โดยเก็บทั้ง 41 สถานะและ 123 ไฟล์ที่รับรองแล้วไว้ครบ ทั้ง hash, ขนาด และเวลาแก้ไข ขั้นตอน `delayed_noisy__v4_no_recovery` จบด้วย attempt 2 ไม่มีการเปลี่ยน specification, retry budget, frozen policy หรือเกณฑ์ตัดสิน สาเหตุของการขัดจังหวะและเวลารวมของ session เดิมยังไม่ทราบ หลักฐานแยกอยู่ใน `v4_results/reproduction_recovery/` และ `v4_results/recovery_independent_evidence_check.json` จึงไม่เรียกรอบนี้ว่าเป็นการรันต่อเนื่องโดยไม่มีการขัดจังหวะ


## สิ่งที่ทำในรุ่นนี้

รุ่นนี้ต่อยอดจาก v0.3 เพื่อแก้การรอจนความเสียหายสะสม ผสานการจัดสรรตาม Capacity balance กับการประเมินความเสี่ยงของการย้าย และขยายตัวจำลองให้มีข้อจำกัดทางกายภาพและข้อมูลวัดที่ไม่สมบูรณ์ ผลทั้งหมดเป็นข้อมูลจำลอง โค้ดไม่มีตัวเชื่อมที่สั่งย้ายทรัพยากรในระบบจริง

หลักฐานและโค้ด v0.1, v0.2 และ v0.3 อยู่ครบ เอกสารรุ่นเดิมแยกเป็น `README_v010.md`, `README_v020.md`, `README_v030.md` ตัวอ่าน shadow telemetry เดิมเป็นของ v0.3 ไม่ใช่ตัวควบคุม v0.4 ที่ทดสอบในรายงานนี้

## เริ่มรัน

### Google Colab

เปิด `START_COLAB_v040.ipynb` ใน Google Colab แล้วอัปโหลด `Adaptive_Nervous_System_v040.zip` เมื่อเซลล์ขอ รันตามลำดับ ใช้ CPU ไม่ต้องใช้ GPU หรือ API key Notebook ตรวจไฟล์และ frozen protocol รัน tests ใหม่ใน session นั้น แล้วรัน validation, independent review และเกณฑ์ PASS/FAIL โดยอัตโนมัติ ตารางและกราฟแสดงทั้งหกสภาพแวดล้อมแยกกัน

Notebook เก็บผล tests และผลทดลองใหม่ในโฟลเดอร์ภายนอกโครงการ และส่งผล tests ใหม่นั้นให้ผู้ตรวจผลโดยตรง การทำซ้ำเต็มอีกครั้งเป็นทางเลือกซึ่งปิดไว้เป็นค่าเริ่มต้น ไม่เพิ่มจำนวนกรณีอิสระทางสถิติ ตรวจ syntax ทุก code cell และคำสั่งหลักในเครื่องนี้แล้ว รวมถึงรันเซลล์แสดงผลกับข้อมูลพัฒนาจริงได้ 39 ตารางและ 6 กราฟ แต่ไม่ได้รันหน้าจอ Google Colab โดยตรง

### คอมพิวเตอร์

Python 3.11 ขึ้นไป; สภาพแวดล้อมที่ตรวจจริงคือ Python 3.12.14 และ NumPy 2.3.5:

```sh
python -m pip install -r requirements.txt
python -m unittest -v test_lab test_capacity test_v3 test_v4 test_v4_metrics
python freeze_v4.py --verify
python run_v4.py --split validation --out fresh_validation
python review_v4_results.py --out fresh_validation --report fresh_validation/independent_review.json
python evaluate_v4.py --results fresh_validation --review fresh_validation/independent_review.json --out fresh_validation/decision.json
```

`fresh_validation` ต้องเป็นโฟลเดอร์ใหม่หรือว่าง ผู้ตรวจและ evaluator ในคำสั่งข้างต้นใช้หลักฐาน tests ที่แถมมาโดยค่าเริ่มต้น คำสั่ง unittest ไม่ได้สร้าง JSON ให้เอง หากต้องการผูกผลกับ tests รอบใหม่ ให้ใช้ notebook หรือส่ง `--tests` และ `--test-log` ให้ reviewer และ `--tests` ให้ evaluator

การทำงานของ workflow สำเร็จไม่เท่ากับผ่านเกณฑ์ความสามารถ ต้องอ่าน `decision.json` ทุกครั้ง โปรโตคอลถูกตรึงแล้ว ไม่ต้องสร้าง freeze ใหม่ การแก้ไฟล์หลักจะทำให้ validation ปฏิเสธ ควรแยกรุ่นและชุดทดสอบใหม่เมื่อปรับ policy หลังเปิดผลนี้

## แก้การรอที่สร้างความเสียหายอย่างไร

การตรวจย้อนหลังพบเกณฑ์ย้ายของ V3 ที่ผูกกับสต็อกงานค้าง เมื่อคิวสะสมสูง เกณฑ์ก็เพิ่มขึ้นแม้ประโยชน์ที่ทำได้ในหน้าต่างสั้นยังเท่าเดิม ประกอบกับขอบอัตราที่คงค่าต่ำจากเหตุการณ์เก่านานเกินไป จึงเกิดการถือ allocation ที่เสียเปรียบเป็นเวลานาน ดู `V4_DIAGNOSIS_NOTES.md` และ `v4_results/diagnosis/`

Hybrid ใช้ตัวประมาณอัตราและนาฬิกาการย้ายชุดเดียว ประเมินทั้ง hold และการย้ายด้วยหน้าต่าง 16 นาที คิดเวลาขนย้าย การเริ่มทำงาน คิวจำกัด และงานส่งต่อใน forecast แล้วเลือกข้อเสนอจาก catalog เดิม หากข้อเสนอที่เข้าใกล้สัดส่วน Capacity balance มีคะแนนอย่างน้อย 90% ของข้อเสนอที่ดีที่สุด ก็ให้ความสำคัญกับข้อเสนอนั้น

เปลี่ยนเกณฑ์ประโยชน์ขั้นต่ำเป็นค่าที่อิงงานเข้าล่าสุด แทนคิวสะสมทั้งหมด หักคะแนนด้วยความเสียหายคาดการณ์ในส่วนแย่ที่สุด 25% ของ stress forecasts เมื่อมีแรงกดดันต่อเนื่องและ allocation ยังเสียสมดุล จะลดน้ำหนักความเสี่ยงและเกณฑ์ลงได้ แต่ไม่น้อยกว่า 25% ของค่าปกติ เงื่อนไขทรัพยากรขั้นต่ำและช่วงเว้นการย้ายยังคงอยู่

ขอบจาก busy observation เก่าหมดอายุหลัง 8 นาทีของเวลาต้นทาง การลบ anchor ต่ำที่เก่าอาจทำให้ช่วงแคบลง ไม่ได้หมายความว่ามีหลักฐานการฟื้นตัวจริง ส่วนข้อมูลน้อยหรือมี lag ทำให้แถบรอบค่าประมาณกว้างขึ้นต่างหาก เส้นทาง idle recovery ใช้เมื่อคิวปลายช่วงที่วัดได้รวมไม่เกิน 10 หน่วยติดต่อกัน 3 ครั้ง และยังต้องผ่านการตรวจ forecast ทุกข้อ ไม่ได้หมายถึงคิวจริงเป็นศูนย์เสมอ

**คะแนน stress ภายใน policy ไม่ใช่ empirical CVaR90** ที่ใช้ตัดสินผล และไม่ใช่ความน่าจะเป็นที่สอบเทียบกับข้อมูลจริง รุ่น `v4_no_risk` ตัดเฉพาะน้ำหนักคะแนน stress ส่วน `v4_no_recovery` ตัดทั้ง pressure escape และ idle recovery พร้อมกัน จึงแยกผลของ recovery สองส่วนนี้ออกจากกันไม่ได้

## ข้อจำกัดที่เพิ่มในตัวจำลอง

แต่ละแถวรวมข้อจำกัดของแถวก่อนหน้า ยกเว้นแถวแรกเป็นฐานเดิม:

| Regime | ข้อจำกัด |
|---|---|
| legacy_instant | ระบบของไหลเดิม ทรัพยากรเป็นเศษส่วน ย้ายทันที งานผ่านหลายขั้นในนาทีเดียว |
| legacy_transit3 | ย้ายทรัพยากรแล้วใช้งานไม่ได้ 3 ช่วงบริการ |
| integer_transit3 | ทรัพยากรจำนวนเต็ม ปัด allocation ด้วย largest remainder ที่ให้ผลแน่นอน |
| finite_pipeline | ส่งงานข้ามขั้นใช้ 1 ช่วงเต็ม; ความจุคิว 60,000 / 40,000 / 40,000 หน่วย งานเกินถูกทิ้งและบันทึกไว้ |
| startup | ทรัพยากรใหม่ทำงาน 50% และ 75% ในสองช่วงแรกหลังถึงปลายทาง แล้วจึงครบ 100% |
| delayed_noisy | ข้อมูลวัดช้า 2 ช่วง มี mean-one lognormal multiplicative noise โดย log standard deviation = 0.05 |

ค่าตัวเลขเหล่านี้เป็นสมมติฐาน stress test ไม่ใช่การวัดจากระบบจริง ไม่ใช่ค่า default ของ Kubernetes ทรัพยากรเป็นจำนวนเต็มแต่ปริมาณงานและบริการยังเป็นของไหลต่อเนื่อง ไม่ใช่ตัวจำลองงานรายชิ้น

ทรัพยากรรวม 207 เริ่ม 69/69/69 กันขั้นต่ำขั้นละ 10 warmup 5 นาที และเว้นการย้าย 8 นาที ทุก policy ใช้ physics และ sensor noise ร่วมกันภายในกรณีเดียวกัน ตัวประมาณใช้ flow หารด้วย effective resources ณ นาทีต้นทางเดียวกับ flow รวมช่วง startup ข้อมูล allocation ที่สั่งเองรู้ได้ทันที แต่ข้อมูลวัดอื่นเป็นไปตาม lag ไม่มี true productivity, label สถานการณ์ หรือข้อมูลอนาคตส่งเข้า controller

## วัดงานค้างและความเสียหายโดยไม่ซ่อนงานที่ถูกทิ้ง

ในแต่ละนาที:

`demand deficit = offered cumulative - completed cumulative = retained queues + in-flight work + cumulative dropped work`

ตัวชี้วัดหลัก M คือ mean deficit เฉพาะช่วงเดิม 60 หรือ 96 นาที งานที่ถูกทิ้งไม่ถูกนับว่าเสร็จและไม่หายจาก M ส่วน `mean_queue` คือ retained work รวมงานส่งต่อ แต่ไม่รวมงานที่ถูกทิ้ง

Harm ของ policy ในแต่ละกรณีคือ `max(M_policy - M_static, 0)` CVaR90 เฉลี่ย harm ในส่วนแย่ที่สุด 10% ของกรณีทั้งหมด ใช้น้ำหนักเศษส่วนที่ขอบ ไม่ใช่เฉลี่ยเฉพาะกรณีแพ้ และไม่ใช่ CVaR ของความต่างระหว่าง hybrid กับ V3 โดยตรง

ติดตามอีก 32 นาทีโดยไม่รับงานเข้าใหม่และคง productivity แถวสุดท้าย ตัวควบคุมไม่รู้เวลาสลับช่วงนี้ Drain time ใช้ retained work เท่านั้น กรณีที่ยังระบายไม่หมดถูก censor ที่ 32 และรายงานสัดส่วนควบคู่ หากงานเคยถูกทิ้ง physical queue อาจหมดแล้วแต่ demand deficit ยังเป็นบวกได้

Transit resource-minutes และ startup capacity-minutes รายงานแยกกัน กำลังสูญเสียเกิดขึ้นใน physics โดยตรง ไม่บวกค่าปรับซ้ำ ตัวชี้วัดเหล่านี้ไม่ใช่ค่าใช้จ่ายเป็นเงินหรือ HTTP latency

## การพัฒนาและการประเมิน

พัฒนาบน 88 กรณีที่ทราบแล้วจาก V3 เปรียบเทียบ risk weight 0.1/0.3/0.6 กับ transfer cap 20/30 รวม 6 แบบและ 5,280 episodes เก็บทุกผลรวมถึงแบบที่ไม่ดี เลือกด้วยลำดับเกณฑ์ที่ประกาศไว้ก่อนรัน ไม่มีการเลือกด้วยมือ ดู `V4_DEVELOPMENT_NOTES.md` และ `v4_results/development_grid/selection.json`

จากนั้นรันแบบที่เลือกครบ 7 policies × 6 regimes × 88 กรณี = 3,696 episodes ก่อนตรึง source/config/เกณฑ์ แล้วประเมินกรณีใหม่ 704 กรณี (11 กลุ่ม × seeds 4000–4063) รวม **29,568 episodes / 3,300,864 นาทีจำลอง** ผลหก regimes ของกรณีเดียวกันไม่ใช่ตัวอย่างอิสระหกเท่า

ใช้ paired family-stratified bootstrap 2,000 ครั้ง โดยผลทุก policy ของกรณีเดียวกันถูกสุ่มร่วมกัน รายงานแต่ละ regime, source slice และทุก family แยกกัน ช่วงดังกล่าวครอบคลุมความแปรผันของกรณีในโมเดลนี้ ไม่ครอบคลุมความไม่แน่นอนว่าโมเดลเหมือนระบบจริงเพียงใด

เงื่อนไขหลักคือขอบล่าง 95% ของ CVaR90 ที่ลดจาก V3 ใน delayed_noisy ต้องเป็นบวก นอกจากนี้ CVaR90 จุดประมาณต้องไม่แย่กว่า V3/V2/Balance ทุก regime; ค่าเฉลี่ยต้องไม่ด้อยกว่า V2 และ Balance เกิน 5% ตามเกณฑ์ช่วงความเชื่อมั่น; dropped work, end deficit และ tail AUC ต้องไม่แย่กว่าทั้งสองวิธี; integrity และ underload ต้องผ่านทุกข้อ การทดสอบรองและ ablations เป็นข้อมูลอธิบาย ไม่ได้ปรับ multiple comparisons

## ตรวจความถูกต้องและทำซ้ำ

79 tests ครอบคลุม source เดิม, controller, physics ใหม่, sensor boundary, forecast, causality, statistics และ evaluator ผู้ตรวจอิสระตรวจแถวผลและขอบเขตทั้งหมด พร้อมสร้าง physics และ replay การตัดสินใจจากข้อมูลวัดซ้ำบน traces ตัวแทนที่เลือกไว้ก่อนดูผล จำนวน 462 traces / 51,576 decisions ไม่อ้างว่าทุกนาทีของทุก episode ถูกจำลองอิสระซ้ำ

ผู้ตรวจสถิติคำนวณ point summaries และช่วง bootstrap ที่ใช้ตัดสินผลขึ้นใหม่อีกชุดหนึ่ง การทดสอบเชิงลบแก้ completed count +1 ในสำเนาแล้วตรวจว่าถูกปฏิเสธและบล็อกเฉพาะงานที่พึ่งพา Neuron Flow เดิมทำงานจริงด้วย 3 workers พร้อมทดสอบ retry, duplicate submission และ restart

```sh
python run_v4.py --split validation --out fresh_repeat
python check_v4_reproducibility.py --first fresh_validation --second fresh_repeat --report fresh_validation/reproducibility.json
```

ตัวเปรียบเทียบตรวจ 88 ไฟล์ deterministic ไม่เปรียบเทียบเวลารัน, event timestamps หรือ absolute paths การทำซ้ำเป็นหลักฐานความทำซ้ำได้ของกรณีเดิม ไม่เพิ่มกรณีอิสระ

## ไฟล์สำคัญ

| ไฟล์ | หน้าที่ |
|---|---|
| v4_policy.py | Hybrid, risk scoring, balance preference, recovery และ ablations |
| v4_environment.py, v4_simulation.py, v4_scenarios.py | Physics, observation delivery, work/resource accounting และ cases |
| v4_metrics.py, evaluate_v4.py | ตัวชี้วัด สถิติ และเกณฑ์ตัดสินที่ตรึง |
| v4_agents.py, run_v4.py | การทดลองผ่าน Neuron Flow เดิม |
| audit_v4.py, test_v4.py, test_v4_metrics.py, review_v4_results.py | Tests และการตรวจอิสระ |
| develop_v4.py, diagnose_v3.py | การพัฒนาที่เก็บทุกผลและการวิเคราะห์ V3 เดิม |
| v4_protocol.json, v4_protocol.sha256, v4_selected_config.json | Frozen identity และค่าที่เลือก |
| v4_acceptance_predeclared.json | เกณฑ์ที่ประกาศก่อนผล development |
| v4_results/validation/ | ข้อมูลราย episode, CSV, summary, decision, audits และ reproducibility |
| report_v040/ | รายงานไทยและกราฟจากผลจริง |
| describe_v4_tail.py | การอธิบาย global CVaR90 แยกตาม family และกรณีเสียหายมากสุด โดยไม่รัน policy ใหม่ |
| V4_RESEARCH_NOTES.md | แหล่งข้อมูลปฐมภูมิและขอบเขตการนำแนวคิดมาใช้ |

## สร้างรายงานซ้ำ

```sh
python -m pip install -r report_requirements.txt
python build_v4_report.py --out fresh_report
```

ตัวสร้างรายงานใช้หลักฐานที่ตรวจแล้ว ไม่ปรับหรือรัน policy ใหม่ ฟอนต์ Noto และประกาศลิขสิทธิ์รวมอยู่ในแพ็กเกจ

## ขอบเขตที่ยังเหลือ

ยังไม่มีข้อมูลการทำงานจริงสำหรับสอบเทียบ arrivals, productivity, lag, noise, transfer/startup หรือความจุคิว ไม่มีงานรายชิ้น, request latency, retry/backpressure, resource heterogeneity, shared contention, failure ระหว่างย้าย หรือค่าใช้จ่ายทางธุรกิจ การเพิ่มข้อจำกัดใน simulator ช่วยทดสอบสมมติฐาน แต่ยังไม่พิสูจน์ประสิทธิภาพใน production และไม่สืบทอดหลักประกันทางทฤษฎีจากงาน scheduling หรือ MPC ที่อ้างใน research notes
