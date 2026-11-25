"""Build the Thai six-page report from completed v0.2.0 experiment evidence.

This presentation-only builder is outside the frozen controller manifest.
Dependencies for rebuilding the report: PyMuPDF and matplotlib, plus the
Noto Sans Thai/Noto Sans fonts in report/fonts or the standard Linux font path.
It never runs a policy experiment or modifies a frozen source.
"""
from pathlib import Path
import argparse
import hashlib
import html
import json
import re
import shutil

import fitz
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parent
POLICIES = ["static", "maxweight", "abc", "abc_guard", "abc_capacity", "capacity_aware"]
NAMES = {
    "static": "Static",
    "maxweight": "Queue-weight heuristic",
    "abc": "Original ABC",
    "abc_guard": "ABC + persistent gate",
    "abc_capacity": "ABC + capacity gate",
    "capacity_aware": "Capacity-aware selection",
}
GROUP_NAMES = {
    "surge_only": "Flash: งานเข้าเพิ่ม",
    "ingress_slow": "Flash: รับเข้างานช้า",
    "processing_slow": "Flash: ประมวลผลช้า",
    "delivery_slow": "Flash: ส่งออกช้า",
    "shifting_bottleneck": "Flash: คอขวดเปลี่ยนจุด",
    "generated_underload": "สร้างใหม่: ภาระต่ำ",
    "generated_sustained_processing_fault": "สร้างใหม่: ช้าต่อเนื่อง",
    "generated_switching_fault": "สร้างใหม่: คอขวดสลับ",
    "generated_pulsed_short_fault": "สร้างใหม่: พุ่ง/ขัดข้องสั้น",
    "generated_asymmetric_recovery": "สร้างใหม่: ฟื้นตัวไม่สมมาตร",
    "generated_sustained_overload": "สร้างใหม่: งานเกินกำลัง",
}
GROUP_EN = {
    "surge_only": "Flash: surge only",
    "ingress_slow": "Flash: ingress slowdown",
    "processing_slow": "Flash: processing slowdown",
    "delivery_slow": "Flash: delivery slowdown",
    "shifting_bottleneck": "Flash: shifting bottleneck",
    "generated_underload": "New: underload",
    "generated_sustained_processing_fault": "New: sustained fault",
    "generated_switching_fault": "New: switching fault",
    "generated_pulsed_short_fault": "New: brief pulses / faults",
    "generated_asymmetric_recovery": "New: asymmetric recovery",
    "generated_sustained_overload": "New: sustained overload",
}
TEAL = "#147D72"
BLUE = "#4A79AA"
INK = "#17324B"
RED = "#B7473A"
GRAY = "#8595A6"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def num(value, digits=2):
    return f"{value:,.{digits}f}"


def pct(value):
    return "ไม่มีค่า" if value is None else f"{value:.2f}%"


def interval(values, digits=2):
    return "ไม่มีค่า" if values is None else f"{values[0]:,.{digits}f} ถึง {values[1]:,.{digits}f}"


def caption(text):
    return f'<p class="caption">{text}</p>'


def data_path(name):
    return f'<span class="file">capacity_results/validation/{html.escape(name)}</span>'


def table(headers, rows, classes=None):
    head = "".join(f"<th>{x}</th>" for x in headers)
    body = []
    for i, row in enumerate(rows):
        cls = "" if classes is None else f' class="{classes[i]}"'
        cells = "".join(f'<td class="{"left" if j == 0 else "num"}">{x}</td>' for j, x in enumerate(row))
        body.append(f"<tr{cls}>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def prepare_fonts(out):
    font_dir = out / "fonts"
    font_dir.mkdir(exist_ok=True)
    for name in ("NotoSansThai-Regular.ttf", "NotoSansThai-Bold.ttf",
                 "NotoSans-Regular.ttf", "NotoSans-Bold.ttf"):
        target = font_dir / name
        if not target.exists():
            source = Path("/usr/share/fonts/truetype/noto") / name
            if not source.exists():
                raise FileNotFoundError(f"Install Noto fonts or place the font at {target}")
            shutil.copyfile(source, target)


def make_figures(summary, out):
    figure_dir = out / "figures"
    figure_dir.mkdir(exist_ok=True)
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "axes.labelcolor": INK,
        "xtick.color": INK, "ytick.color": INK,
        "text.color": INK, "axes.edgecolor": "#DCE4EA",
        "savefig.facecolor": "white",
    })
    values = [summary["overall"][p]["mean_backlog"] for p in POLICIES]
    fig, ax = plt.subplots(figsize=(9.5, 3.65))
    bars = ax.barh(range(6), values, height=.58,
                   color=[GRAY, "#A9B5C1", "#A9B5C1", "#A9B5C1", BLUE, TEAL])
    ax.set_yticks(range(6), [NAMES[p] for p in POLICIES])
    ax.invert_yaxis()
    ax.set_xlim(0, max(values) * 1.18)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x/1000:.0f}k"))
    ax.set_xlabel("Mean end-of-minute backlog (work units; lower is better)")
    ax.grid(axis="x", color="#E5EBEF", linewidth=.7)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0, pad=9)
    for bar, value in zip(bars, values):
        ax.text(value + max(values) * .014, bar.get_y() + bar.get_height()/2,
                f"{value:,.0f}", va="center", fontsize=10, color=INK)
    fig.subplots_adjust(left=.28, right=.99, top=.98, bottom=.18)
    fig.savefig(figure_dir / "policy_mean_backlog.png", dpi=220)
    plt.close(fig)

    groups = list(summary["by_group"])
    reductions = [summary["by_group"][g]["comparisons"]["capacity_aware"]["static"]["reduction_pct"] for g in groups]
    fig, ax = plt.subplots(figsize=(9.5, 3.55))
    bar_values = [v if v is not None else 0.0 for v in reductions]
    ax.barh(range(len(groups)), bar_values, height=.57,
            color=[GRAY if v is None else TEAL if v >= 0 else RED for v in reductions])
    ax.set_yticks(range(len(groups)), [GROUP_EN[g] for g in groups], fontsize=9.1)
    ax.invert_yaxis()
    ax.axvline(0, color=INK, linewidth=.8)
    ax.set_xlim(-38, 94)
    ax.set_xlabel("Mean-backlog reduction vs static (%)", fontsize=10)
    ax.grid(axis="x", color="#E5EBEF", linewidth=.7)
    ax.set_axisbelow(True)
    ax.tick_params(axis="y", length=0, pad=8)
    for i, value in enumerate(reductions):
        if value is None:
            ax.text(2, i, "both zero", fontsize=8.5, va="center", color=GRAY)
        else:
            ax.text(value + (1.5 if value >= 0 else -1.5), i, f"{value:+.1f}",
                    ha="left" if value >= 0 else "right", va="center", fontsize=8.8)
    fig.subplots_adjust(left=.285, right=.995, top=.99, bottom=.17)
    fig.savefig(figure_dir / "family_improvement.png", dpi=220)
    plt.close(fig)


