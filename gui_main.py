"""GUI 打包入口（用于 PyInstaller onefile）。

说明：
- 不直接用 `tk_gui/main.py` 作为 PyInstaller 入口，避免相对导入报错。
- 源码运行仍推荐：`python -m tk_gui`
"""

from tk_gui.main import main


if __name__ == "__main__":
    main()

