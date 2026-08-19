#!/usr/bin/env python3
"""notebook 靜態檢查:語法 + 跨格名稱順序。

**為什麼需要這支:** 這台開發機沒有 GPU,notebook 的 GPU 路徑一格都跑不了,
所以只能靠使用者在 Colab 上回報錯誤才知道壞掉。已經這樣浪費過四輪:
torchvision 升級、缺 noise_src、numpy 被 numba 帶壞、`CHUNK_SEC` 在定義前就被讀。
**最後那個是純靜態問題,這支腳本抓得到——所以改 notebook 後一定要跑一次。**

抓得到:語法錯、把變數用在定義它的那一格之前。
抓不到:執行期的一切(記憶體、網路、套件相容)。

用法:
    python3 notebooks/check_notebooks.py            # 檢查全部
    python3 notebooks/check_notebooks.py a.ipynb    # 只檢查指定的
"""
import ast
import builtins
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def cell_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """→ (這格讀到的名字, 這格定義的名字)"""
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    made: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            made.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            made.add(n.name)
            # 函式參數與 except 的別名不是全域,但也不該被當成「跨格缺失」
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = n.args
                made |= {x.arg for x in a.args + a.posonlyargs + a.kwonlyargs}
                for x in (a.vararg, a.kwarg):
                    if x:
                        made.add(x.arg)
        elif isinstance(n, ast.Import):
            for a in n.names:
                made.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                made.add(a.asname or a.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            made.add(n.name)
        elif isinstance(n, ast.comprehension):
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    made.add(t.id)
    return used, made


def check(path: Path) -> list[str]:
    nb = json.loads(path.read_text())
    cells = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
    problems: list[str] = []
    defined = set(dir(builtins))
    for i, raw in enumerate(cells):
        # IPython magic 不是合法 Python,註解掉再解析
        src = "\n".join("#" + ln if ln.lstrip().startswith(("%", "!")) else ln
                        for ln in raw.split("\n"))
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            problems.append(f"  code cell #{i}: 語法錯 line {e.lineno}: {e.msg}")
            continue
        used, made = cell_names(tree)
        missing = sorted(used - defined - made)
        if missing:
            problems.append(f"  code cell #{i}: 用到但前面沒定義 → {missing}")
        defined |= made
    return problems


def main() -> None:
    args = sys.argv[1:]
    files = [Path(a) for a in args] if args else sorted(HERE.glob("*.ipynb"))
    bad = 0
    for f in files:
        problems = check(f)
        print(f"{f.name}: {'OK ✅' if not problems else '有問題 ❌'}")
        for p in problems:
            print(p)
        bad += bool(problems)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