CSS = """
@font-face {font-family:Thai; src:url(fonts/NotoSansThai-Regular.ttf); font-weight:normal;}
@font-face {font-family:Thai; src:url(fonts/NotoSansThai-Bold.ttf); font-weight:bold;}
@font-face {font-family:Sans; src:url(fonts/NotoSans-Regular.ttf); font-weight:normal;}
@font-face {font-family:Sans; src:url(fonts/NotoSans-Bold.ttf); font-weight:bold;}
body {font-family:Thai, Sans, sans-serif; font-size:10.3pt; line-height:1.48; color:#17324B; margin:0;}
h1 {font-family:Thai, Sans; font-size:23pt; line-height:1.28; margin:0 0 10pt; color:#17324B;}
h1.cover {font-size:28pt; line-height:1.25; margin-top:10pt;}
h2 {font-size:14pt; line-height:1.3; margin:15pt 0 7pt; color:#17324B;}
h3 {font-size:11.5pt; line-height:1.35; margin:11pt 0 5pt;}
p {margin:0 0 8pt;}
strong, b {font-weight:bold;}
.eyebrow {font-family:Sans; color:#147D72; font-size:8.4pt; letter-spacing:1.3pt; margin:0 0 13pt;}
.subtitle {font-size:11pt; color:#61788B;}
.hero {border-top:2pt solid #147D72; border-bottom:1pt solid #DDE6E9; padding:10pt 0; margin:13pt 0;}
.hero td {background:#FFFFFF; border:0; padding:0 10pt 0 0; vertical-align:top;}
.hero .big {font-family:Sans; color:#147D72; font-size:31pt; line-height:1.2; font-weight:bold;}
.hero .label {font-size:10pt; color:#456174;}
.notice {padding:10pt 12pt; background-color:#EDF5F3; border-left:3pt solid #147D72; margin:9pt 0 12pt;}
.warn {padding:9pt 11pt; background-color:#FFF3EE; border-left:3pt solid #B7473A; margin:9pt 0;}
.caption {font-size:8.1pt; line-height:1.4; color:#64798B; margin:6pt 0 9pt;}
.small {font-size:9.2pt; line-height:1.45;}
.file {font-family:Sans; font-size:8.1pt; color:#426984;}
.muted {color:#64798B;}
.green {color:#147D72;}
.red {color:#B7473A;}
table {border-collapse:collapse; width:523pt; margin:8pt 0 9pt; font-size:9.1pt; line-height:1.35;}
th {background-color:#E8EFF4; color:#17324B; border-bottom:1pt solid #17324B; font-weight:bold; text-align:right; padding:6pt 5pt;}
th:first-child {text-align:left;}
td {padding:5.1pt 5pt; border-bottom:.6pt solid #DEE6EB; vertical-align:top;}
td.num {text-align:right; white-space:nowrap; font-family:Thai, Sans;}
td.left {text-align:left;}
tr.highlight td {background-color:#EAF4F1;}
tr.warning td {background-color:#FFF2EC;}
.compact td {padding:4.0pt 5pt; font-size:8.8pt;}
.compact th {padding:5pt;}
img {max-width:100%;}
pre {font-family:Sans; font-size:8.6pt; line-height:1.7; background:#EFF3F6; padding:11pt 12pt; margin:9pt 0;}
ol,ul {padding-left:17pt; margin:5pt 0 10pt;}
li {margin-bottom:6pt;}
a {color:#2E6E95; text-decoration:none;}
.step {padding:8pt 0; border-bottom:.6pt solid #DEE6EB;}
.step strong {color:#147D72;}
.footnote {font-size:8.2pt; line-height:1.4; color:#64798B;}
"""


