import io
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.schemas.terms import TermsVerificationResult

ITEM_COLUMNS = ["itemNm", "value", "subClaim", "evidence", "page", "article", "reason", "status"]
STATUS_ORDER = ["MATCHED", "PARTIAL_MATCH", "MISMATCH"]
STATUS_LABELS = {"MATCHED": "일치", "PARTIAL_MATCH": "부분 일치", "MISMATCH": "불일치"}
HEADER_FILL_COLOR = "0000A5"
HEADER_FONT_COLOR = "FFFFFF"
STATUS_STYLE = {
    "일치": {"fill": "C6EFCE", "font": "006100"},
    "부분 일치": {"fill": "FFEB9C", "font": "9C6500"},
    "불일치": {"fill": "FFC7CE", "font": "9C0006"},
}
COLUMN_LABELS = {
    "name": "대상명",
    "termNm": "약관명",
    "itemNm": "지식 항목",
    "value": "상품 지식",
    "subClaim": "상품 지식 단위",
    "evidence": "약관 내 해당 문구",
    "page": "약관 내 페이지",
    "article": "조항",
    "reason": "차이 설명",
    "status": "검토 결과",
}
WRAP_COLUMNS = {"evidence", "reason"}
COLUMN_WIDTHS = [22, 22, 22, 50, 8, 16, 40, 14]
MAX_COL = len(COLUMN_WIDTHS)

_THIN_SIDE = Side(style="thin", color="B7B7B7")
BOX_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)

TITLE_FILL = PatternFill(start_color="375623", end_color="375623", fill_type="solid")
TITLE_FONT = Font(bold=True, size=16, color="FFFFFF")
BANNER_FILL = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
BANNER_FONT = Font(bold=True, size=13, color="375623")
STATS_HEADER_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
ZEBRA_FILL = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
CARD_STYLES = {
    "total": {"fill": "EDEDED", "font": "404040"},
    "matched": {"fill": "E2EFDA", "font": "375623"},
    "mismatch": {"fill": "FCE4EC", "font": "9C0006"},
}


def _flatten(result: TermsVerificationResult) -> pd.DataFrame:
    rows = [
        {
            "name": name_result.name,
            "termNm": doc_result.termNm,
            **{col: getattr(item, col) for col in ITEM_COLUMNS},
        }
        for name_result in result.data
        for doc_result in name_result.documents
        for item in doc_result.items
    ]
    return pd.DataFrame(rows, columns=["name", "termNm", *ITEM_COLUMNS])


def _grouped_stats(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[*group_cols, *STATUS_LABELS.values(), "합계"])
    pivot = (
        df.groupby(group_cols, sort=False)["status"]
        .value_counts()
        .unstack(fill_value=0)
        .reindex(columns=STATUS_ORDER, fill_value=0)
    )
    pivot["합계"] = pivot.sum(axis=1)
    pivot = pivot.rename(columns=STATUS_LABELS)
    return pivot.reset_index()


def _build_item_row(item) -> dict:
    row = {col: getattr(item, col) for col in ITEM_COLUMNS}
    row["status"] = STATUS_LABELS.get(row["status"], row["status"])
    return row


def _write_title(ws, row: int) -> int:
    ws.cell(row=row, column=1, value="상품지식-약관 검증 AI 결과 보고서")
    for col_idx in range(1, MAX_COL + 1):
        cell = ws.cell(row=row, column=col_idx)
        cell.fill = TITLE_FILL
        cell.border = BOX_BORDER
    title_cell = ws.cell(row=row, column=1)
    title_cell.font = TITLE_FONT
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=MAX_COL)
    ws.row_dimensions[row].height = 28
    return row + 2


def _write_section_title(ws, row: int, title: str) -> int:
    ws.cell(row=row, column=1, value=title)
    for col_idx in range(1, MAX_COL + 1):
        cell = ws.cell(row=row, column=col_idx)
        cell.fill = BANNER_FILL
        cell.border = BOX_BORDER
    ws.cell(row=row, column=1).font = BANNER_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=MAX_COL)
    return row + 1


