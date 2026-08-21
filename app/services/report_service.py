import io
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from app.schemas.terms import TermsVerificationResult

ITEM_COLUMNS = ["itemNm", "value", "subClaim", "evidence", "page", "article", "reason", "status"]
STATUS_ORDER = ["MATCHED", "PARTIAL_MATCH", "MISMATCH"]
STATUS_LABELS = {"MATCHED": "일치", "PARTIAL_MATCH": "부분 일치", "MISMATCH": "불일치"}
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


def _write_section_title(ws, row: int, title: str) -> int:
    ws.cell(row=row, column=1, value=title).font = Font(bold=True, size=14)
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
    ws, df: pd.DataFrame, start_row: int, title: str | None = None, merge_columns: frozenset = frozenset()
) -> int:
    row = start_row
    if title:
        ws.cell(row=row, column=1, value=title).font = Font(bold=True, size=12)
        row += 1

    if df.empty:
        ws.cell(row=row, column=1, value="(데이터 없음)")
        return row + 2

    for col_idx, col_name in enumerate(df.columns, start=1):
        header = COLUMN_LABELS.get(col_name, str(col_name))
        ws.cell(row=row, column=col_idx, value=header).font = Font(bold=True)
    row += 1
    data_start_row = row

    for _, record in df.iterrows():
        for col_idx, col_name in enumerate(df.columns, start=1):
            value = record[col_name]
            cell = ws.cell(row=row, column=col_idx, value=None if pd.isna(value) else value)
            if col_name in WRAP_COLUMNS:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
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

    for label, value in [
        ("검증 일시", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("대상 상품지식", ", ".join(names)),
        ("매핑약관", ", ".join(docs)),
    ]:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        ws.cell(row=row, column=2, value=value)
        row += 1

    return row + 1


def _write_summary_dashboard(ws, df: pd.DataFrame, start_row: int) -> int:
    row = _write_section_title(ws, start_row, "2. 검증 요약 대시보드")

    counts = df["status"].value_counts() if not df.empty else pd.Series(dtype=int)
    matched = int(counts.get("MATCHED", 0))
    partial = int(counts.get("PARTIAL_MATCH", 0))
    mismatch = int(counts.get("MISMATCH", 0))
    total = matched + partial + mismatch

    headers = ["총 검증 항목", "일치/부분일치", "불일치"]
    values = [f"{total} 건", f"{matched}/{partial}", f"{mismatch} 건"]

    for col_idx, header in enumerate(headers, start=1):
        ws.cell(row=row, column=col_idx, value=header).font = Font(bold=True)
    row += 1
    for col_idx, value in enumerate(values, start=1):
        ws.cell(row=row, column=col_idx, value=value)
    row += 1

    return row + 1


def _write_detailed_stats(ws, df: pd.DataFrame, start_row: int) -> int:
    row = _write_section_title(ws, start_row, "3. 상세 통계")
    row = _write_table(ws, _grouped_stats(df, ["name"]), row, "상품명별 통계")
    row = _write_table(ws, _grouped_stats(df, ["name", "termNm"]), row, "문서별 통계")
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
                ws, item_df, row, f"{name_result.name} - {doc_result.termNm}", merge_columns=frozenset({"itemNm", "value"})
            )
    return row


def build_report_xlsx(result: TermsVerificationResult) -> bytes:
    """검증 결과를 하나의 시트에 1.개요 -> 2.검증 요약 대시보드 -> 3.상세 통계 -> 4.항목별 상세
    비교 결과 순서로 담은 xlsx를 만든다. 4번은 (name, 문서) 조합별 표를 상품명 -> 문서 순서로
    수직 나열한다.
    """
    df = _flatten(result)

    wb = Workbook()
    ws = wb.active
    ws.title = "약관비교 결과"

    row = 1
    row = _write_overview(ws, result, row)
    row = _write_summary_dashboard(ws, df, row)
    row = _write_detailed_stats(ws, df, row)
    _write_item_detail(ws, result, row)

    for col_idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
