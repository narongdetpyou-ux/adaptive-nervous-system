# Adaptive Nervous System (ANS)

ANS คืองานวิจัยตัวควบคุมที่ตัดสินใจย้ายกำลังประมวลผลระหว่างขั้นตอนของคิวงานเมื่อเกิดคอขวด รุ่น v0.1–v0.4 ทดลองในแบบจำลอง ส่วน Mosaic Queue v1 ทดลองกับงานสร้างภาพโมเสกจากภาพท้องฟ้าจริง โค้ดชุดนี้ไม่มีตัวเชื่อมที่สั่งงานระบบจริง

> หน้านี้คือ `.github/README.md` ที่ GitHub แสดงเป็นหน้าแรก รายงาน v0.4.0 เดิมอยู่ที่ [`simulation/README.md`](../simulation/README.md)

## ผลล่าสุด

- **Mosaic Queue v1 (รันจริง):** V5 เดิม FAIL ส่วน V5 พร้อมตัวจัดลำดับคิว PASS · [อ่านผล](../benchmarks/mosaic_v1/RESULTS_TH.md)
  - [รันซ้ำบนเครื่องอื่น 25 พ.ย. 2026](../benchmarks/mosaic_v1/reproductions/20261125/README_TH.md): ได้คำตัดสินเดิมทุกข้อ
  - [ลดเวลารอของคำขอที่ช้าที่สุด](../benchmarks/mosaic_v1/tail_study/RESULTS_TH.md): สรุปให้คงตัวจัดคิวเดิม
  - [ให้สิทธิ์เริ่มงานหนึ่งครั้ง](../benchmarks/mosaic_v1/first_start/RESULTS_TH.md): งานใหญ่เริ่มเร็วขึ้นแต่ได้ภาพช้าเท่าเดิม สรุปให้คงตัวจัดคิวเดิม
- **M101 pilot (รันจริง):** สร้างโมเสก M101 จากภาพ 2MASS 10 ภาพ และวัดเวลาแต่ละขั้นตอนก่อนสร้าง benchmark · [อ่านผล](../pilots/m101/RESULTS_TH.md)
- **v0.4.0 (แบบจำลอง):** ลด CVaR90 ได้ชัดเจน แต่ยังไม่ผ่านเกณฑ์ยกระดับทั้งหมด · [อ่านผล](../simulation/README.md)
- **v0.5 (กำลังพัฒนา):** [narongdetpyou-ux/adaptive-nervous-system#2](https://github.com/narongdetpyou-ux/adaptive-nervous-system/pull/2) ยังเป็นร่าง

## เริ่มจากตรงไหน

| ต้องการ | เปิด |
|---|---|
| ดูผลล่าสุด | [Mosaic Queue v1](../benchmarks/mosaic_v1/RESULTS_TH.md) |
| พัฒนารอบถัดไป | [CONTRIBUTING.md](../CONTRIBUTING.md) และ [AGENTS.md](../AGENTS.md) |
| รู้ว่าสำเนานี้ตัดอะไรออกจากชุดเต็ม | [simulation/GITHUB_START.md](../simulation/GITHUB_START.md) |
| รันสมุดงาน Colab รุ่นเดิม | ใช้ ZIP ต้นฉบับตาม [simulation/GITHUB_START.md](../simulation/GITHUB_START.md) เพราะ ZIP จากปุ่ม Download ของ GitHub ใช้แทนไม่ได้ |

## โฟลเดอร์หลัก

| โฟลเดอร์ | มีอะไร |
|---|---|
| [`simulation/`](../simulation) | ซอร์ส tests protocol และหลักฐานของรุ่น v0.1–v0.4 ที่ทดลองในแบบจำลอง ทุกไฟล์มีไบต์เดิมตั้งแต่ตอนนำเข้า |
| [`benchmarks/`](../benchmarks/mosaic_v1/README.md) | ชุดทดสอบ Mosaic Queue v1 ที่รันจริง รวม workload ที่ตรึงไว้ ผลใน `results/` ผลรันซ้ำใน `reproductions/` และการศึกษาต่อยอด `tail_study/` กับ `first_start/` |
| [`pilots/`](../pilots/m101/README.md) | pilot ภาพ M101 ที่ใช้กำหนดชุดทดสอบ |
| `.github/` | CI, แม่แบบ PR, Dependabot และหน้านี้ |

ที่รากมีเพียงโฟลเดอร์ข้างบน `AGENTS.md`, `CONTRIBUTING.md`, `.gitattributes` และ `.gitignore`

## แผนที่ไฟล์ใน `simulation/`

ไฟล์ทุกรุ่นอยู่ระดับเดียวกันภายใน `simulation/` เพราะโค้ด import กันและอ่านไฟล์ตามตำแหน่งนี้ จัดกลุ่มตามรุ่นได้ดังนี้

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
- `requirements.txt`: dependency ของ baseline (ตรึงไว้) และ `report_requirements.txt` สำหรับสร้างรายงาน PDF

### การนำเข้าและการตรวจ hash

- `source_checksums.sha256`, `SOURCE_IMPORT_*` และ [GITHUB_START.md](../simulation/GITHUB_START.md): hash ของไฟล์ต้นฉบับ บันทึกการตรวจ และคู่มือตอนนำเข้า
- `test_source_layout.py`: ตรวจว่าไฟล์ทั้ง 199 ไฟล์ที่บันทึกไว้ตอนนำเข้ายังมี SHA-256 เดิมหลังย้ายเข้าโฟลเดอร์นี้

## ก่อนแก้หรือย้ายไฟล์

- ไฟล์ใน `simulation/` ถูกบันทึกชื่อหรือ hash ไว้ใน `freeze_*.py`, `source_checksums.sha256` หรือ `SOURCE_IMPORT_MANIFEST.json` คงชื่อ ตำแหน่งภายในโฟลเดอร์ และไบต์เดิมไว้ ดูขอบเขตใน [CONTRIBUTING.md](../CONTRIBUTING.md)
- งานรุ่นใหม่ของแบบจำลองให้เพิ่มเป็นไฟล์ใหม่ใน `simulation/` เช่น `v5_*.py` กับ `test_v5_*.py` งานรันจริงใหม่ให้เป็นโฟลเดอร์ใหม่ใน `benchmarks/` หรือ `pilots/`
- เก็บผลที่รันในเครื่องไว้ใน `local_runs/`, `local_reports/`, `local_packages/` หรือ `local_qa/` ทั้งที่รากและใน `simulation/` ซึ่ง `.gitignore` กันไว้ไม่ให้ถูก commit
- Mosaic Queue v1 ใช้ซอร์ส V5 จาก commit `22676ec` บน branch `research/v5-loss-aware` ซึ่งยังมีไฟล์อยู่ที่รากตามโครงสร้างเดิม อย่าลบ branch นี้จนกว่า commit นั้นจะอยู่ใน `main` หรือมี tag เก็บไว้ ถ้าใช้ซอร์สหลังการย้าย ให้ตั้ง `ANS_SOURCE` ไปที่โฟลเดอร์ `simulation/` ของ checkout นั้น
