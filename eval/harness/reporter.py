from __future__ import annotations

import csv
import json
from pathlib import Path

from eval.harness.metrics import QuestionResult

CSV_COLUMNS = [
    "id",
    "category",
    "answerable",
    "question",
    "predicted_answer",
    "ground_truth",
    "refusal_match",
    "over_refusal",
    "semantic_score",
    "semantic_pass",
    "retrieval_hit",
    "article_hit",
    "confidence_metric",
    "latency_seconds",
    "error",
    "sources_json",
]


def write_report(results: list[QuestionResult], summary: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    with (output_dir / "details.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, delimiter=";")
        writer.writeheader()
        for result in results:
            row = {k: v for k, v in result.as_dict().items() if k in CSV_COLUMNS}
            row["sources_json"] = json.dumps(
                [
                    {k: s.get(k) for k in ("index", "filename", "page", "section", "distance", "cited")}
                    for s in result.sources
                ],
                ensure_ascii=False,
            )
            writer.writerow(row)
    return output_dir


def format_console(summary: dict) -> str:
    g = summary["results"]["global"]

    def pct(value):
        return "—" if value is None else f"{value * 100:.0f} %"

    lines = [
        f"Jeu : {summary['dataset']} (v{summary['dataset_version']}) — modèle {summary['model']}",
        f"Questions : {g['count']}  |  réussite globale : {pct(g['pass_rate'])}  |  erreurs : {g['errors']}",
        f"Refus corrects sur pièges : {pct(g.get('refusal_accuracy'))}",
        f"Refus à tort (questions répondables) : {pct(g.get('over_refusal_rate'))}",
        f"Score sémantique moyen : {g.get('semantic_mean')}  |  taux > seuil : {pct(g.get('semantic_pass_rate'))}",
        f"Bonne source récupérée : {pct(g.get('retrieval_hit_rate'))}  |  bon article cité : {pct(g.get('article_hit_rate'))}",
        f"Latence moyenne : {g.get('latency_mean_seconds')} s",
        "",
        "Par catégorie :",
    ]
    for name, block in summary["results"]["by_category"].items():
        lines.append(f"  {name:<24} n={block['count']:<3} réussite {pct(block['pass_rate'])}")
    cal = summary["results"]["calibration"]
    lines.append("")
    lines.append(
        "Calibration de la pertinence : "
        f"correct={cal.get('confidence_mean_when_correct')} / faux={cal.get('confidence_mean_when_wrong')}"
    )
    return "\n".join(lines)