def _run_ids(keys: list) -> list[int]:
    """keys가 바로 앞과 같으면 같은 run으로 묶는다. 값 하나만으로는 판단하지 않고, 여러 병합
    대상 컬럼을 하나의 튜플 키로 합쳐서 넘겨야 한다 - 그래야 예를 들어 value가 둘 다 None인
    서로 다른 항목이 같은 값으로 오인되어 잘못 병합되는 걸 막을 수 있다."""
    ids = []
    current_id = 0
    for i, key in enumerate(keys):
        if i > 0 and key != keys[i - 1]:
            current_id += 1
        ids.append(current_id)
    return ids


def _merge_consecutive_rows(ws, col_idx: int, start_row: int, run_ids: list[int]) -> None:
    """run_ids가 연속으로 같은 구간을 세로로 셀병합한다 (claim 분해로 반복되는 값 정리용)."""
    run_start = 0
    n = len(run_ids)
    for i in range(1, n + 1):
        if i == n or run_ids[i] != run_ids[run_start]:
            if i - run_start > 1:
                ws.merge_cells(
                    start_row=start_row + run_start, start_column=col_idx, end_row=start_row + i - 1, end_column=col_idx
                )
                ws.cell(row=start_row + run_start, column=col_idx).alignment = Alignment(vertical="top")
            run_start = i


def _write_table(
    ws,
    df: pd.DataFrame,
    start_row: int,
    title: str | None = None,
    merge_columns: frozenset = frozenset(),
    highlight_header: bool = False,
    status_column: str | None = None,
    bold_columns: frozenset = frozenset(),
) -> int:
    row = start_row
    if title:
        title_cell = ws.cell(row=row, column=1, value=title)
        title_cell.font = Font(bold=True, size=12)
        for col_idx in range(1, MAX_COL + 1):
            ws.cell(row=row, column=col_idx).border = BOX_BORDER
        row += 1

    if df.empty:
        ws.cell(row=row, column=1, value="(데이터 없음)").border = BOX_BORDER
        return row + 2

    header_font = Font(bold=True, color=HEADER_FONT_COLOR) if highlight_header else Font(bold=True)
    header_fill = PatternFill(start_color=HEADER_FILL_COLOR, end_color=HEADER_FILL_COLOR, fill_type="solid")
    for col_idx, col_name in enumerate(df.columns, start=1):
        header = COLUMN_LABELS.get(col_name, str(col_name))
        cell = ws.cell(row=row, column=col_idx, value=header)
        cell.font = header_font
        cell.border = BOX_BORDER
        cell.alignment = Alignment(horizontal="center")
        cell.fill = header_fill if highlight_header else STATS_HEADER_FILL
    row += 1
    data_start_row = row

    for row_idx, (_, record) in enumerate(df.iterrows()):
        zebra = row_idx % 2 == 1
        for col_idx, col_name in enumerate(df.columns, start=1):
            value = record[col_name]
            cell = ws.cell(row=row, column=col_idx, value=None if pd.isna(value) else value)
            cell.border = BOX_BORDER
            if col_name in WRAP_COLUMNS:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col_name == status_column and value in STATUS_STYLE:
                style = STATUS_STYLE[value]
                cell.fill = PatternFill(start_color=style["fill"], end_color=style["fill"], fill_type="solid")
                cell.font = Font(bold=True, color=style["font"])
                cell.alignment = Alignment(horizontal="center")
            elif col_name in bold_columns:
                cell.font = Font(bold=True)
            elif zebra:
                cell.fill = ZEBRA_FILL
        row += 1

    active_merge_cols = [col for col in df.columns if col in merge_columns]
    if active_merge_cols:
        keys = list(zip(*(df[col].tolist() for col in active_merge_cols)))
        run_ids = _run_ids(keys)
        for col_name in active_merge_cols:
            col_idx = df.columns.get_loc(col_name) + 1
            _merge_consecutive_rows(ws, col_idx, data_start_row, run_ids)

    return row + 1


