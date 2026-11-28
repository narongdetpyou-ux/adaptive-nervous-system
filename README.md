# Adaptive Nervous System (ANS)

**ANS ศึกษาว่าระบบงานที่มีหลายขั้นตอนต่อกัน ควรย้ายกำลังคนหรือกำลังประมวลผลระหว่างขั้นอย่างไร เมื่อเกิดคอขวดและคอขวดย้ายที่ไปมา**

ตัวควบคุมที่ทดสอบต้องตัดสินใจจากข้อมูลที่มาช้าและมีสัญญาณรบกวน และทรัพยากรที่ย้ายต้องใช้เวลากว่าจะเริ่มทำงาน คำถามหลักของทุกรุ่นคือ ตัวควบคุมลดงานค้าง งานตกหล่น และกรณีแย่ที่สุด ได้ดีกว่าวิธีพื้นฐาน เช่น ไม่ย้ายเลย หรือย้ายตามกฎง่าย ๆ หรือไม่

- **ส่วนใหญ่เป็นการจำลอง:** รุ่น v0.1–v0.5 และ v0.6 ที่กำลังพัฒนาทดลองในแบบจำลอง
- **งานจริงหนึ่งชุด:** Mosaic Queue v1 สร้างภาพโมเสกจากภาพท้องฟ้าสาธารณะบนเครื่องเดียว
- **ไม่มีตัวเชื่อมระบบจริง:** โค้ดชุดนี้ไม่มีตัวเชื่อมที่สั่งงานระบบจริงหรือระบบ production

## ชื่อนี้หมายถึงอะไร

"ระบบประสาท" (nervous system) ในชื่อเป็นคำเปรียบเปรย ไม่ใช่แบบจำลองระบบประสาททางชีววิทยา [README ของ v0.1](simulation/README_v010.md) เขียนไว้ตั้งแต่แรกว่าไม่ใช่ biological neural model

สิ่งที่ศึกษาคือวงจรที่คล้ายการตอบสนองต่อสิ่งเร้า:
1. รับรู้สถานะคิวที่มาช้า
2. ตัดสินใจ
3. สั่งย้ายทรัพยากร
4. รับรู้ผลแล้วปรับอีกครั้ง

ส่วน Neuron Flow ในโค้ดคือ runtime สำหรับรันงานแบบ workflow ที่รุ่นแรก ๆ ใช้ประกอบการทดลอง

## เริ่มอ่านที่นี่

ถ้าอ่านได้ไฟล์เดียว ให้อ่าน [ผลของรุ่นปัจจุบัน v0.5](simulation/V5_VALIDATION_TH.md) แล้วกลับมาดูหัวข้อ "อ่านผลอย่างไร" ด้านล่าง

| ต้องการ | เปิด |
|---|---|
| รู้สถานะล่าสุด | หัวข้อ "สถานะตอนนี้" ด้านล่าง |
| ดูผลการทดสอบกับงานจริง | [Mosaic Queue v1](benchmarks/mosaic_v1/RESULTS_TH.md) |
| รู้ว่ารุ่นเก่าแต่ละรุ่นได้ผลอย่างไร | หัวข้อ "ประวัติรุ่น" ด้านล่าง |
| พัฒนารอบถัดไป | [CONTRIBUTING.md](CONTRIBUTING.md) และ [AGENTS.md](AGENTS.md) |
| รันการทดลองรุ่นเดิมซ้ำ หรือรู้ว่าสำเนานี้ตัดอะไรออกจากชุดเต็ม | [simulation/GITHUB_START.md](simulation/GITHUB_START.md) ZIP จากปุ่ม Download ของ GitHub ใช้แทน ZIP ต้นฉบับไม่ได้ |

เอกสารของรุ่นก่อน v0.5 เป็นประวัติ เปิดเมื่อต้องการรายละเอียดของรุ่นนั้นเท่านั้น ข้อสรุปที่ใช้ได้ตอนนี้อยู่ในเอกสารของรุ่นปัจจุบัน

