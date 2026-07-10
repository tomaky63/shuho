"""content/reports/ のJSONから静的サイトを dist/ に生成する。

dist/index.html            最新号
dist/reports/<id>.html     アーカイブ
dist/style.css             共通スタイル
"""

import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    reports_dir = REPO / "content" / "reports"
    reports = sorted(reports_dir.glob("*.json"), key=lambda p: p.stem, reverse=True)
    if not reports:
        raise SystemExit("content/reports/ にレポートがない。先に compute_scores.py を実行すること")

    env = Environment(
        loader=FileSystemLoader(REPO / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")

    dist = REPO / "dist"
    (dist / "reports").mkdir(parents=True, exist_ok=True)
    (dist / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copyfile(REPO / "templates" / "style.css", dist / "style.css")

    archive = [p.stem for p in reports]
    for i, path in enumerate(reports):
        report = json.loads(path.read_text(encoding="utf-8-sig"))
        page = template.render(r=report, archive=archive, rel_root="../")
        (dist / "reports" / f"{report['id']}.html").write_text(page, encoding="utf-8")
        if i == 0:
            index = template.render(r=report, archive=archive, rel_root="")
            (dist / "index.html").write_text(index, encoding="utf-8")

    print(f"生成: {len(reports)}号 → {dist}")


if __name__ == "__main__":
    main()