def pages_for(summary, development, verification, decision, protocol, unit_tests, audit, rerun):
    current = summary["overall"]["capacity_aware"]
    static = summary["overall"]["static"]
    main = summary["capacity_aware_vs"]["static"]
    guard = summary["capacity_aware_vs"]["abc_guard"]
    abc_only = summary["abc_capacity_vs"]["static"]
    source_rows = []
    for source, label in [("legacy_flash", "Flash Sale เดิม"), ("generated_synthetic", "รูปแบบที่สร้างใหม่")]:
        item = summary["source_slices"][source]
        for base, base_name in [("static", "Static"), ("abc_guard", "Persistent gate")]:
            c = item["comparisons"]["capacity_aware"][base]
            source_rows.append([f"{label} / {base_name}", str(item["scenarios"]),
                                pct(c["reduction_pct"]), interval(c["reduction_pct_ci95"])])
    criteria_primary = "ผ่าน" if decision["primary_pass"] else "ไม่ผ่าน"
    criteria_advance = "ผ่าน" if decision["advancement_pass"] else "ไม่ผ่าน"
    short_hash = verification["freeze_verification"]["protocol_sha256"][:16]
    evidence_caption = caption("หลักฐาน: " + data_path("summary.json") + " และ " + data_path("decision.json"))
    page1 = f"""
    <p class="eyebrow">ADAPTIVE NERVOUS SYSTEM / RESEARCH NOTE 02</p>
    <h1 class="cover">ประเมินกำลังการทำงาน<br>ก่อนย้ายทรัพยากร</h1>
    <p class="subtitle">ผลทดลอง Capacity-Aware Decision Gate v0.2.0<br>25 พฤศจิกายน 2026 | CPU simulation | งบทรัพยากรคงที่ 207 หน่วย</p>
    <table class="hero"><tr><td style="width:55%"><p class="big">{pct(main["reduction_pct"])}</p>
    <p class="label">งานค้างเฉลี่ยลดลงเทียบกับ Static<br>95% CI: {interval(main["reduction_pct_ci95"])}%</p></td>
    <td><p class="big">{pct(guard["reduction_pct"])}</p>
    <p class="label">ลดลงเทียบกับ Gate รุ่นก่อน<br>95% CI: {interval(guard["reduction_pct_ci95"])}%</p></td></tr></table>
    <p>ตัวเลือกใหม่ <b>capacity_aware</b> ลดงานค้างเฉลี่ยในชุด validation
    จาก {num(static["mean_backlog"])} เป็น <b>{num(current["mean_backlog"])}</b> หน่วย
    ผลนี้มาจาก {summary["scenarios"]} สถานการณ์ที่ใช้ seed แยกจากการพัฒนา
    เมื่อจับคู่กับ Static พบชนะ {main["wins"]} เสมอ {main["ties"]} และแพ้ {main["losses"]} สถานการณ์</p>
    <div class="notice"><b>เกณฑ์หลัก: {criteria_primary} | เกณฑ์ไปต่อ: {criteria_advance}</b><br>
    ขอบล่างของช่วงความเชื่อมั่นของผลดีเป็นบวก ทั้งการเปรียบเทียบหลักกับ Static
    และการเปรียบเทียบกับ Static / Gate รุ่นก่อนแยกในข้อมูลทั้งสองกลุ่ม
    พร้อมผ่านข้อกำหนดทรัพยากรและกรณีภาระต่ำ</div>
    <h2>ผลยังคงเป็นบวกเมื่อแยกแหล่งข้อมูล</h2>
    {table(["กลุ่ม / วิธีที่เทียบ", "กรณี", "ลดงานค้าง", "95% CI (%)"], source_rows)}
    <p class="small"><b>ข้อสรุปที่รองรับ:</b> ต้นแบบมีเหตุผลพอสำหรับขยายการทดลองและอ่าน telemetry
    เพื่อประเมินกับสภาพงานจริง ยังไม่ใช่หลักฐานอนุมัติให้ย้ายทรัพยากรในระบบที่ใช้งานจริง
    กลุ่มงานพุ่งอย่างเดียวกลับแย่กว่า Static และมี {main["losses"]} กรณีที่แพ้ในชุด validation</p>
    {evidence_caption}
    """

    policy_rows = []
    for p in POLICIES:
        v = summary["overall"][p]
        policy_rows.append([NAMES[p], num(v["mean_backlog"]), num(v["peak_backlog"]),
                            num(v["end_backlog"]), num(v["moves"])])
    page2 = f"""
    <p class="eyebrow">01 / COMPARISON</p>
    <h1>เปรียบเทียบครบทั้ง 6 วิธี</h1>
    <p>ทุกวิธีเริ่มที่ 69 / 69 / 69 ใช้งานเข้าและกำลังการผลิตจริงของสถานการณ์เดียวกัน
    วิธีใหม่ทั้งสองแบบตรวจข้อเสนอด้วยแบบจำลอง ขณะที่ <b>capacity_aware</b>
    เปลี่ยนวิธีเลือกข้อเสนอด้วย จึงต้องแยกผลสองอย่างนี้ออกจากกัน</p>
    <div class="compact">{table(["วิธี", "งานค้างเฉลี่ย", "ยอดค้างสูงสุด*", "งานค้างท้าย", "ครั้งที่ย้าย"],
        policy_rows, ["", "", "", "", "", "highlight"])}</div>
    {caption("* ยอดค้างสูงสุดคือค่าเฉลี่ยของค่าสูงสุดในแต่ละ episode; ทุกคอลัมน์เฉลี่ย seed ของ ABC ภายในสถานการณ์ก่อนแล้วจึงเฉลี่ยสถานการณ์")}
    <img src="figures/policy_mean_backlog.png" style="width:100%">
    {caption("กราฟ: ค่า mean_backlog จาก " + data_path("summary.json") + "; ยิ่งน้อยยิ่งดี")}
    <h2>การกรองข้อเสนอช่วยได้ แต่ตัวเลือกใหม่ช่วยเพิ่มอีก</h2>
    <p><b>abc_capacity</b> รับข้อเสนอครั้งละหนึ่งจาก ABC รุ่นเดิมและมีสิทธิ์เพียงยอมรับหรือยับยั้ง
    ลดงานค้างเทียบ Static ได้ {pct(abc_only["reduction_pct"])}
    และย้ายเฉลี่ย {num(summary["overall"]["abc_capacity"]["moves"])} ครั้ง
    ส่วน <b>capacity_aware</b> จัดอันดับทั้ง catalog เดิม เลือกการย้ายได้ต่างจาก ABC
    ลดงานค้างเพิ่มอีก {pct(summary["capacity_aware_vs"]["abc_capacity"]["reduction_pct"])}
    เทียบกับแบบยับยั้งอย่างเดียว โดยย้ายเฉลี่ย {num(current["moves"])} ครั้ง</p>
    <p class="small muted">ทั้งสองแบบเพิ่มเงื่อนไขสำรองอย่างน้อย 10 หน่วยต่อขั้น
    ขณะที่วิธีฐานใช้ข้อจำกัดเดิมว่าไม่ติดลบ จึงเป็นการเทียบแพ็กเกจการควบคุมที่ต่างกัน
    แม้ใช้งบรวมและชุดการย้ายเดียวกัน ผลดีของตัวเลือกใหม่ไม่ควรถูกอ้างว่าเป็นความสามารถของ ABC รุ่นเดิม</p>
    """

    family_rows = []
    family_classes = []
    for g, values in summary["by_group"].items():
        c = values["comparisons"]["capacity_aware"]["static"]
        change = "ทั้งคู่ 0" if c["reduction_pct"] is None else pct(c["reduction_pct"])
        family_rows.append([GROUP_NAMES[g], num(values["overall"]["static"]["mean_backlog"], 1),
                           num(values["overall"]["capacity_aware"]["mean_backlog"], 1), change])
        family_classes.append("warning" if c["reduction_pct"] is not None and c["reduction_pct"] < 0 else "")
    surge = summary["by_group"]["surge_only"]["comparisons"]["capacity_aware"]["static"]
    pulse = summary["by_group"]["generated_pulsed_short_fault"]["comparisons"]["capacity_aware"]["static"]
    switching = summary["by_group"]["generated_switching_fault"]["overall"]
    net_family_improvement = sum(v["overall"]["static"]["mean_backlog"] - v["overall"]["capacity_aware"]["mean_backlog"]
                                 for v in summary["by_group"].values())
    sustained = summary["by_group"]["generated_sustained_processing_fault"]["overall"]
    sustained_share = 100 * (sustained["static"]["mean_backlog"] - sustained["capacity_aware"]["mean_backlog"]) / net_family_improvement
    page3 = f"""
    <p class="eyebrow">02 / WHERE IT WORKS, WHERE IT FAILS</p>
    <h1>ผลรายกลุ่มและจุดที่ยังพลาด</h1>
    <p class="small">แต่ละแถวมี 16 สถานการณ์ ค่าเปอร์เซ็นต์เป็นบวกหมายถึงงานค้างลดลงเมื่อเทียบ Static
    ตัวเลขรวมไม่แทนผลของทุกสภาพแวดล้อม</p>
    <div class="compact">{table(["กลุ่มสถานการณ์", "Static", "ตัวเลือกใหม่", "ลดงานค้าง"], family_rows, family_classes)}</div>
    <img src="figures/family_improvement.png" style="width:100%">
    {caption("แหล่งข้อมูล: by_group ใน " + data_path("summary.json") + "; ภาระต่ำไม่คำนวณเปอร์เซ็นต์เพราะฐานเป็นศูนย์")}
    <div class="warn small"><b>กรณีที่ต้องรักษาไว้ในการทดสอบรุ่นต่อไป:</b>
    งานพุ่งอย่างเดียวมีงานค้างเฉลี่ยเพิ่ม {abs(surge["reduction_pct"]):.2f}% และแพ้ {surge["losses"]}/16 กรณี
    ส่วนการพุ่ง/ขัดข้องสั้นชนะ {pulse["wins"]}/16 แต่ยังแพ้ {pulse["losses"]}/16
    แม้ค่าเฉลี่ยทั้งกลุ่มดีขึ้น {pct(pulse["reduction_pct"])}</div>
    <p class="footnote">ผลดีสุทธิ {sustained_share:.1f}% มาจากกลุ่มช้าต่อเนื่องที่สร้างใหม่เพียงกลุ่มเดียว
    จึงควรดูน้ำหนักและลักษณะงานของระบบเป้าหมายก่อนใช้ผลรวมตัดสินใจ</p>
    """

    page4 = f"""
    <p class="eyebrow">03 / DECISION MECHANISM</p>
    <h1>Gate ตัดสินใจจากอะไร</h1>
    <p>ตัวควบคุมเห็นงานเข้าและคิวปัจจุบัน พร้อมประวัติการไหลของงานและทรัพยากรที่จัดสรรแล้ว
    <b>ไม่เห็นกำลังการผลิตจริงของนาทีปัจจุบันหรือข้อมูลอนาคต</b>
    ภายในแต่ละนาที แบบจำลองส่งงานผ่านคิวทั้งสามขั้นตามลำดับ</p>
    <div class="step"><strong>1. แยกกำลังการทำงานออกจากภาระงาน</strong><br>
    เมื่อยังมีคิวเหลือหลังทำงาน อัตรา flow / resource เป็นหลักฐานว่าขั้นนั้นใช้กำลังเต็ม
    ข้อมูลแรกอัปเดตเต็มค่า หลังจากนั้นให้น้ำหนักข้อมูลใหม่ 0.7</div>
    <div class="step"><strong>2. รับมือข้อมูลที่เห็นไม่ครบ</strong><br>
    เมื่อทำงานหมดคิว flow / resource เป็นเพียงขอบล่างของกำลังการทำงาน
    การดึงค่าประมาณกลับหา nominal 200 ทีละ 10% เป็น <b>สมมติฐานล่วงหน้า</b>
    ไม่ใช่การวัดว่าระบบฟื้นแล้ว</div>
    <div class="step"><strong>3. มองล่วงหน้า 8 นาที ครอบคลุมช่วงห้ามย้ายซ้ำ</strong><br>
    ใช้ค่าเฉลี่ยงานเข้าจาก 3 นาทีล่าสุด แล้วทดสอบงานเข้า 0.8 / 1.0 / 1.2 เท่า
    ร่วมกับอัตราบริการสามแบบ: 0.85 เท่าค่าประมาณ, ค่าประมาณปัจจุบัน
    และค่อย ๆ กลับไป nominal จึงได้ <b>9 สถานการณ์คาดการณ์</b></div>
    <div class="step"><strong>4. ประเมินผลทั้งสายงาน</strong><br>
    คะแนน = งานค้างเฉลี่ยในช่วงคาดการณ์ + 0.25 เท่าของงานค้างเมื่อจบช่วง
    การโยกกำลังไปขั้นหนึ่งจึงถูกประเมินพร้อมผลกระทบต่อขั้นที่เสียทรัพยากร</div>
    <div class="step"><strong>5. ย้ายเมื่อผ่านเกณฑ์ผลดีที่ชัดเจน</strong><br>
    ผลดีเฉลี่ยที่คาดต้องมากกว่า max(10 หน่วยงาน, 2% ของคะแนนการอยู่ที่เดิม)
    และต้องไม่มีหนึ่งในเก้าแบบจำลองทำนายว่าแย่กว่าการอยู่ที่เดิม หากเสมอให้ถือทรัพยากรไว้</div>
    <div class="step"><strong>6. รักษาข้อจำกัดและแยกสองโหมด</strong><br>
    warmup 5 นาที, dwell 8 นาที, งบ 207 และสำรองอย่างน้อย 10 หน่วยต่อขั้น
    โหมด ABC ใช้สำเนาชั่วคราวและยืนยัน state เฉพาะข้อเสนอที่ผ่าน
    โหมด catalog เลือกเองจากการย้ายเดิมทั้งหมดที่ทำได้</div>
    <div class="warn small"><b>การไม่แพ้ใน 9 แบบจำลองไม่ใช่หลักประกันความปลอดภัย</b>
    แบบจำลองไม่ได้ครอบคลุมเหตุขัดข้องทุกชนิด และอาจประเมินกำลังหรือเวลาฟื้นตัวผิดได้
    แนวคิดนี้สัมพันธ์กับการตัดสินใจด้วยแบบจำลองและการมองล่วงหน้า [1]
    แต่ต้นแบบนี้ไม่มีบทพิสูจน์เสถียรภาพหรือความเหมาะที่สุด</div>
    {caption("หลักฐานการทำงาน: capacity_policy.py และ capacity_protocol.json; สมการและค่าตั้งต้นถูกตรึงก่อน validation")}
    """

    checks = verification["independent_trace_replay"]
    decision_replay = audit["checks"]["causal_decision_replay_from_observations_only"]
    rerun_files = len(rerun["files"])
    dev_criterion = development["capacity_aware_vs"]["static"]["reduction_pct"]
    study_rows = [
        ["Development", "300-303", str(development["scenarios"]), num(development["episodes"], 0), num(development["minutes"], 0)],
        ["Validation", "1000-1015", str(summary["scenarios"]), num(summary["episodes"], 0), num(summary["minutes"], 0)],
        ["รวมรุ่น v0.2.0", "", str(development["scenarios"] + summary["scenarios"]),
         num(development["episodes"] + summary["episodes"], 0), num(development["minutes"] + summary["minutes"], 0)],
    ]
    page5 = f"""
    <p class="eyebrow">04 / EXPERIMENT AND VERIFICATION</p>
    <h1>ออกแบบและตรวจสอบการทดลอง</h1>
    <p><b>5 กลุ่มเดิม</b> ใช้ Flash Sale CSV และตัวสร้างเดิม 60 นาทีต่อกรณี
    <b>6 กลุ่มใหม่</b> สร้างงานเข้าแยกจาก CSV ครอบคลุมภาระต่ำ คอขวดต่อเนื่อง/สลับจุด
    การขัดข้องสั้น การฟื้นตัวไม่สมมาตร และภาระเกินกำลัง 96 นาทีต่อกรณี</p>
    {table(["ชุด", "seed", "สถานการณ์", "episode", "นาทีจำลอง"], study_rows)}
    <p class="small">หนึ่งสถานการณ์มี 12 episode: วิธี deterministic 3 วิธีอย่างละหนึ่ง
    และ ABC อีก 3 แบบอย่างละ 3 seed (11, 29, 47)
    จำนวนนี้ไม่นับ unit tests, smoke run และการตรวจผลซ้ำ</p>
    <h2>เปรียบเทียบแบบจับคู่และตรึงก่อนเปิดผล</h2>
    <p>เฉลี่ย seed ของนโยบายภายในแต่ละกรณีก่อน แล้วให้น้ำหนักกรณีเท่ากัน
    bootstrap แบบจับคู่ 2,000 ครั้งสุ่มกรณีภายในแต่ละครอบครัว เพื่อสร้างช่วง 95%
    ช่วงดังกล่าวสะท้อนความแปรผันของชุดจำลองนี้ ไม่รวมความไม่แน่นอนจากการเลือกแบบจำลอง
    และไม่มีการปรับชดเชยการเปรียบเทียบรองหลายรายการ</p>
    <p>Development ลดงานค้างเฉลี่ยเทียบ Static ได้ {pct(dev_criterion)}
    แต่ยังมีข้อเสียในงานค้างท้ายและกลุ่ม surge-only จึงบันทึกข้อจำกัดไว้
    ไม่มีการปรับพารามิเตอร์ตามผล validation
    การตรึง hash เป็นวินัยการทดลองภายใน ไม่ใช่การลงทะเบียนงานวิจัยกับหน่วยงานภายนอก</p>
    <h2>หลักฐานความถูกต้องของการรัน</h2>
    <ul class="small">
      <li><b>{unit_tests} tests ผ่าน</b> ครอบคลุมต้นแบบเดิมและ Gate ใหม่ รวมกรณีคิวหมด,
      อัตราบริการที่ถูกจำกัดด้วยปริมาณงาน, ข้อมูลอนาคต และการปฏิเสธข้อเสนอ</li>
      <li>ตรวจสมดุลงาน/ทรัพยากรทุกนาที และตรวจ summary ทั้ง {num(summary["episodes"], 0)} episode
      ความคลาดเคลื่อนสมดุลงานสูงสุด {summary["max_mass_error"]:.3g};
      ความคลาดเคลื่อนงบทรัพยากรสูงสุด {summary["max_budget_error"]:.0f}</li>
      <li>เล่นซ้ำด้วยเลขคณิตอิสระ <b>{checks["traces"]} traces / {num(checks["minutes"], 0)} นาที</b>
      จากกรณีตัวแทนที่เลือกไว้ก่อนผล ไม่ใช่การเล่นซ้ำอิสระทุก episode</li>
      <li>Neuron Flow รันสำเร็จ 3 workers; inject ข้อผิดพลาดชั่วคราวให้ abc_capacity
      แล้วสำเร็จครั้งที่ 2; เปิด workspace ซ้ำและส่ง job เดิมไม่เพิ่ม attempt หรือเปลี่ยน receipt</li>
      <li>เมื่อจงใจเพิ่มยอด completed ผิด 1 หน่วย ตัวตรวจปฏิเสธผลและบล็อกขั้นที่พึ่งพา
      ขณะที่สาขาอิสระทำงานสำเร็จ; เป็นการทดสอบ exception และผลลัพธ์ ไม่ใช่เครื่องล่มจริง</li>
      <li>การทบทวนอิสระเพิ่มอีก {decision_replay["traces"]} traces ตรวจการตัดสินใจและหลักฐาน {num(decision_replay["decisions"], 0)} นาทีจากข้อมูลที่สังเกตได้
      การรัน validation ใหม่ให้ไฟล์ผลสำคัญทั้ง {rerun_files} ไฟล์เหมือนเดิมทุก byte
      การรันซ้ำนี้ไม่นับเป็นหลักฐานอิสระหรือเพิ่มจำนวนการทดลองในตาราง</li>
    </ul>
    {caption("หลักฐาน: " + data_path("verification.json") + ", capacity_results/unit_tests.log, capacity_results/independent_review.json, capacity_results/reproducibility.json และ DEVELOPMENT_NOTES.md")}
    <p class="footnote">Protocol SHA-256: <span class="file">{short_hash}...</span>
    | Python {verification["python"]} | NumPy {verification["numpy"]}</p>
    """

    dev_end_static = development["overall"]["static"]["end_backlog"]
    dev_end_new = development["overall"]["capacity_aware"]["end_backlog"]
    dev_end_worse = 100 * (dev_end_new-dev_end_static) / dev_end_static
    validation_end_reduction = 100 * (static["end_backlog"] - current["end_backlog"]) / static["end_backlog"]
    terminal_regressions = [c for c in summary["case_results"]
                            if c["capacity_aware"]["end_backlog"] > c["static"]["end_backlog"] + 1e-6]
    worst_terminal = max(summary["case_results"],
                         key=lambda c:c["capacity_aware"]["end_backlog"]-c["static"]["end_backlog"])
    switching_terminal = max((c for c in summary["case_results"] if c["group"] == "generated_switching_fault"),
                             key=lambda c:c["capacity_aware"]["end_backlog"]-c["static"]["end_backlog"])
    throughput_change = 100 * (current["completed"]-static["completed"])/static["completed"]
    page6 = f"""
    <p class="eyebrow">05 / LIMITS AND REUSE</p>
    <h1>ใช้ผลนี้ต่ออย่างไร</h1>
    <h2>สิ่งที่ยังต้องพิสูจน์</h2>
    <p><b>ค่าเฉลี่ยที่ดีขึ้นไม่ได้แปลว่างานค้างท้ายดีทุกกรณี:</b>
    validation มีงานค้างท้ายเฉลี่ย {num(current["end_backlog"])} เทียบ Static {num(static["end_backlog"])}
    หรือลดลง {validation_end_reduction:.2f}% ในเชิงพรรณนา
    แต่ยังมี <b>{len(terminal_regressions)}/{summary["scenarios"]} กรณี</b> ที่งานค้างท้ายมากกว่า Static
    development เคยแย่กว่า Static {dev_end_worse:.2f}% ในตัวชี้วัดนี้</p>
    <div class="warn small"><b>สองกรณีที่ควรตามต่อ:</b>
    ภาระเกินกำลัง seed {worst_terminal["scenario_seed"]} งานค้างท้ายเพิ่มจาก {num(worst_terminal["static"]["end_backlog"])}
    เป็น {num(worst_terminal["capacity_aware"]["end_backlog"])} หน่วย;
    คอขวดสลับ seed {switching_terminal["scenario_seed"]} เพิ่มจาก 0 เป็น {num(switching_terminal["capacity_aware"]["end_backlog"])} หน่วย
    <br><b>{pct(main["reduction_pct"])} เป็นการลดงานค้างเฉลี่ย ไม่ใช่ throughput เพิ่ม {pct(main["reduction_pct"])}</b>
    งานที่เสร็จเฉลี่ยเพิ่มเพียง {throughput_change:.3f}% เมื่อเทียบ Static</div>
    <p>ยังไม่มีค่าใช้จ่ายในการย้ายทรัพยากร คิวที่เต็มจนทิ้งงาน ข้อผิดพลาด HTTP
    หรือ latency รายคำขอ แบบจำลองให้หนึ่งหน่วยงานผ่านหลายขั้นในนาทีเดียวได้
    และอัตรา nominal 200 เป็นสมมติฐาน การทดสอบทั้งหมดใช้ข้อมูลสังเคราะห์
    ที่ยังแชร์กลไกคิวเดียวกัน จึงไม่ใช่หลักฐานจากลูกค้าหรือบริการจริง</p>
    <div class="notice"><b>ก้าวต่อไปที่ผลรองรับ:</b> เก็บการทดสอบ surge-only และการสลับคอขวดไว้
    แล้วประเมินแบบอ่าน telemetry ก่อนตัดสินใจจริง เพิ่มต้นทุนการย้าย,
    ความหน่วงการสังเกต และรูปแบบการฟื้นตัวที่ไม่อยู่ในเก้าแบบจำลอง
    การเปลี่ยนนโยบายหลังจากนี้ควรเป็นรุ่นใหม่พร้อม validation ชุดใหม่</div>
    <h2>รันซ้ำจากแพ็กเกจ</h2>
    <p class="small">แตก <b>Adaptive_Nervous_System_v020.zip</b> แล้วเข้าโฟลเดอร์โครงการ
    ใช้โฟลเดอร์ผลลัพธ์ใหม่ทุกครั้งที่ต้องการทดลองใหม่</p>
    <pre>python -m pip install -r requirements.txt
python -m unittest -v test_lab test_capacity
python run_capacity.py --split validation --out fresh_capacity_results</pre>
    <p class="small">บนโทรศัพท์เปิด <b>START_COLAB_v020.ipynb</b> ใน Colab แล้วอัปโหลด ZIP
    ใช้ CPU และไม่ต้องมี API key โค้ดเซลล์ตรวจ syntax แล้ว และคำสั่งหลักรันจริงในสภาพแวดล้อมนี้
    ส่วนติดต่อ Colab ยังไม่ได้ทดสอบโดยตรง ไม่ต้องรัน freeze ใหม่เพื่อทำซ้ำชุดเดิม</p>
    <h2>แหล่งอ้างอิงและไฟล์หลักฐาน</h2>
    <p class="footnote"><b>[1]</b> J. B. Rawlings, D. Q. Mayne, M. M. Diehl.
    <i>Model Predictive Control: Theory, Computation, and Design</i>, 2nd paperback edition, 2020.
    ใช้เป็นพื้นฐานแนวคิดการคาดการณ์และตัดสินใจ ไม่ใช่การรับรองนโยบายนี้<br>
    <a href="https://sites.engineering.ucsb.edu/~jbraw/mpc/index.html">https://sites.engineering.ucsb.edu/~jbraw/mpc/index.html</a></p>
    <p class="footnote"><b>[2]</b> ต้นฉบับผู้ใช้: ABC_Causal_Service_0.2.0.zip,
    neuron_flow_v0.1.0 และ load_test_summary_per_minute.csv
    ดูแหล่งที่มาใน <span class="file">provenance.json</span>
    และ hash ใน <span class="file">source_checksums.sha256</span></p>
    <p class="footnote"><b>[3]</b> ผลจริงและรายละเอียด:
    <span class="file">capacity_results/validation/summary.json</span>,
    <span class="file">episodes.csv</span>,
    <span class="file">verification.json</span>,
    <span class="file">decision.json</span> และ <span class="file">capacity_protocol.json</span>
    <br>กราฟและตารางในรายงานสร้างจาก JSON ผลทดลองโดยตรง ไม่มีการใส่ตัวเลขผลสำเร็จล่วงหน้า</p>
    """
    return [page1, page2, page3, page4, page5, page6]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "report")
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    summary = read_json(ROOT / "capacity_results/validation/summary.json")
    development = read_json(ROOT / "capacity_results/development/summary.json")
    verification = read_json(ROOT / "capacity_results/validation/verification.json")
    decision = read_json(ROOT / "capacity_results/validation/decision.json")
    protocol = read_json(ROOT / "capacity_protocol.json")
    audit = read_json(ROOT / "capacity_results/independent_review.json")
    rerun = read_json(ROOT / "capacity_results/reproducibility.json")
    test_log = (ROOT / "capacity_results/unit_tests.log").read_text(encoding="utf-8")
    test_count = re.search(r"Ran (\d+) tests", test_log)
    if not test_count or not re.search(r"\nOK\s*$", test_log):
        raise RuntimeError("A complete passing unit test log is required")
    if verification["status"] != "SUCCEEDED":
        raise RuntimeError("The validation run has not completed successfully")
    if rerun["status"] != "PASS" or not all(item["bytes_identical"] for item in rerun["files"]):
        raise RuntimeError("The reproducibility report has not passed")
    if not audit["checks"]["causal_decision_replay_from_observations_only"]["all_recorded_actions_and_full_evidence_equal"]:
        raise RuntimeError("The observation-only decision replay did not pass")
    digest = hashlib.sha256((ROOT / "capacity_results/validation/summary.json").read_bytes()).hexdigest()
    if digest != verification["summary_sha256"]:
        raise RuntimeError("Summary digest does not match validation receipt")
    prepare_fonts(out)
    make_figures(summary, out)
    page_bodies = pages_for(summary, development, verification, decision, protocol, int(test_count.group(1)), audit, rerun)
    html_pages = "\n".join(f'<section class="page">{body}</section>' for body in page_bodies)
    screen_css = """
    html {background:#E7EDF0;} body {margin:0;}
    section.page {box-sizing:border-box; width:210mm; min-height:297mm;
    background:white; padding:15mm 13mm; margin:9mm auto;}
    @media print {html {background:white;} section.page {margin:0; page-break-after:always;}}
    """
    (out / "ANS_v020_Report_TH.html").write_text(
        '<!doctype html><html lang="th"><head><meta charset="utf-8">'
        '<title>ANS v0.2.0 - รายงานผลการทดลอง</title><style>' + CSS + screen_css +
        '</style></head><body>' + html_pages + '</body></html>', encoding="utf-8")
    archive = fitz.Archive(str(out))
    pdf = fitz.open()
    qa = []
    for page_number, body in enumerate(page_bodies, 1):
        page = pdf.new_page(width=595.276, height=841.89)
        # A fixed content rectangle per page prevents an unnoticed extra page
        # or automatic scale-down. All content must fit at its designed size.
        rect = fitz.Rect(36, 35, 559.276, 793)
        spare, scale = page.insert_htmlbox(rect, body, css=CSS, archive=archive, scale_low=1)
        if spare < 0 or scale != 1:
            raise RuntimeError(f"Page {page_number} overflows: spare={spare}, scale={scale}")
        page.draw_line(fitz.Point(36, 807), fitz.Point(559.276, 807),
                       color=(.84, .89, .92), width=.5)
        footer = f"ANS v0.2.0  |  SYNTHETIC VALIDATION  |  25 NOV 2026"
        page.insert_text(fitz.Point(36, 822), footer, fontsize=7.6, fontname="helv", color=(.38,.47,.54))
        page.insert_text(fitz.Point(536, 822), f"{page_number} / 6", fontsize=7.6, fontname="helv", color=(.38,.47,.54))
        qa.append({"page":page_number,"spare_height_pt":spare,"scale":scale})
    pdf.set_metadata({"title":"ANS v0.2.0 - Capacity-Aware Decision Gate",
                      "author":"Adaptive Nervous System experiment",
                      "subject":"Thai report from synthetic validation results",
                      "keywords":"capacity-aware, queue control, synthetic validation, Thai"})
    target = out / "ANS_v020_Report_TH.pdf"
    pdf.save(target, deflate=True, garbage=4)
    pdf.close()
    reopened = fitz.open(target)
    image_dir = out / "qa_pages"
    image_dir.mkdir(exist_ok=True)
    for number, page in enumerate(reopened, 1):
        page.get_pixmap(matrix=fitz.Matrix(1.5,1.5), alpha=False).save(image_dir / f"page_{number:02}.png")
    text = "\n".join(page.get_text() for page in reopened)
    expected = pct(summary["capacity_aware_vs"]["static"]["reduction_pct"])
    if len(reopened) != 6 or expected not in text:
        raise RuntimeError("PDF text/page verification failed")
    (out / "pdf_layout_checks.json").write_text(json.dumps({
        "pages":len(reopened), "content_boxes":qa, "rendered_pngs":len(reopened),
        "summary_sha256":digest,
        "protocol_sha256":verification["freeze_verification"]["protocol_sha256"],
        "note":"All designed text was inserted at scale 1; rendered pages still require visual review."
    }, indent=2) + "\n")
    reopened.close()
    print(json.dumps({"pdf":str(target),"html":str(out/"ANS_v020_Report_TH.html"),
                      "pages":6,"layout":qa},indent=2))


if __name__ == "__main__":
    main()
