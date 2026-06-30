from dataclasses import asdict
from pathlib import Path
from typing import List
import pandas as pd
from src.models.schemas import FinalValidationRecord


def write_outputs(records: List[FinalValidationRecord], output_dir: str):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = [asdict(r) for r in records]
    df = pd.DataFrame(rows)
    csv_path = out / "final_validation_results.csv"
    json_path = out / "final_validation_results.json"
    xlsx_path = out / "smarty_melissa_comparison.xlsx"
    exceptions_path = out / "exceptions.csv"
    df.to_csv(csv_path, index=False)
    df.to_json(json_path, orient="records", indent=2)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Validation Results", index=False)
        exception_df = df[df["validation_status"].isin(["MANUAL_REVIEW", "LOW_CONFIDENCE"])]
        exception_df.to_excel(writer, sheet_name="Exceptions", index=False)
        summary = pd.DataFrame({
            "metric": ["total_records", "validated", "manual_review", "low_confidence"],
            "count": [
                len(df),
                (df["validation_status"] == "VALIDATED").sum(),
                (df["validation_status"] == "MANUAL_REVIEW").sum(),
                (df["validation_status"] == "LOW_CONFIDENCE").sum(),
            ]
        })
        summary.to_excel(writer, sheet_name="Summary", index=False)
    df[df["validation_status"].isin(["MANUAL_REVIEW", "LOW_CONFIDENCE"])].to_csv(exceptions_path, index=False)
    return {"csv": str(csv_path), "json": str(json_path), "xlsx": str(xlsx_path), "exceptions": str(exceptions_path)}
