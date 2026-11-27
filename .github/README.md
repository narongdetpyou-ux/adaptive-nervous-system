# Adaptive Nervous System (ANS)

ANS คืองานวิจัยตัวควบคุมที่ตัดสินใจย้ายกำลังประมวลผลระหว่างขั้นตอนของคิวงานเมื่อเกิดคอขวด รุ่น v0.1–v0.5 ทดลองในแบบจำลอง ส่วน Mosaic Queue v1 ทดลองกับงานสร้างภาพโมเสกจากภาพท้องฟ้าจริง โค้ดชุดนี้ไม่มีตัวเชื่อมที่สั่งงานระบบจริง

> หน้านี้คือ `.github/README.md` ที่ GitHub แสดงเป็นหน้าแรก รายงาน v0.4.0 เดิมอยู่ที่ [`simulation/README.md`](../simulation/README.md)

## สถานะตอนนี้ (27 พ.ย. 2026)

- **v0.5 ทดสอบครบแล้ว:** รุ่นล่าสุดในแบบจำลองคือ V5 Full ทดสอบกับชุดยืนยันและชุด OOD ที่กันไว้แล้ว ผลคือ **FAIL** พร้อมหลักฐานครบ งานนี้รวมเข้า `main` แล้วผ่าน [PR #2](https://github.com/narongdetpyou-ux/adaptive-nervous-system/pull/2)
- **ยังไม่มีรุ่นใหม่ที่กำลังพัฒนา:** รอบถัดไปต้องประกาศเกณฑ์และ seed ชุดใหม่ก่อนสร้างข้อมูล ตาม [CONTRIBUTING.md](../CONTRIBUTING.md)

## ผลล่าสุด

- **v0.5 (แบบจำลอง):** V5 Full ไม่ผ่านเกณฑ์เลื่อนรุ่น · [อ่านผล](../simulation/V5_VALIDATION_TH.md)
  - สิ่งที่ผ่าน: เมื่อข้อมูลล่าช้าและมีสัญญาณรบกวน V5 Full ลดงานตกหล่นจาก V4 Hybrid ได้ 10.2%
  - สิ่งที่ไม่ผ่าน: เมื่อบัฟเฟอร์ไม่จำกัด มันเหลืองานค้างปลายช่วงมากกว่า Capacity Balance และในชุด OOD ความเสี่ยงรวมสูงกว่า V4 Hybrid 9.6%
  - [ชุดพัฒนาและชุดคัดเลือก](../simulation/V5_SELECTION_TH.md): เลือก V5 Full ก่อนเปิดชุดยืนยัน
  - [ทดลองเมื่อคอขวดย้ายไปขั้นกลาง](../simulation/V5_SWITCH_STUDY_TH.md): ไม่มีการเปลี่ยนคำสั่งแบบใดผ่านเกณฑ์ จึงคงตัวเลือกเดิม
- **Mosaic Queue v1 (รันจริง):** V5 เดิม FAIL ส่วน V5 พร้อมตัวจัดลำดับคิว PASS · [อ่านผล](../benchmarks/mosaic_v1/RESULTS_TH.md)
  - ใช้โค้ด V5 ช่วงพัฒนาจาก commit `22676ec` ซึ่งเก่ากว่ารุ่นที่ตรึงไว้ ผลนี้จึงแยกจากผล v0.5 ในแบบจำลอง
  - [รันซ้ำบนเครื่องอื่น 25 พ.ย. 2026](../benchmarks/mosaic_v1/reproductions/20261125/README_TH.md): ได้คำตัดสินเดิมทุกข้อ
  - [ลดเวลารอของคำขอที่ช้าที่สุด](../benchmarks/mosaic_v1/tail_study/RESULTS_TH.md): สรุปให้คงตัวจัดคิวเดิม
  - [ให้สิทธิ์เริ่มงานหนึ่งครั้ง](../benchmarks/mosaic_v1/first_start/RESULTS_TH.md): งานใหญ่เริ่มเร็วขึ้นแต่ได้ภาพช้าเท่าเดิม สรุปให้คงตัวจัดคิวเดิม
- **M101 pilot (รันจริง):** สร้างโมเสก M101 จากภาพ 2MASS 10 ภาพ และวัดเวลาแต่ละขั้นตอนก่อนสร้าง benchmark · [อ่านผล](../pilots/m101/RESULTS_TH.md)
- **v0.4.0 (แบบจำลอง):** ลด CVaR90 ได้ชัดเจน แต่ยังไม่ผ่านเกณฑ์ยกระดับทั้งหมด · [อ่านผล](../simulation/README.md)

## เริ่มจากตรงไหน

| ต้องการ | เปิด |
|---|---|
| ดูผลล่าสุดของแบบจำลอง (v0.5) | [V5_VALIDATION_TH.md](../simulation/V5_VALIDATION_TH.md) |
| ดูผลรันจริงล่าสุด | [Mosaic Queue v1](../benchmarks/mosaic_v1/RESULTS_TH.md) |
| ดูแผนและลำดับงานของ v0.5 | [V5_DESIGN.md](../simulation/V5_DESIGN.md) |
| พัฒนารอบถัดไป | [CONTRIBUTING.md](../CONTRIBUTING.md) และ [AGENTS.md](../AGENTS.md) |
| รู้ว่าสำเนานี้ตัดอะไรออกจากชุดเต็ม | [simulation/GITHUB_START.md](../simulation/GITHUB_START.md) |
| รันสมุดงาน Colab รุ่นเดิม | ใช้ ZIP ต้นฉบับตาม [simulation/GITHUB_START.md](../simulation/GITHUB_START.md) เพราะ ZIP จากปุ่ม Download ของ GitHub ใช้แทนไม่ได้ |

## โฟลเดอร์หลัก

| โฟลเดอร์ | มีอะไร |
|---|---|
| [`simulation/`](../simulation) | ซอร์ส tests protocol และหลักฐานของรุ่น v0.1–v0.5 ที่ทดลองในแบบจำลอง ไฟล์ v0.1–v0.4 มีไบต์เดิมตั้งแต่ตอนนำเข้า ส่วนไฟล์ v0.5 ตรึงไว้ก่อนเปิดชุดยืนยัน |
| [`benchmarks/`](../benchmarks/mosaic_v1/README.md) | ชุดทดสอบ Mosaic Queue v1 ที่รันจริง รวม workload ที่ตรึงไว้ ผลใน `results/` ผลรันซ้ำใน `reproductions/` และการศึกษาต่อยอด `tail_study/` กับ `first_start/` |
| [`pilots/`](../pilots/m101/README.md) | pilot ภาพ M101 ที่ใช้กำหนดชุดทดสอบ |
| `.github/` | CI, แม่แบบ PR, Dependabot และหน้านี้ |

ที่รากมีเพียงโฟลเดอร์ข้างบน `AGENTS.md`, `CONTRIBUTING.md`, `.gitattributes` และ `.gitignore`

## แผนที่ไฟล์ใน `simulation/`

ไฟล์ทุกรุ่นอยู่ระดับเดียวกันภายใน `simulation/` เพราะโค้ด import กันและอ่านไฟล์ตามตำแหน่งนี้ จัดกลุ่มตามรุ่นได้ดังนี้

### v0.5 · [V5_DESIGN.md](../simulation/V5_DESIGN.md)

- ตัวควบคุม: `v5_policy.py`, `v5_observer.py`, `v5_regime.py`, `v5_forecast.py` และตัวรันแบบจำลอง `v5_simulation.py`
- สถานการณ์ OOD หกครอบครัว: `v5_ood.py`
- ขั้นพัฒนาและคัดเลือก: `v5_smoke.py`, `v5_known_study.py`, `v5_development.py`, `v5_development_cohort.py`, `v5_switch_study.py`, `v5_intervention.py`, `v5_grid.py`, `diagnose_v4_for_v5.py`
- ชุดยืนยัน การประเมิน ผู้ตรวจอิสระ และการตรึง: `v5_validation.py`, `v5_evaluate.py`, `v5_review.py`, `v5_decide.py`, `v5_freeze.py`
- เกณฑ์และแผนที่ประกาศไว้ก่อนสร้างข้อมูล: `v5_acceptance_predeclared.json`, `v5_switch_study_plan.json`, `v5_selection_plan.json`, `v5_ood_plan.json`
- บันทึกการตรึง: `v5_freeze_record.json` (SHA-256 ของ 38 ไฟล์และ config ของ V5 Full)
- tests: `test_v5*.py`
- หลักฐาน: [`v5_results/`](../simulation/v5_results) แบ่งเป็น `development_grid/`, `selection/` และ `validation/`
- รายงานภาษาไทย: `V5_DEVELOPMENT_RESULTS_TH.md`, `V5_SWITCH_STUDY_TH.md`, `V5_SELECTION_TH.md`, `V5_VALIDATION_TH.md`

### v0.4 · [README.md](../simulation/README.md)

- โมดูลหลัก (ตัวควบคุม แบบจำลอง และการวิเคราะห์): `v4_*.py`
- ขั้นตอนรัน ตรึง ตรวจ และทำรายงาน: ไฟล์ `.py` อื่นที่มี `_v4` ในชื่อ เช่น `run_v4.py`, `freeze_v4.py`, `evaluate_v4.py`
- protocol เกณฑ์ และ config: `v4_*.json`, `v4_*.sha256`
- หลักฐาน: [`v4_results/`](../simulation/v4_results)
- บันทึก: `V4_*_NOTES.md`, `v040_provenance.json`, `START_COLAB_v040.ipynb`

### v0.3 · [README_v030.md](../simulation/README_v030.md)

- โมดูลหลัก: `v3_*.py`
- ขั้นตอนรันและตรวจ: ไฟล์ `.py` อื่นที่มี `_v3` ในชื่อ และ `diagnose_v2.py`
- protocol: `v3_*.json`, `v3_*.sha256`
- shadow replay: `shadow_replay.py`, `synthetic_example.json`, `telemetry_schema.json`, [`telemetry/`](../simulation/telemetry/README_TH.md)
- หลักฐาน: [`v3_results/`](../simulation/v3_results)
- บันทึก: `V3_DEVELOPMENT_NOTES.md`, `v030_provenance.json`, `START_COLAB_v030.ipynb`

### v0.2 · [README_v020.md](../simulation/README_v020.md)

- โมดูลหลัก: `capacity_*.py`
- ขั้นตอนรันและตรวจ: ไฟล์ `.py` อื่นที่มี `_capacity` ในชื่อ
- protocol: `capacity_*.json`, `capacity_*.sha256`
- หลักฐาน: [`capacity_results/`](../simulation/capacity_results)
- บันทึก: `DEVELOPMENT_NOTES.md`, `v020_provenance.json`, `START_COLAB_v020.ipynb`

### v0.1 · [README_v010.md](../simulation/README_v010.md)

- แบบจำลองและการรัน: `simulation.py`, `lab_agents.py`, `run_lab.py`, `test_lab.py`
- exploratory guard: `followup.py`, `followup_protocol.*`
- protocol และที่มา: `protocol.*`, `provenance.json`
- ข้อมูลและหลักฐาน: [`data/`](../simulation/data), [`results/`](../simulation/results)
- สมุดงาน: `START_COLAB_v010.ipynb`

### ใช้ร่วมกันทุกรุ่น

- [`neuron_flow/`](../simulation/neuron_flow): runtime ของ Neuron Flow
- [`abc_service/`](../simulation/abc_service): ABC Causal Service ที่ตรึงไว้
- `requirements.txt`: dependency ของ baseline (ตรึงไว้) และ `report_requirements.txt` บันทึก environment ที่ใช้สร้างรายงาน PDF ต้นฉบับ (ตรึงไว้)
- [`report_env/`](../simulation/report_env/requirements.txt): environment ปัจจุบันสำหรับสร้างรายงาน PDF ใหม่ (PyMuPDF 1.26.7)

### การนำเข้าและการตรวจ hash

- `source_checksums.sha256`, `SOURCE_IMPORT_*` และ [GITHUB_START.md](../simulation/GITHUB_START.md): hash ของไฟล์ต้นฉบับ บันทึกการตรวจ และคู่มือตอนนำเข้า
- `test_source_layout.py`: ตรวจว่าไฟล์ทั้ง 199 ไฟล์ที่บันทึกไว้ตอนนำเข้ายังมี SHA-256 เดิมหลังย้ายเข้าโฟลเดอร์นี้

## ก่อนแก้หรือย้ายไฟล์

- ไฟล์ใน `simulation/` ถูกบันทึกชื่อหรือ hash ไว้ใน `freeze_*.py`, `source_checksums.sha256`, `SOURCE_IMPORT_MANIFEST.json` หรือ `v5_freeze_record.json` คงชื่อ ตำแหน่งภายในโฟลเดอร์ และไบต์เดิมไว้ ดูขอบเขตใน [CONTRIBUTING.md](../CONTRIBUTING.md)
- งานรุ่นใหม่ของแบบจำลองให้เพิ่มเป็นไฟล์ใหม่ใน `simulation/` เช่น `v6_*.py` กับ `test_v6_*.py` และอย่าแก้ไฟล์ v0.5 ที่ตรึงไว้ งานรันจริงใหม่ให้เป็นโฟลเดอร์ใหม่ใน `benchmarks/` หรือ `pilots/`
- เก็บผลที่รันในเครื่องไว้ใน `local_runs/`, `local_reports/`, `local_packages/` หรือ `local_qa/` ทั้งที่รากและใน `simulation/` ซึ่ง `.gitignore` กันไว้ไม่ให้ถูก commit
- Mosaic Queue v1 ใช้ซอร์ส V5 จาก commit `22676ec` ซึ่งอยู่ในประวัติของ `main` แล้วตั้งแต่รวม PR #2 จึงลบ branch `research/v5-loss-aware` ได้โดยไม่เสีย commit นั้น ที่ commit นั้นไฟล์ยังอยู่ที่รากตามโครงสร้างเดิม ถ้าใช้ซอร์สหลังการย้าย ให้ตั้ง `ANS_SOURCE` ไปที่โฟลเดอร์ `simulation/` ของ checkout นั้น