def _write_overview(ws, result: TermsVerificationResult, start_row: int) -> int:
    row = _write_section_title(ws, start_row, "1. 개요")

    names = list(dict.fromkeys(name_result.name for name_result in result.data))
    docs = list(
        dict.fromkeys(
            doc_result.termNm for name_result in result.data for doc_result in name_result.documents
        )
    )

    label_fill = PatternFill(start_color="F7F7F7", end_color="F7F7F7", fill_type="solid")
    for label, value in [
        ("검증 일시", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("대상 상품지식", ", ".join(names)),
        ("매핑약관", ", ".join(docs)),
    ]:
        label_cell = ws.cell(row=row, column=1, value=label)
        label_cell.font = Font(bold=True)
        label_cell.fill = label_fill
        label_cell.border = BOX_BORDER
        ws.cell(row=row, column=2, value=value).border = BOX_BORDER
        for col_idx in range(3, MAX_COL + 1):
            ws.cell(row=row, column=col_idx).border = BOX_BORDER
        row += 1

    return row + 1


def _write_summary_dashboard(ws, df: pd.DataFrame, start_row: int) -> int:
    row = _write_section_title(ws, start_row, "2. 검증 요약 대시보드")

    counts = df["status"].value_counts() if not df.empty else pd.Series(dtype=int)
    matched = int(counts.get("MATCHED", 0))
    partial = int(counts.get("PARTIAL_MATCH", 0))
    mismatch = int(counts.get("MISMATCH", 0))
    total = matched + partial + mismatch

    cards = [
        ("총 검증 항목", f"{total} 건", "total"),
        ("일치 / 부분일치", f"{matched} / {partial}", "matched"),
        ("불일치", f"{mismatch} 건", "mismatch"),
    ]

    label_row, value_row = row, row + 1
    for col_idx, (label, value, style_key) in enumerate(cards, start=1):
        style = CARD_STYLES[style_key]
        fill = PatternFill(start_color=style["fill"], end_color=style["fill"], fill_type="solid")

        label_cell = ws.cell(row=label_row, column=col_idx, value=label)
        label_cell.font = Font(bold=True, size=11, color=style["font"])
        label_cell.fill = fill
        label_cell.border = BOX_BORDER
        label_cell.alignment = Alignment(horizontal="center")

        value_cell = ws.cell(row=value_row, column=col_idx, value=value)
        value_cell.font = Font(bold=True, size=20, color=style["font"])
        value_cell.fill = fill
        value_cell.border = BOX_BORDER
        value_cell.alignment = Alignment(horizontal="center")

    ws.row_dimensions[value_row].height = 32
    for col_idx in range(len(cards) + 1, MAX_COL + 1):
        ws.cell(row=label_row, column=col_idx).border = BOX_BORDER
        ws.cell(row=value_row, column=col_idx).border = BOX_BORDER

    return value_row + 2


def _write_detailed_stats(ws, df: pd.DataFrame, start_row: int) -> int:
    row = _write_section_title(ws, start_row, "3. 상세 통계")
    row = _write_table(ws, _grouped_stats(df, ["name"]), row, "상품명별 통계", bold_columns=frozenset({"name"}))
    row = _write_table(
        ws, _grouped_stats(df, ["name", "termNm"]), row, "문서별 통계", bold_columns=frozenset({"name"})
    )
    return row


def _write_item_detail(ws, result: TermsVerificationResult, start_row: int) -> int:
    row = _write_section_title(ws, start_row, "4. 항목별 상세 비교 결과")
    for name_result in result.data:
        for doc_result in name_result.documents:
            item_df = pd.DataFrame(
                [_build_item_row(item) for item in doc_result.items],
                columns=ITEM_COLUMNS,
            )
            row = _write_table(
                ws,
                item_df,
                row,
                f"{name_result.name} - {doc_result.termNm}",
                merge_columns=frozenset({"itemNm", "value"}),
                highlight_header=True,
                status_column="status",
                bold_columns=frozenset({"itemNm"}),
            )
    return row


def build_report_xlsx(result: TermsVerificationResult) -> bytes:
    """검증 결과를 하나의 시트에 제목 -> 1.개요 -> 2.검증 요약 대시보드 -> 3.상세 통계 ->
    4.항목별 상세 비교 결과 순서로 담은 xlsx를 만든다. 4번은 (name, 문서) 조합별 표를
    상품명 -> 문서 순서로 수직 나열한다.
    """
    df = _flatten(result)

    wb = Workbook()
    ws = wb.active
    ws.title = "약관비교 결과"

    row = 1
    row = _write_title(ws, row)
    row = _write_overview(ws, result, row)
    row = _write_summary_dashboard(ws, df, row)
    row = _write_detailed_stats(ws, df, row)
    _write_item_detail(ws, result, row)

    for col_idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