## สถานะตอนนี้ (28 พ.ย. 2026)

- **รุ่นปัจจุบันใน `main`: v0.5 (V5 Full)** ทดสอบกับข้อมูลที่กันไว้ไม่ให้เห็นระหว่างพัฒนาแล้ว ผลคือ **ไม่ผ่าน (FAIL)** หลักฐานครบ · [อ่านผล](simulation/V5_VALIDATION_TH.md)
  - สิ่งที่ผ่าน: เมื่อข้อมูลล่าช้าและมีสัญญาณรบกวน V5 ลดงานตกหล่นจาก v0.4 ได้ 10.2%
  - สิ่งที่ไม่ผ่าน: เมื่อบัฟเฟอร์ไม่จำกัด V5 เหลืองานค้างปลายช่วงมากกว่าวิธี Capacity Balance และในสถานการณ์นอกแบบที่ไม่เคยเห็น (OOD) ความเสี่ยงสูงกว่า v0.4
- **กำลังพัฒนา: v0.6** ใน [PR #16](https://github.com/narongdetpyou-ux/adaptive-nervous-system/pull/16) ยังไม่ได้รวมเข้า `main` รุ่นนี้แก้สองจุดที่ทำให้ v0.5 ไม่ผ่าน และกำลังรันชุดยืนยัน
- **งานจริง Mosaic Queue v1:** V5 รุ่นช่วงพัฒนา (commit `22676ec`) ไม่ผ่าน แต่เมื่อเพิ่มตัวจัดลำดับคิวแล้วผ่าน · [อ่านผล](benchmarks/mosaic_v1/RESULTS_TH.md)

## อ่านผลอย่างไร

**เกณฑ์ประกาศไว้ก่อน** ก่อนสร้างข้อมูลทดสอบ ทุกรุ่นประกาศเกณฑ์ seed และวิธีคำนวณไว้ใน commit แรกของรอบ จากนั้นทดสอบกับข้อมูลที่กันไว้ไม่ให้เห็นระหว่างพัฒนา

| ผล | ความหมาย |
|---|---|
| **PASS** | ผ่านทุกเกณฑ์พร้อมกัน |
| **FAIL** | หลักฐานครบ แต่ไม่ผ่านอย่างน้อยหนึ่งเกณฑ์ |
| **NOT_EVALUATED** | หลักฐานยังไม่ครบ จึงยังตัดสินไม่ได้ |

**FAIL ไม่ได้แปลว่าไม่ดีขึ้นเลย** แต่แปลว่ายังไม่ดีพอจะเลื่อนรุ่นตามเกณฑ์ที่ตั้งไว้ เช่น v0.5 ลดงานตกหล่นจาก v0.4 ได้จริง แต่แพ้ในเกณฑ์อื่น

**รุ่นที่ไม่ผ่านจะคงสถานะไม่ผ่าน** ห้ามปรับเกณฑ์หลังเห็นผล การแก้ต้องทำเป็นรุ่นใหม่ ประกาศเกณฑ์ใหม่ และใช้ข้อมูลทดสอบชุดใหม่

## ผลในแบบจำลองกับผลในโลกจริง

- **แบบจำลอง:** ตัวเลขของ v0.1–v0.6 มาจากแบบจำลองที่สร้างงานเข้า ความเร็วของแต่ละขั้น และความผิดปกติขึ้นเอง ผลจึงบอกได้ว่าตัวควบคุมทำงานอย่างไรภายใต้สมมติฐานของแบบจำลองนั้น แต่ยังไม่ได้บอกว่าจะใช้ได้กับระบบจริง
- **งานจริงบนเครื่องเดียว:** Mosaic Queue v1 รันงานจริงบนเครื่องเดียว แต่ยังไม่ใช่ระบบ production
- **สิ่งที่ยังขาด:** ก่อนจะอ้างว่าแนวทางนี้ใช้กับระบบจริงได้ ต้องมีสามอย่าง:
  - ข้อมูลจากระบบจริง
  - การปรับเทียบสมมติฐานของแบบจำลอง เช่น รูปแบบงานเข้า เวลาย้ายทรัพยากร และความผิดพลาดของการวัด
  - การตรวจสอบจากภายนอก

## ประวัติรุ่น

ทุกรุ่นทดลองในแบบจำลอง เปิดเอกสารของรุ่นเก่าเมื่อต้องการรายละเอียดเท่านั้น

| รุ่น | สิ่งที่ลอง | ผล | เอกสาร |
|---|---|---|---|
| v0.1 | ตัวตัดสินใจ ABC Causal Service บน Neuron Flow | มีงานค้างมากกว่าการไม่ย้าย (Static) 45.69% ตัวกรองที่เพิ่มภายหลังยังแย่กว่า Static 3.56% | [README_v010.md](simulation/README_v010.md) |
| v0.2 | ประเมินกำลังประมวลผลก่อนอนุญาตให้ย้าย | ลดงานค้างจาก Static ได้ 23.39% แต่ยังแพ้ Static ใน 20 จาก 176 สถานการณ์ | [README_v020.md](simulation/README_v020.md) |
| v0.3 | จับการย้ายผิดจังหวะ จำลองเวลาขนย้าย และวัดงานค้างช่วงท้าย | ลดจาก Static ได้ 18.83% แต่มีงานค้างมากกว่า v0.2 จึงไม่ผ่าน | [README_v030.md](simulation/README_v030.md) |
| v0.4 | ตัวควบคุมแบบผสมที่ระวังกรณีแย่ที่สุด | ลดความเสี่ยงกรณีแย่ที่สุดจาก v0.3 ได้ 36.84% แต่แพ้ Capacity Balance ด้านงานค้างปลายช่วง จึงไม่ผ่านทุกเกณฑ์ | [README.md](simulation/README.md) |
| **v0.5** | ระวังงานตกหล่น ประมาณคิวที่มองไม่เห็น และตรวจคอขวดที่ย้าย | ไม่ผ่าน (FAIL) แม้ลดงานตกหล่นจาก v0.4 ได้ 10.2% | [V5_VALIDATION_TH.md](simulation/V5_VALIDATION_TH.md) |
| v0.6 | ระบายงานค้างตอนท้าย และหยุดย้ายเมื่อการย้ายไม่ทันการเปลี่ยนของคอขวด | กำลังรันชุดยืนยัน | [PR #16](https://github.com/narongdetpyou-ux/adaptive-nervous-system/pull/16) |

## License

โค้ดและเอกสารใน repository นี้ใช้ [MIT License](LICENSE):

- **ใช้ได้เต็มที่:** นำไปใช้ แก้ไข และเผยแพร่ต่อได้ รวมถึงใช้เชิงพาณิชย์
- **เงื่อนไข:** ต้องคงประกาศลิขสิทธิ์และข้อความ license ไว้
- **ไม่มีการรับประกัน:** ซอฟต์แวร์ให้มาตามสภาพ

ข้อมูลภาพท้องฟ้า 2MASS ที่ benchmark ดาวน์โหลดตอนรันไม่ได้อยู่ใน repository ข้อมูลนั้นใช้เงื่อนไขและคำขอบคุณของผู้เผยแพร่ (NASA/IPAC IRSA) ตามที่ระบุใน [benchmarks/mosaic_v1/README.md](benchmarks/mosaic_v1/README.md)

## โฟลเดอร์หลัก

| โฟลเดอร์ | มีอะไร |
|---|---|
| [`simulation/`](simulation) | ซอร์ส tests protocol และหลักฐานของรุ่น v0.1–v0.5 ที่ทดลองในแบบจำลอง ไฟล์ v0.1–v0.4 มีไบต์เดิมตั้งแต่ตอนนำเข้า ส่วนไฟล์ v0.5 ตรึงไว้ก่อนเปิดชุดยืนยัน |
| [`benchmarks/`](benchmarks/mosaic_v1/README.md) | ชุดทดสอบ Mosaic Queue v1 ที่รันจริง รวม workload ที่ตรึงไว้ ผลใน `results/` ผลรันซ้ำใน `reproductions/` และการศึกษาต่อยอด `tail_study/` กับ `first_start/` |
| [`pilots/`](pilots/m101/README.md) | pilot ภาพ M101 ที่ใช้กำหนดชุดทดสอบ |
| `.github/` | CI, แม่แบบ PR และ Dependabot |

ที่รากมีเพียงโฟลเดอร์ข้างบน, หน้านี้ (`README.md`), `LICENSE`, `AGENTS.md`, `CONTRIBUTING.md`, `.gitattributes` และ `.gitignore`

## แผนที่ไฟล์ใน `simulation/`

ไฟล์ทุกรุ่นอยู่ระดับเดียวกันภายใน `simulation/` เพราะโค้ด import กันและอ่านไฟล์ตามตำแหน่งนี้ จัดกลุ่มตามรุ่นได้ดังนี้

### v0.5 · [V5_DESIGN.md](simulation/V5_DESIGN.md)

- ตัวควบคุม: `v5_policy.py`, `v5_observer.py`, `v5_regime.py`, `v5_forecast.py` และตัวรันแบบจำลอง `v5_simulation.py`
- สถานการณ์ OOD หกครอบครัว: `v5_ood.py`
- ขั้นพัฒนาและคัดเลือก: `v5_smoke.py`, `v5_known_study.py`, `v5_development.py`, `v5_development_cohort.py`, `v5_switch_study.py`, `v5_intervention.py`, `v5_grid.py`, `diagnose_v4_for_v5.py`
- ชุดยืนยัน การประเมิน ผู้ตรวจอิสระ และการตรึง: `v5_validation.py`, `v5_evaluate.py`, `v5_review.py`, `v5_decide.py`, `v5_freeze.py`
- เกณฑ์และแผนที่ประกาศไว้ก่อนสร้างข้อมูล: `v5_acceptance_predeclared.json`, `v5_switch_study_plan.json`, `v5_selection_plan.json`, `v5_ood_plan.json`
- บันทึกการตรึง: `v5_freeze_record.json` (SHA-256 ของ 38 ไฟล์และ config ของ V5 Full)
- tests: `test_v5*.py`
- หลักฐาน: [`v5_results/`](simulation/v5_results) แบ่งเป็น `development_grid/`, `selection/` และ `validation/`
- รายงานภาษาไทย: `V5_DEVELOPMENT_RESULTS_TH.md`, `V5_SWITCH_STUDY_TH.md`, `V5_SELECTION_TH.md`, `V5_VALIDATION_TH.md`

### v0.4 · [README.md](simulation/README.md)

- โมดูลหลัก (ตัวควบคุม แบบจำลอง และการวิเคราะห์): `v4_*.py`
- ขั้นตอนรัน ตรึง ตรวจ และทำรายงาน: ไฟล์ `.py` อื่นที่มี `_v4` ในชื่อ เช่น `run_v4.py`, `freeze_v4.py`, `evaluate_v4.py`
- protocol เกณฑ์ และ config: `v4_*.json`, `v4_*.sha256`
- หลักฐาน: [`v4_results/`](simulation/v4_results)
- บันทึก: `V4_*_NOTES.md`, `v040_provenance.json`, `START_COLAB_v040.ipynb`

### v0.3 · [README_v030.md](simulation/README_v030.md)

- โมดูลหลัก: `v3_*.py`
- ขั้นตอนรันและตรวจ: ไฟล์ `.py` อื่นที่มี `_v3` ในชื่อ และ `diagnose_v2.py`
- protocol: `v3_*.json`, `v3_*.sha256`
- shadow replay: `shadow_replay.py`, `synthetic_example.json`, `telemetry_schema.json`, [`telemetry/`](simulation/telemetry/README_TH.md)
- หลักฐาน: [`v3_results/`](simulation/v3_results)
- บันทึก: `V3_DEVELOPMENT_NOTES.md`, `v030_provenance.json`, `START_COLAB_v030.ipynb`

### v0.2 · [README_v020.md](simulation/README_v020.md)

- โมดูลหลัก: `capacity_*.py`
- ขั้นตอนรันและตรวจ: ไฟล์ `.py` อื่นที่มี `_capacity` ในชื่อ
- protocol: `capacity_*.json`, `capacity_*.sha256`
- หลักฐาน: [`capacity_results/`](simulation/capacity_results)
- บันทึก: `DEVELOPMENT_NOTES.md`, `v020_provenance.json`, `START_COLAB_v020.ipynb`

### v0.1 · [README_v010.md](simulation/README_v010.md)

- แบบจำลองและการรัน: `simulation.py`, `lab_agents.py`, `run_lab.py`, `test_lab.py`
- exploratory guard: `followup.py`, `followup_protocol.*`
- protocol และที่มา: `protocol.*`, `provenance.json`
- ข้อมูลและหลักฐาน: [`data/`](simulation/data), [`results/`](simulation/results)
- สมุดงาน: `START_COLAB_v010.ipynb`

### ใช้ร่วมกันทุกรุ่น

- [`neuron_flow/`](simulation/neuron_flow): runtime ของ Neuron Flow
- [`abc_service/`](simulation/abc_service): ABC Causal Service ที่ตรึงไว้
- `requirements.txt`: dependency ของ baseline (ตรึงไว้) และ `report_requirements.txt` บันทึก environment ที่ใช้สร้างรายงาน PDF ต้นฉบับ (ตรึงไว้)
- [`report_env/`](simulation/report_env/requirements.txt): environment ปัจจุบันสำหรับสร้างรายงาน PDF ใหม่ (PyMuPDF 1.26.7)

### การนำเข้าและการตรวจ hash

- `source_checksums.sha256`, `SOURCE_IMPORT_*` และ [GITHUB_START.md](simulation/GITHUB_START.md): hash ของไฟล์ต้นฉบับ บันทึกการตรวจ และคู่มือตอนนำเข้า
- `test_source_layout.py`: ตรวจว่าไฟล์ทั้ง 199 ไฟล์ที่บันทึกไว้ตอนนำเข้ายังมี SHA-256 เดิมหลังย้ายเข้าโฟลเดอร์นี้

## ก่อนแก้หรือย้ายไฟล์

- ไฟล์ใน `simulation/` ถูกบันทึกชื่อหรือ hash ไว้ใน `freeze_*.py`, `source_checksums.sha256`, `SOURCE_IMPORT_MANIFEST.json` หรือ `v5_freeze_record.json` คงชื่อ ตำแหน่งภายในโฟลเดอร์ และไบต์เดิมไว้ ดูขอบเขตใน [CONTRIBUTING.md](CONTRIBUTING.md)
- งานรุ่นใหม่ของแบบจำลองให้เพิ่มเป็นไฟล์ใหม่ใน `simulation/` เช่น `v6_*.py` กับ `test_v6_*.py` และอย่าแก้ไฟล์ v0.5 ที่ตรึงไว้ งานรันจริงใหม่ให้เป็นโฟลเดอร์ใหม่ใน `benchmarks/` หรือ `pilots/`
- เก็บผลที่รันในเครื่องไว้ใน `local_runs/`, `local_reports/`, `local_packages/` หรือ `local_qa/` ทั้งที่รากและใน `simulation/` ซึ่ง `.gitignore` กันไว้ไม่ให้ถูก commit
- Mosaic Queue v1 ใช้ซอร์ส V5 จาก commit `22676ec` ซึ่งอยู่ในประวัติของ `main` แล้วตั้งแต่รวม PR #2 จึงลบ branch `research/v5-loss-aware` ได้โดยไม่เสีย commit นั้น ที่ commit นั้นไฟล์ยังอยู่ที่รากตามโครงสร้างเดิม ถ้าใช้ซอร์สหลังการย้าย ให้ตั้ง `ANS_SOURCE` ไปที่โฟลเดอร์ `simulation/` ของ checkout นั้น
